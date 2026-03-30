"""
VoiceBridge — FastAPI Backend
MongoDB Atlas persistence via Motor (async driver).
All routes are JWT-protected and user-scoped.
"""

import asyncio
import base64
import json
import logging
import os
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, field_validator

from gemini_live import GeminiLive

# ─── Environment ─────────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO)
logging.getLogger("gemini_live").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
MODEL           = os.getenv("MODEL", "gemini-2.0-flash-live-001")
JWT_SECRET      = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM   = "HS256"
JWT_EXPIRE_HOURS = 72
MONGODB_URL     = os.getenv("MONGODB_URL")
DB_NAME         = os.getenv("DB_NAME", "voicebridge")

# ─── MongoDB client (module-level, shared across requests) ────────────────────────
if not MONGODB_URL:
    raise RuntimeError("MONGODB_URL is not set in .env")

mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(
    MONGODB_URL,
    serverSelectionTimeoutMS=10_000,   # 10s timeout on first connect
    connectTimeoutMS=10_000,
    socketTimeoutMS=30_000,
    tls=True,
    tlsAllowInvalidCertificates=False,
)
db = mongo_client[DB_NAME]

users_col  = db["users"]   # { _id, name, email (unique), password_hash, created_at }
agents_col = db["agents"]  # { _id, user_id, name, instructions, voice, source_language,
                            #   target_language, created_at, updated_at }

