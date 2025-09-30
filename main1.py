from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from uuid import uuid4
from datetime import datetime
from typing import List, Dict, Optional
import redis
import json
from pathlib import Path
import asyncio
from openai import OpenAI
import os

# ---------------- Configuration ----------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CHAT_PREFIX = "chat:"
REDIS_BOOKING_PREFIX = "booking:"
CHAT_DIR = Path("chat_sessions")
CHAT_DIR.mkdir(exist_ok=True)
BOOKING_FILE = Path("bookings.json")

# Initialize OpenAI client with validation
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY not set. Chat endpoint will not work.")
    openai_client = None
else:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Initialize Redis with error handling
try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()  # Test connection
    print(f"✓ Connected to Redis at {REDIS_URL}")
except Exception as e:
    print(f"WARNING: Could not connect to Redis: {e}")
    redis_client = None

app = FastAPI(title="Conversational RAG API")

# ---------------- Models ----------------
class Booking(BaseModel):
    name: str
    email: EmailStr
    date: str
    time: str

class ChatMessage(BaseModel):
    session_id: str
    message: str

# ---------------- Helpers ----------------
def save_booking_file(booking_id: str, data: dict):
    """Save booking to JSON file"""
    try:
        if BOOKING_FILE.exists():
            all_bookings = json.loads(BOOKING_FILE.read_text())
        else:
            all_bookings = {}
        all_bookings[booking_id] = data
        BOOKING_FILE.write_text(json.dumps(all_bookings, indent=2))
    except Exception as e:
        print(f"Error saving booking file: {e}")

def save_chat_file(session_id: str, msg: dict):
    """Save chat message to JSON file"""
    try:
        session_file = CHAT_DIR / f"session_{session_id}.json"
        if session_file.exists():
            session_data = json.loads(session_file.read_text())
        else:
            session_data = []
        session_data.append({"timestamp": datetime.now().isoformat(), **msg})
        session_file.write_text(json.dumps(session_data, indent=2))
    except Exception as e:
        print(f"Error saving chat file: {e}")

async def query_openai_async(prompt: str, chat_history: Optional[List[Dict[str, str]]] = None) -> str:
    """Query OpenAI API asynchronously"""
    if not openai_client:
        raise HTTPException(status_code=500, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    
    chat_history = chat_history or []
    messages = [{"role": "system", "content": "You are a helpful assistant."}]
    
    # Add chat history
    for msg in chat_history:
        messages.append({
            "role": msg.get("role", "user"), 
            "content": msg.get("message", "")
        })
    
    # Add current prompt
    messages.append({"role": "user", "content": prompt})

    def call_openai():
        response = openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=messages,
            temperature=0.7
        )
        return response.choices[0].message.content

    return await asyncio.to_thread(call_openai)

# ---------------- Booking Endpoints ----------------
@app.post("/book/", status_code=200)
def create_booking(booking: Booking):
    """Create a new booking"""
    booking_id = str(uuid4())
    data = booking.model_dump()  # Use model_dump() instead of dict() for Pydantic v2
    
    # Save to Redis if available
    if redis_client:
        try:
            redis_client.set(f"{REDIS_BOOKING_PREFIX}{booking_id}", json.dumps(data))
        except Exception as e:
            print(f"Redis error: {e}")
    
    # Always save to file as backup
    save_booking_file(booking_id, data)
    
    return {
        "booking_id": booking_id, 
        "status": "saved",
        "data": data
    }

@app.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    """Get a specific booking by ID"""
    # Try Redis first
    if redis_client:
        try:
            raw = redis_client.get(f"{REDIS_BOOKING_PREFIX}{booking_id}")
            if raw:
                return json.loads(raw)
        except Exception as e:
            print(f"Redis error: {e}")
    
    # Fallback to file
    if BOOKING_FILE.exists():
        all_bookings = json.loads(BOOKING_FILE.read_text())
        booking = all_bookings.get(booking_id)
        if booking:
            return booking
    
    raise HTTPException(status_code=404, detail="Booking not found")

@app.get("/all_bookings")
def list_bookings():
    """List all bookings"""
    if BOOKING_FILE.exists():
        return json.loads(BOOKING_FILE.read_text())
    return {}

