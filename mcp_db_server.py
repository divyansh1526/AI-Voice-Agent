"""
mcp_db_server.py — VoiceBridge MCP Database Server
====================================================
Runs as a SEPARATE background process using the MCP stdio transport.
Exposes MongoDB Atlas collections as callable tools for the Gemini Voice Agent.

HOW IT RUNS:
    main.py spawns this automatically as a subprocess the first time a voice
    session connects. You do NOT need to start it manually.

    To run manually for debugging:
        python mcp_db_server.py

IMPORTANT — user_id parameter:
    Tools that need user_id do NOT declare it in their inputSchema.
    This prevents Gemini from hallucinating a wrong user_id.
    Instead, mcp_client.py always force-injects the real authenticated
    user_id before calling any tool. The user can never spoof this.
"""

import asyncio
import json
import logging
import os
from datetime import datetime

from bson import ObjectId
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pymongo import MongoClient

# ─── Bootstrap ────────────────────────────────────────────────────────────────
load_dotenv()

# CRITICAL: Log to stderr only — stdout must stay clean for the MCP stdio protocol
import sys
logging.basicConfig(
    level=logging.INFO,
    format="[MCP-Server] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger(__name__)

# ─── MongoDB Connection ────────────────────────────────────────────────────────
MONGODB_URL = os.getenv("MONGODB_URL")
DB_NAME     = os.getenv("DB_NAME", "voicebridge")

if not MONGODB_URL:
    raise RuntimeError("MONGODB_URL is not set in .env — cannot start MCP server")

_mongo = MongoClient(MONGODB_URL)
_db    = _mongo[DB_NAME]
logger.info(f"MongoDB Atlas connected | db={DB_NAME}")


# ─── Serialisation helpers ────────────────────────────────────────────────────
def _to_json(obj) -> str:
    """JSON-serialize MongoDB documents, handling ObjectId and datetime."""
    def default(o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, indent=2, default=default)


def _clean_doc(doc: dict) -> dict:
    """Convert _id → id and strip sensitive fields."""
    if not doc:
        return {}
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    # Strip any password-related fields
    for k in ("password_hash", "password"):
        d.pop(k, None)
    return d


def _err(msg: str) -> CallToolResult:
    """Return an error result back to Gemini."""
    logger.warning(f"Tool error: {msg}")
    return CallToolResult(content=[TextContent(type="text", text=f"Error: {msg}")])


# ─── MCP Server ───────────────────────────────────────────────────────────────
server = Server("voicebridge-db-server")


@server.list_tools()
async def list_tools() -> ListToolsResult:
    """
    Declare all tools to the MCP client on startup.

    CRITICAL DESIGN: Tools that operate on the current user's data do NOT
    expose user_id in their inputSchema. This prevents Gemini from trying
    to fill in user_id (which it doesn't know). The real user_id is
    force-injected by mcp_client.py before any tool is executed.
    """
    return ListToolsResult(
        tools=[

            # ── 1. Get current user's profile ─────────────────────────────────
            Tool(
                name="get_user_profile",
                description=(
                    "Retrieve the profile of the currently logged-in user. "
                    "Returns their name, email address, and the date their account was created. "
                    "Call this when the user asks: 'What is my name?', 'What email do I use?', "
                    "'When did I create my account?', or any question about their account details. "
                    "No parameters needed — the user identity is handled automatically."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},   # user_id is injected server-side, not passed by Gemini
                    "required": [],
                },
            ),

            # ── 2. List all agents for current user ───────────────────────────
            Tool(
                name="list_user_agents",
                description=(
                    "List all voice agents the current user has created on this platform. "
                    "Returns each agent's name, voice, source language, target language, "
                    "and creation date. "
                    "Call this when the user asks: 'What agents do I have?', "
                    "'Show me my agents', 'List my voice agents'. "
                    "No parameters needed."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},   # user_id injected server-side
                    "required": [],
                },
            ),

            # ── 3. Count agents for current user ──────────────────────────────
            Tool(
                name="count_user_agents",
                description=(
                    "Count the total number of voice agents the current user has created. "
                    "Returns a single number. "
                    "Call this for questions like: 'How many agents do I have?', "
                    "'How many voice agents have I created?'. "
                    "No parameters needed."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},   # user_id injected server-side
                    "required": [],
                },
            ),

            # ── 4. Get details of a specific agent ────────────────────────────
            Tool(
                name="get_agent_details",
                description=(
                    "Retrieve complete details of one specific voice agent by its ID. "
                    "Returns the agent's name, voice, system instructions, source language, "
                    "target language, and timestamps. "
                    "Use this when the user asks about a specific agent and provides its ID."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "The MongoDB ID of the agent to look up.",
                        }
                    },
                    "required": ["agent_id"],
                },
            ),

            # ── 5. List database collections ──────────────────────────────────
            Tool(
                name="list_collections",
                description=(
                    "List all MongoDB collection names available in the VoiceBridge database. "
                    "Use this to discover what types of data are stored."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),

            # ── 6. Generic read-only query ────────────────────────────────────
            Tool(
                name="query_collection",
                description=(
                    "Perform a read-only query on any MongoDB collection. "
                    "Returns up to 10 matching documents. "
                    "This is a general-purpose tool for any data not covered by the other tools. "
                    "NEVER use this for write, update, or delete operations."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "collection": {
                            "type": "string",
                            "description": "Collection name, e.g. 'users' or 'agents'.",
                        },
                        "filter": {
                            "type": "object",
                            "description": (
                                "MongoDB filter as a JSON object. "
                                "Example: {} returns all documents. "
                                "{\"voice\": \"Puck\"} finds agents with voice=Puck."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max documents to return (1–10, default 5).",
                        },
                    },
                    "required": ["collection", "filter"],
                },
            ),
        ]
    )


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    """
    Dispatch an incoming tool call to the correct MongoDB operation.
    arguments always includes user_id (force-injected by mcp_client.py)
    for tools that operate on the current user's data.
    """
    logger.info(f"▶ Tool called: '{name}' | args={arguments}")

    try:
        if name == "get_user_profile":
            return _get_user_profile(arguments.get("user_id", ""))

        elif name == "list_user_agents":
            return _list_user_agents(arguments.get("user_id", ""))

        elif name == "count_user_agents":
            return _count_user_agents(arguments.get("user_id", ""))

        elif name == "get_agent_details":
            return _get_agent_details(arguments.get("agent_id", ""))

        elif name == "list_collections":
            return _list_collections()

        elif name == "query_collection":
            return _query_collection(
                collection=arguments.get("collection", ""),
                filter_doc=arguments.get("filter", {}),
                limit=int(arguments.get("limit", 5)),
            )

        else:
            return _err(f"Unknown tool: '{name}'")

    except Exception as exc:
        logger.error(f"Unhandled error in tool '{name}': {exc}", exc_info=True)
        return _err(f"Unexpected error running '{name}': {exc}")


