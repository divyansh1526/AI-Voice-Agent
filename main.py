import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
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
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gemini_live import GeminiLive

# ─── Environment ────────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO)
logging.getLogger("gemini_live").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("MODEL", "gemini-2.0-flash-live-001")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
AGENTS_FILE = DATA_DIR / "agents.json"
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# ─── JSON DB helpers ────────────────────────────────────────────────────────────
def _read(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

def _write(path: Path, data: list):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# ─── Auth helpers ────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

security = HTTPBearer(auto_error=False)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = decode_token(credentials.credentials)
    users = _read(USERS_FILE)
    user = next((u for u in users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def get_current_user_id_from_ws_token(token: str) -> str:
    """Decode JWT from query param for WebSocket connections."""
    user_id = decode_token(token)
    users = _read(USERS_FILE)
    user = next((u for u in users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user_id

# ─── Pydantic models ────────────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class AgentCreate(BaseModel):
    name: str
    instructions: str
    voice: str = "Puck"
    source_language: str = "English"
    target_language: str = "Spanish"

class AgentUpdate(BaseModel):
    name: Optional[str] = None
    instructions: Optional[str] = None
    voice: Optional[str] = None
    source_language: Optional[str] = None
    target_language: Optional[str] = None

# ─── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(title="Gemini Voice Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Auth routes ────────────────────────────────────────────────────────────────
@app.post("/api/auth/signup")
async def signup(body: SignupRequest):
    users = _read(USERS_FILE)
    if any(u["email"].lower() == body.email.lower() for u in users):
        raise HTTPException(status_code=409, detail="Email already registered")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")

    user = {
        "id": str(uuid.uuid4()),
        "name": body.name.strip(),
        "email": body.email.lower().strip(),
        "password": hash_password(body.password),
        "created_at": datetime.utcnow().isoformat(),
    }
    users.append(user)
    _write(USERS_FILE, users)
    token = create_token(user["id"])
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}

@app.post("/api/auth/login")
async def login(body: LoginRequest):
    users = _read(USERS_FILE)
    user = next((u for u in users if u["email"].lower() == body.email.lower()), None)
    if not user or not verify_password(body.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_token(user["id"])
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}

@app.get("/api/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {"id": current_user["id"], "name": current_user["name"], "email": current_user["email"]}

# ─── Agent routes ────────────────────────────────────────────────────────────────
@app.get("/api/agents")
async def list_agents(current_user: dict = Depends(get_current_user)):
    agents = _read(AGENTS_FILE)
    user_agents = [a for a in agents if a["user_id"] == current_user["id"]]
    return user_agents

@app.post("/api/agents", status_code=201)
async def create_agent(body: AgentCreate, current_user: dict = Depends(get_current_user)):
    agents = _read(AGENTS_FILE)
    agent = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "name": body.name.strip(),
        "instructions": body.instructions.strip(),
        "voice": body.voice,
        "source_language": body.source_language,
        "target_language": body.target_language,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    agents.append(agent)
    _write(AGENTS_FILE, agents)
    return agent

@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str, current_user: dict = Depends(get_current_user)):
    agents = _read(AGENTS_FILE)
    agent = next((a for a in agents if a["id"] == agent_id and a["user_id"] == current_user["id"]), None)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate, current_user: dict = Depends(get_current_user)):
    agents = _read(AGENTS_FILE)
    idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id and a["user_id"] == current_user["id"]), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    update_data = body.model_dump(exclude_none=True)
    agents[idx].update(update_data)
    agents[idx]["updated_at"] = datetime.utcnow().isoformat()
    _write(AGENTS_FILE, agents)
    return agents[idx]

@app.delete("/api/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, current_user: dict = Depends(get_current_user)):
    agents = _read(AGENTS_FILE)
    new_agents = [a for a in agents if not (a["id"] == agent_id and a["user_id"] == current_user["id"])]
    if len(new_agents) == len(agents):
        raise HTTPException(status_code=404, detail="Agent not found")
    _write(AGENTS_FILE, new_agents)
    return JSONResponse(status_code=204, content=None)

@app.post("/api/upload-instructions")
async def upload_instructions(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are allowed")
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be valid UTF-8 text")
    # Limit to ~1000 words
    words = text.split()
    if len(words) > 1000:
        text = " ".join(words[:1000])
    return {"text": text, "word_count": len(words)}

# ─── WebSocket — voice session ────────────────────────────────────────────────────
def _build_system_instruction(agent: dict) -> str:
    src = agent.get("source_language", "English")
    tgt = agent.get("target_language", "Spanish")
    custom = agent.get("instructions", "").strip()

    base = (
        f"You are a real-time conversational voice assistant named '{agent['name']}'. "
        f"The user will speak in {src}. "
        f"You MUST ALWAYS respond in {tgt}. "

        f"IMPORTANT RULES: "
        f"- Do NOT translate the user's sentence. "
        f"- Understand the meaning and reply naturally like a human conversation. "
        f"- NEVER repeat or restate the user's input. "
        f"- NEVER output {src}. Only use {tgt}. "
        f"- Keep responses short, natural, and conversational. "
    )

    if src == tgt:
        base = (
            f"You are a helpful voice assistant named '{agent['name']}'. "
            f"Respond naturally and conversationally in {tgt}. "
        )

    if custom:
        base += f"\n\nAdditional instructions: {custom}"

    return base

@app.websocket("/ws/{agent_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    agent_id: str,
    token: str = Query(...),
):
    # Auth
    try:
        user_id = get_current_user_id_from_ws_token(token)
    except HTTPException as e:
        await websocket.close(code=4001, reason=e.detail)
        return

    # Load agent
    agents = _read(AGENTS_FILE)
    agent = next((a for a in agents if a["id"] == agent_id and a["user_id"] == user_id), None)
    if not agent:
        await websocket.close(code=4004, reason="Agent not found")
        return

    await websocket.accept()
    logger.info(f"WebSocket connected for agent '{agent['name']}' (user {user_id})")

    audio_input_queue = asyncio.Queue()
    video_input_queue = asyncio.Queue()
    text_input_queue = asyncio.Queue()

    async def audio_output_callback(data):
        await websocket.send_bytes(data)

    system_instruction = _build_system_instruction(agent)
    voice = agent.get("voice", "Puck")

    gemini_client = GeminiLive(
        api_key=GEMINI_API_KEY,
        model=MODEL,
        input_sample_rate=16000,
        voice_name=voice,
        system_instruction=system_instruction,
    )

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
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.error(f"Error receiving from client: {e}")

    receive_task = asyncio.create_task(receive_from_client())

    async def run_session():
        async for event in gemini_client.start_session(
            audio_input_queue=audio_input_queue,
            video_input_queue=video_input_queue,
            text_input_queue=text_input_queue,
            audio_output_callback=audio_output_callback,
        ):
            if event:
                await websocket.send_json(event)

    try:
        await run_session()
    except Exception as e:
        import traceback
        logger.error(f"Error in Gemini session: {type(e).__name__}: {e}\n{traceback.format_exc()}")
    finally:
        receive_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass

# ─── Static files & SPA catch-all ────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
async def root():
    return FileResponse("frontend/index.html")

@app.get("/{full_path:path}")
async def spa_catch_all(full_path: str):
    """Serve index.html for all non-API routes (SPA routing)."""
    file_path = Path("frontend") / full_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse("frontend/index.html")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="localhost", port=port)