@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: str):
    """Delete a booking"""
    # Delete from Redis
    if redis_client:
        try:
            redis_client.delete(f"{REDIS_BOOKING_PREFIX}{booking_id}")
        except Exception as e:
            print(f"Redis error: {e}")
    
    # Delete from file
    if BOOKING_FILE.exists():
        all_bookings = json.loads(BOOKING_FILE.read_text())
        if booking_id in all_bookings:
            del all_bookings[booking_id]
            BOOKING_FILE.write_text(json.dumps(all_bookings, indent=2))
            return {"status": "deleted", "booking_id": booking_id}
    
    raise HTTPException(status_code=404, detail="Booking not found")

# ---------------- Chat Endpoints ----------------
@app.post("/chat/")
async def chat_endpoint(chat: ChatMessage):
    """Send a message and get AI response"""
    if not openai_client:
        raise HTTPException(
            status_code=500, 
            detail="OpenAI API key not configured. Please set OPENAI_API_KEY environment variable."
        )
    
    session_key = f"{REDIS_CHAT_PREFIX}{chat.session_id}"
    history = []
    
    # Retrieve existing history from Redis
    if redis_client:
        try:
            history_raw = redis_client.lrange(session_key, 0, -1)
            history = [json.loads(h) for h in history_raw] if history_raw else []
        except Exception as e:
            print(f"Redis error: {e}")
    
    # Append user message
    user_msg = {"role": "user", "message": chat.message}
    if redis_client:
        try:
            redis_client.rpush(session_key, json.dumps(user_msg))
            # Set expiry for 24 hours
            redis_client.expire(session_key, 86400)
        except Exception as e:
            print(f"Redis error: {e}")
    
    save_chat_file(chat.session_id, user_msg)

    # Query OpenAI
    try:
        answer = await query_openai_async(chat.message, chat_history=history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    # Append assistant reply
    bot_msg = {"role": "assistant", "message": answer}
    if redis_client:
        try:
            redis_client.rpush(session_key, json.dumps(bot_msg))
        except Exception as e:
            print(f"Redis error: {e}")
    
    save_chat_file(chat.session_id, bot_msg)

    return {
        "answer": answer, 
        "session_id": chat.session_id,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/chat_history/{session_id}")
def get_chat_history(session_id: str):
    """Get chat history for a session"""
    session_key = f"{REDIS_CHAT_PREFIX}{session_id}"
    
    # Try Redis first
    if redis_client:
        try:
            history_raw = redis_client.lrange(session_key, 0, -1)
            if history_raw:
                return [json.loads(h) for h in history_raw]
        except Exception as e:
            print(f"Redis error: {e}")

    # Fallback to file
    session_file = CHAT_DIR / f"session_{session_id}.json"
    if session_file.exists():
        return json.loads(session_file.read_text())

    return []

@app.delete("/chat_history/{session_id}")
def delete_chat_history(session_id: str):
    """Delete chat history for a session"""
    session_key = f"{REDIS_CHAT_PREFIX}{session_id}"
    
    # Delete from Redis
    if redis_client:
        try:
            redis_client.delete(session_key)
        except Exception as e:
            print(f"Redis error: {e}")
    
    # Delete from file
    session_file = CHAT_DIR / f"session_{session_id}.json"
    if session_file.exists():
        session_file.unlink()
        return {"status": "deleted", "session_id": session_id}
    
    raise HTTPException(status_code=404, detail="Chat session not found")

@app.get("/all_chats")
def list_chats():
    """List all chat sessions"""
    files = list(CHAT_DIR.glob("session_*.json"))
    all_chats = {}
    for f in files:
        session_id = f.stem.replace("session_", "")
        try:
            all_chats[session_id] = json.loads(f.read_text())
        except Exception as e:
            print(f"Error reading {f}: {e}")
    return all_chats

# ---------------- Health Check ----------------
@app.get("/")
def health_check():
    """API health check"""
    return {
        "status": "running",
        "redis_connected": redis_client is not None,
        "openai_configured": openai_client is not None,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
def detailed_health():
    """Detailed health check"""
    redis_status = "disconnected"
    if redis_client:
        try:
            redis_client.ping()
            redis_status = "connected"
        except:
            redis_status = "error"
    
    return {
        "api": "online",
        "redis": redis_status,
        "openai": "configured" if openai_client else "not configured",
        "storage": {
            "chat_sessions": len(list(CHAT_DIR.glob("*.json"))),
            "bookings_file_exists": BOOKING_FILE.exists()
        }
    }