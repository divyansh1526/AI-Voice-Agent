"""
mcp_client.py — VoiceBridge MCP Client Wrapper
================================================
Spawns mcp_db_server.py as a child subprocess and bridges tool calls
between Gemini Live and MongoDB Atlas via the MCP stdio transport.

KEY BUG FIXES (v2):
────────────────────
1. user_id is FORCE-INJECTED (kwargs[k] = v) — not setdefault().
   setdefault() was silently skipping injection because when a tool
   has no user_id in its schema, the argument dict from Gemini is empty
   but execution path was still wrong if Gemini somehow passed something.

2. The _call closure no longer uses keyword-only args (no leading *).
   gemini_live.py calls tool_func(**args) where args is a plain dict
   from Gemini. The old * signature caused parameter name conflicts.

3. user_id is injected into ALL tools unconditionally (not just those
   that declare it as required), because the schema no longer exposes
   user_id — so Gemini never passes it, and the tool always needs it
   to be injected. The MCP server ignores unknown kwargs.
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from google.genai import types as genai_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


# ─── Singleton ────────────────────────────────────────────────────────────────
_shared: "MCPClient | None" = None
_lock   = asyncio.Lock()


async def get_shared_mcp_client() -> "MCPClient":
    """
    Return the single shared MCPClient, creating + connecting it on first call.
    Safe to call concurrently from multiple async tasks.
    """
    global _shared
    async with _lock:
        if _shared is None or not _shared.is_connected():
            client = MCPClient()
            await client.connect()
            _shared = client
        return _shared


# ─── MCPClient ────────────────────────────────────────────────────────────────
class MCPClient:
    """
    Manages the lifecycle of the mcp_db_server.py subprocess and all
    tool interactions over the MCP stdio transport.
    """

    def __init__(self):
        self._session:        ClientSession | None = None
        self._cm              = None
        self._connected: bool = False

    def is_connected(self) -> bool:
        return self._connected and self._session is not None

    # ── Connection ────────────────────────────────────────────────────────────

    async def connect(self):
        """Spawn mcp_db_server.py and perform the MCP protocol handshake."""
        server_script = Path(__file__).parent / "mcp_db_server.py"
        if not server_script.exists():
            raise FileNotFoundError(f"MCP server script not found: {server_script}")

        params = StdioServerParameters(
            command=sys.executable,   # same Python interpreter as main.py
            args=[str(server_script)],
            env=None,                 # inherit environment → MONGODB_URL reaches subprocess
        )

        logger.info(f"Spawning MCP DB server: {server_script}")

        self._cm = stdio_client(params)
        read, write = await self._cm.__aenter__()

        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

        self._connected = True
        logger.info("✓ MCP Client connected to VoiceBridge DB Server")

    async def disconnect(self):
        """Gracefully shut down session and kill the subprocess."""
        self._connected = False
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None
        logger.info("MCP Client disconnected")

    # ── Tool Discovery ────────────────────────────────────────────────────────

    async def get_raw_tools(self) -> list:
        self._require_connected()
        result = await self._session.list_tools()
        return result.tools

    async def get_gemini_tools(self) -> list[genai_types.Tool]:
        """
        Fetch all MCP tools and convert them into google.genai.types.Tool objects
        that GeminiLive passes to the Gemini Live API.

        NOTE: user_id is NOT included in any tool schema. Gemini never sees it
        as a parameter, so it never tries to fill it in. mcp_client injects it
        transparently into every tool call that needs it.
        """
        raw = await self.get_raw_tools()
        declarations = []

        for tool in raw:
            schema = _json_schema_to_genai(
                tool.inputSchema or {"type": "object", "properties": {}, "required": []}
            )
            declarations.append(
                genai_types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description or "",
                    parameters=schema,
                )
            )

        logger.info(f"Loaded {len(declarations)} MCP tools: {[t.name for t in raw]}")
        return [genai_types.Tool(function_declarations=declarations)]

    async def get_tool_mapping(
        self,
        injected_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Build { tool_name → async callable } for GeminiLive.tool_mapping.

        FIX: injected_context values are ALWAYS written into kwargs with
             kwargs[k] = v (not setdefault). This guarantees the real
             authenticated user_id is always used, even if Gemini somehow
             passes a value for that key.

        FIX: The closure no longer uses keyword-only argument syntax (*),
             because gemini_live.py calls tool_func(**args) with a plain
             dict and keyword-only params would cause TypeError.
        """
        raw = await self.get_raw_tools()
        mapping: dict[str, Any] = {}

        for tool in raw:
            tool_name = tool.name  # captured by value

            # Build the async callable for this specific tool.
            # We use a factory function to avoid the classic Python
            # closure-captures-loop-variable bug.
            mapping[tool_name] = _make_tool_callable(
                client=self,
                name=tool_name,
                injected_context=injected_context or {},
            )

        return mapping

    # ── Tool Execution ────────────────────────────────────────────────────────

    async def execute_tool(self, name: str, arguments: dict) -> str:
        """Call a tool on the MCP server and return the text result."""
        self._require_connected()
        logger.info(f"▶ Executing MCP tool '{name}' | args={arguments}")

        result = await self._session.call_tool(name, arguments)

        parts = [
            block.text
            for block in (result.content or [])
            if hasattr(block, "text") and block.text
        ]
        text = "\n".join(parts) if parts else "No result returned."
        logger.info(f"◀ Tool '{name}' result: {text[:200]}")
        return text

    def _require_connected(self):
        if not self.is_connected():
            raise RuntimeError("MCPClient is not connected. Call connect() first.")