# uploads directory (files are transient — not stored in DB)
UPLOADS_DIR = Path("data/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ─── Startup: ensure indexes ──────────────────────────────────────────────────────
async def ensure_indexes():
    """Create indexes on app startup if they do not already exist."""
    await users_col.create_index("email", unique=True)
    await agents_col.create_index("user_id")
    logger.info("MongoDB indexes verified ✓")


# ─── BSON helper ──────────────────────────────────────────────────────────────────
def _doc_to_dict(doc: dict) -> dict:
    """Convert MongoDB document to JSON-serialisable dict.
    Replaces ObjectId _id with string 'id' field."""
    if doc is None:
        return {}
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    # Ensure datetime fields are ISO strings
    for k in ("created_at", "updated_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


def _safe_object_id(value: str) -> ObjectId:
    """Parse a string to ObjectId; raise 404 on invalid format."""
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid ID format")


# ─── Password helpers ─────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ─── JWT helpers ──────────────────────────────────────────────────────────────────
def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.now(tz=timezone.utc),
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> str:
    """Decode JWT and return user_id string, or raise HTTP 401."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        uid = payload.get("sub")
        if not uid:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return uid
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired — please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")


# ─── Auth dependency ──────────────────────────────────────────────────────────────
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """FastAPI dependency — validates Bearer token and returns the user document."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = _decode_token(credentials.credentials)
    oid = _safe_object_id(user_id)
    user = await users_col.find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=401, detail="User account not found")
    return _doc_to_dict(user)


async def get_current_user_id_from_ws_token(token: str) -> str:
    """Validate JWT from WebSocket query-param; return user_id string."""
    user_id = _decode_token(token)
    oid = _safe_object_id(user_id)
    user = await users_col.find_one({"_id": oid}, {"_id": 1})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user_id


# ─── Pydantic request models ──────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    name: str
    email: str
    password: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()

    @field_validator("email")
    @classmethod
    def email_lowercase(cls, v):
        return v.lower().strip()

    @field_validator("password")
    @classmethod
    def password_length(cls, v):
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def email_lowercase(cls, v):
        return v.lower().strip()


class AgentCreate(BaseModel):
    name: str
    instructions: str = ""
    voice: str = "Puck"
    source_language: str = "English"
    target_language: str = "Spanish"

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Agent name cannot be empty")
        return v.strip()


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    instructions: Optional[str] = None
    voice: Optional[str] = None
    source_language: Optional[str] = None
    target_language: Optional[str] = None


# ─── FastAPI App ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="VoiceBridge API",
    description="Gemini-powered speech-to-speech translation agent platform",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    await ensure_indexes()
    logger.info(f"Connected to MongoDB Atlas › database: '{DB_NAME}'")


@app.on_event("shutdown")
async def on_shutdown():
    mongo_client.close()
    logger.info("MongoDB connection closed")


# ─── Health check ─────────────────────────────────────────────────────────────────
@app.get("/api/health", tags=["Health"])
async def health():
    try:
        await mongo_client.admin.command("ping")
        return {"status": "ok", "database": DB_NAME}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unreachable: {e}")


# ─── Auth routes ──────────────────────────────────────────────────────────────────
@app.post("/api/auth/signup", tags=["Auth"])
async def signup(body: SignupRequest):
    # Check duplicate email (unique index also guards this, but give a clean error)
    existing = await users_col.find_one({"email": body.email})
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    now = datetime.now(tz=timezone.utc)
    doc = {
        "name":          body.name,
        "email":         body.email,
        "password_hash": hash_password(body.password),
        "created_at":    now,
    }
    result = await users_col.insert_one(doc)
    user_id = str(result.inserted_id)
    token = create_token(user_id)
    return {
        "token": token,
        "user":  {"id": user_id, "name": body.name, "email": body.email},
    }


@app.post("/api/auth/login", tags=["Auth"])
async def login(body: LoginRequest):
    user = await users_col.find_one({"email": body.email})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user_id = str(user["_id"])
    token = create_token(user_id)
    return {
        "token": token,
        "user":  {"id": user_id, "name": user["name"], "email": user["email"]},
    }


@app.get("/api/auth/me", tags=["Auth"])
async def me(current_user: dict = Depends(get_current_user)):
    return {"id": current_user["id"], "name": current_user["name"], "email": current_user["email"]}


# ─── Agent routes ─────────────────────────────────────────────────────────────────
@app.get("/api/agents", tags=["Agents"])
async def list_agents(current_user: dict = Depends(get_current_user)):
    cursor = agents_col.find({"user_id": current_user["id"]}).sort("created_at", -1)
    agents = [_doc_to_dict(a) async for a in cursor]
    return agents


@app.post("/api/agents", status_code=201, tags=["Agents"])
async def create_agent(body: AgentCreate, current_user: dict = Depends(get_current_user)):
    now = datetime.now(tz=timezone.utc)
    doc = {
        "user_id":         current_user["id"],
        "name":            body.name,
        "instructions":    body.instructions.strip(),
        "voice":           body.voice,
        "source_language": body.source_language,
        "target_language": body.target_language,
        "created_at":      now,
        "updated_at":      now,
    }
    result = await agents_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _doc_to_dict(doc)


@app.get("/api/agents/{agent_id}", tags=["Agents"])
async def get_agent(agent_id: str, current_user: dict = Depends(get_current_user)):
    oid = _safe_object_id(agent_id)
    agent = await agents_col.find_one({"_id": oid, "user_id": current_user["id"]})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _doc_to_dict(agent)


@app.put("/api/agents/{agent_id}", tags=["Agents"])
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    current_user: dict = Depends(get_current_user),
):
    oid = _safe_object_id(agent_id)
    # Verify ownership before mutating
    existing = await agents_col.find_one({"_id": oid, "user_id": current_user["id"]})
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update")

    update_data["updated_at"] = datetime.now(tz=timezone.utc)
    await agents_col.update_one({"_id": oid}, {"$set": update_data})
    updated = await agents_col.find_one({"_id": oid})
    return _doc_to_dict(updated)