# ─── Tool Implementations ─────────────────────────────────────────────────────

def _get_user_profile(user_id: str) -> CallToolResult:
    """
    Fetch user document by ObjectId string.
    user_id is the MongoDB ObjectId hex string (e.g. "67e8abc123...").
    """
    if not user_id:
        return _err("user_id was not provided (injection may have failed)")
    try:
        oid = ObjectId(user_id)
    except Exception:
        return _err(f"Invalid user_id format: '{user_id}' — expected MongoDB ObjectId hex string")

    doc = _db["users"].find_one({"_id": oid})
    if not doc:
        return _err(f"No user found with _id={user_id}")

    safe = _clean_doc(doc)
    logger.info(f"get_user_profile success → name={safe.get('name')}")
    return CallToolResult(content=[TextContent(type="text", text=_to_json(safe))])


def _list_user_agents(user_id: str) -> CallToolResult:
    """
    Find all agents where user_id field matches the given string.
    In agents collection, user_id is stored as a plain string (ObjectId hex).
    """
    if not user_id:
        return _err("user_id was not provided (injection may have failed)")

    cursor = _db["agents"].find({"user_id": user_id})
    agents = []
    for doc in cursor:
        d = _clean_doc(doc)
        agents.append({
            "id":              d.get("id", ""),
            "name":            d.get("name", ""),
            "voice":           d.get("voice", ""),
            "source_language": d.get("source_language", ""),
            "target_language": d.get("target_language", ""),
            "created_at":      d.get("created_at", ""),
        })

    logger.info(f"list_user_agents → found {len(agents)} agents for user_id={user_id}")

    if not agents:
        return CallToolResult(
            content=[TextContent(type="text", text="You have no voice agents created yet.")]
        )

    return CallToolResult(
        content=[TextContent(
            type="text",
            text=f"Found {len(agents)} agent(s):\n{_to_json(agents)}"
        )]
    )


def _count_user_agents(user_id: str) -> CallToolResult:
    """Count agents for this user."""
    if not user_id:
        return _err("user_id was not provided (injection may have failed)")

    count = _db["agents"].count_documents({"user_id": user_id})
    logger.info(f"count_user_agents → {count} agents for user_id={user_id}")
    msg = f"You have {count} voice agent{'s' if count != 1 else ''}."
    return CallToolResult(content=[TextContent(type="text", text=msg)])


def _get_agent_details(agent_id: str) -> CallToolResult:
    """Fetch a specific agent by its ObjectId string."""
    if not agent_id:
        return _err("agent_id is required")
    try:
        oid = ObjectId(agent_id)
    except Exception:
        return _err(f"Invalid agent_id format: '{agent_id}'")

    doc = _db["agents"].find_one({"_id": oid})
    if not doc:
        return _err(f"No agent found with _id={agent_id}")

    return CallToolResult(content=[TextContent(type="text", text=_to_json(_clean_doc(doc)))])


def _list_collections() -> CallToolResult:
    """Return all collection names in the database."""
    names = _db.list_collection_names()
    return CallToolResult(
        content=[TextContent(type="text", text=f"Collections: {', '.join(names)}")]
    )


def _query_collection(collection: str, filter_doc: dict, limit: int = 5) -> CallToolResult:
    """Generic read-only find() with a limit."""
    if not collection:
        return _err("'collection' parameter is required")

    limit = max(1, min(int(limit), 10))
    docs  = [_clean_doc(d) for d in _db[collection].find(filter_doc).limit(limit)]

    if not docs:
        return CallToolResult(
            content=[TextContent(
                type="text",
                text=f"No documents found in '{collection}' matching filter: {filter_doc}"
            )]
        )
    return CallToolResult(content=[TextContent(type="text", text=_to_json(docs))])


# ─── Entry point ──────────────────────────────────────────────────────────────
async def main():
    logger.info("VoiceBridge MCP DB Server starting (stdio transport)…")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
    logger.info("MCP DB Server stopped.")


if __name__ == "__main__":
    asyncio.run(main())