# ─── Tool callable factory ────────────────────────────────────────────────────

def _make_tool_callable(
    client: MCPClient,
    name: str,
    injected_context: dict[str, Any],
):
    """
    Returns an async function that:
    1. Takes **kwargs from Gemini's tool_call args (could be empty {})
    2. FORCE-injects all context values (user_id etc.) into kwargs
    3. Calls execute_tool with the merged arguments

    Using a factory function (not a closure inside a loop) ensures each
    tool gets its own correctly-captured 'name' and 'injected_context'.
    """
    async def _call(**kwargs) -> str:
        # FORCE-inject authenticated context — always overwrite, never setdefault.
        # This is the key fix: Gemini passes {} for tools with no schema params,
        # and we add user_id here so the MCP server always gets the real value.
        merged = dict(kwargs)
        for k, v in injected_context.items():
            merged[k] = v  # ← always overwrite (was setdefault — that was the bug)

        logger.debug(f"Tool '{name}' merged args: {merged}")
        return await client.execute_tool(name, merged)

    return _call


# ─── JSON Schema → GenAI Schema converter ────────────────────────────────────

def _json_schema_to_genai(schema: dict) -> genai_types.Schema:
    """
    Recursively convert a JSON Schema dict → google.genai.types.Schema.
    Handles object, string, integer, number, boolean, array, and nested structures.
    """
    _TYPE_MAP = {
        "STRING":  genai_types.Type.STRING,
        "INTEGER": genai_types.Type.INTEGER,
        "NUMBER":  genai_types.Type.NUMBER,
        "BOOLEAN": genai_types.Type.BOOLEAN,
        "ARRAY":   genai_types.Type.ARRAY,
        "OBJECT":  genai_types.Type.OBJECT,
    }

    raw_type   = str(schema.get("type", "object")).upper()
    genai_type = _TYPE_MAP.get(raw_type, genai_types.Type.STRING)

    properties = None
    if isinstance(schema.get("properties"), dict):
        properties = {
            k: _json_schema_to_genai(v)
            for k, v in schema["properties"].items()
        }

    items = None
    if schema.get("items"):
        items = _json_schema_to_genai(schema["items"])

    return genai_types.Schema(
        type=genai_type,
        description=schema.get("description", ""),
        properties=properties,
        required=schema.get("required") or [],
        items=items,
    )