@app.delete("/api/agents/{agent_id}", status_code=204, tags=["Agents"])
async def delete_agent(agent_id: str, current_user: dict = Depends(get_current_user)):
    oid = _safe_object_id(agent_id)
    result = await agents_col.delete_one({"_id": oid, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    return JSONResponse(status_code=204, content=None)


# ─── File upload ──────────────────────────────────────────────────────────────────
@app.post("/api/upload-instructions", tags=["Agents"])
async def upload_instructions(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    if not (file.filename or "").endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are accepted")
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be valid UTF-8 text")
    words = text.split()
    if len(words) > 1000:
        text = " ".join(words[:1000])
    return {"text": text, "word_count": min(len(words), 1000)}


# ─── System instruction builder ───────────────────────────────────────────────────
def _build_system_instruction(agent: dict) -> str:
    src    = agent.get("source_language", "English")
    tgt    = agent.get("target_language", "Spanish")
    custom = (agent.get("instructions") or "").strip()

    if src == tgt:
        base = (
            f"You are a helpful voice assistant named '{agent['name']}'. "
            f"Respond naturally and conversationally in {tgt}. "
            f"Keep responses concise and friendly."
        )
    else:
        base = (
            f"You are a real-time conversational voice assistant named '{agent['name']}'. "
            f"The user will speak in {src}. "
            f"You MUST ALWAYS respond in {tgt}. "
            f"IMPORTANT RULES: "
            f"- Do NOT translate the user's sentence word-for-word. "
            f"- Understand the meaning and reply naturally like a human conversation. "
            f"- NEVER output text in {src}. ONLY use {tgt}. "
            f"- Keep responses short, natural, and conversational."
        )

    if custom:
        base += f"\n\nAdditional instructions from the user: {custom}"

    return base


# ─── WebSocket — live voice session ──────────────────────────────────────────────
@app.websocket("/ws/{agent_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    agent_id: str,
    token: str = Query(...),
):
    """
    Authenticated WebSocket endpoint for a live voice session with a specific agent.
    The JWT is passed as a query parameter (?token=...).
    The agent must belong to the authenticated user.
    """
    # ── Auth ─────────────────────────────────────────────────────────────────────
    try:
        user_id = await get_current_user_id_from_ws_token(token)
    except HTTPException as exc:
        logger.warning(f"WS auth failed: {exc.detail}")
        await websocket.close(code=4001, reason=exc.detail)
        return

    # ── Load agent (ownership check) ─────────────────────────────────────────────
    try:
        oid = _safe_object_id(agent_id)
    except HTTPException:
        await websocket.close(code=4004, reason="Invalid agent ID")
        return

    agent_doc = await agents_col.find_one({"_id": oid, "user_id": user_id})
    if not agent_doc:
        logger.warning(f"Agent {agent_id} not found or does not belong to user {user_id}")
        await websocket.close(code=4004, reason="Agent not found")
        return

    agent = _doc_to_dict(agent_doc)
    await websocket.accept()
    logger.info(f"WS session started › agent='{agent['name']}' user={user_id}")

    # ── Queues ────────────────────────────────────────────────────────────────────
    audio_input_queue = asyncio.Queue()
    video_input_queue = asyncio.Queue()
    text_input_queue  = asyncio.Queue()

    async def audio_output_callback(data: bytes):
        await websocket.send_bytes(data)

    # ── Gemini client ─────────────────────────────────────────────────────────────
    system_instruction = _build_system_instruction(agent)
    voice              = agent.get("voice", "Puck")

    gemini_client = GeminiLive(
        api_key=GEMINI_API_KEY,
        model=MODEL,
        input_sample_rate=16000,
        voice_name=voice,
        system_instruction=system_instruction,
    )

    # ── Receive loop (client → server) ────────────────────────────────────────────
    async def receive_from_client():
        try:
            while True:
                message = await websocket.receive()
                if message.get("bytes"):
                    await audio_input_queue.put(message["bytes"])
                elif message.get("text"):
                    text = message["text"]
                    try:
                        payload = json.loads(text)
                        if isinstance(payload, dict) and payload.get("type") == "image":
                            image_data = base64.b64decode(payload["data"])
                            await video_input_queue.put(image_data)
                            continue
                    except json.JSONDecodeError:
                        pass
                    await text_input_queue.put(text)
        except WebSocketDisconnect:
            logger.info(f"WS disconnected › agent={agent['name']}")
        except Exception as exc:
            logger.error(f"WS receive error: {exc}")

    receive_task = asyncio.create_task(receive_from_client())

    # ── Gemini session (server → client) ──────────────────────────────────────────
    async def run_gemini_session():
        async for event in gemini_client.start_session(
            audio_input_queue=audio_input_queue,
            video_input_queue=video_input_queue,
            text_input_queue=text_input_queue,
            audio_output_callback=audio_output_callback,
        ):
            if event:
                await websocket.send_json(event)

    try:
        await run_gemini_session()
    except Exception as exc:
        logger.error(
            f"Gemini session error: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
    finally:
        receive_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info(f"WS session closed › agent={agent['name']}")


# ─── Static files & SPA catch-all ────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


@app.get("/{full_path:path}")
async def spa_catch_all(full_path: str):
    """Serve index.html for all non-API routes (client-side SPA routing)."""
    file_path = Path("frontend") / full_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse("frontend/index.html")


# ─── Entry point ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="localhost", port=port)
