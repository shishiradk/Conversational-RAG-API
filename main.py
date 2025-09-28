from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from uuid import uuid4
from datetime import datetime
from typing import List, Dict, Optional
import redis
import json
from pathlib import Path
import asyncio
import openai
import os

# -------------------
# Configuration
# -------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CHAT_PREFIX = "chat:"
REDIS_BOOKING_PREFIX = "booking:"
CHAT_DIR = Path("chat_sessions")
CHAT_DIR.mkdir(exist_ok=True)
BOOKING_FILE = Path("bookings.json")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

app = FastAPI(title="Conversational RAG API")

# -------------------
# Models
# -------------------
class Booking(BaseModel):
    name: str
    email: EmailStr
    date: str
    time: str

class ChatMessage(BaseModel):
    session_id: str
    message: str

# -------------------
# Helpers
# -------------------
def save_booking_file(booking_id: str, data: dict):
    if BOOKING_FILE.exists():
        all_bookings = json.loads(BOOKING_FILE.read_text())
    else:
        all_bookings = {}
    all_bookings[booking_id] = data
    BOOKING_FILE.write_text(json.dumps(all_bookings, indent=2))

def save_chat_file(session_id: str, message: str):
    session_file = CHAT_DIR / f"session_{session_id}.json"
    if session_file.exists():
        session_data = json.loads(session_file.read_text())
    else:
        session_data = []
    session_data.append({
        "timestamp": datetime.now().isoformat(),
        "message": message
    })
    session_file.write_text(json.dumps(session_data, indent=2))

async def query_openai_async(prompt: str, chat_history: Optional[List[Dict[str, str]]] = None) -> str:
    chat_history = chat_history or []
    messages = [{"role": "system", "content": "You are an assistant."}]
    for msg in chat_history:
        messages.append({"role": "user", "content": msg.get("message", "")})
    messages.append({"role": "user", "content": prompt})

    def call_openai():
        response = openai.chat.completions.create(
            model="gpt-4-turbo",
            messages=messages
        )
        return response.choices[0].message.content

    return await asyncio.to_thread(call_openai)

# -------------------
# Booking Endpoints
# -------------------
@app.post("/book/", status_code=200)
def create_booking(booking: Booking):
    booking_id = str(uuid4())
    data = booking.dict()
    redis_client.set(f"{REDIS_BOOKING_PREFIX}{booking_id}", json.dumps(data))
    save_booking_file(booking_id, data)
    return {"booking_id": booking_id, "status": "saved"}

@app.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    raw = redis_client.get(f"{REDIS_BOOKING_PREFIX}{booking_id}")
    if raw:
        return json.loads(raw)
    if BOOKING_FILE.exists():
        all_bookings = json.loads(BOOKING_FILE.read_text())
        booking = all_bookings.get(booking_id)
        if booking:
            return booking
    raise HTTPException(status_code=404, detail="Booking not found")

@app.get("/all_bookings")
def list_bookings():
    if BOOKING_FILE.exists():
        return json.loads(BOOKING_FILE.read_text())
    return {}

# -------------------
# Chat Endpoints
# -------------------
@app.post("/chat/")
async def chat_endpoint(chat: ChatMessage):
    session_key = f"{REDIS_CHAT_PREFIX}{chat.session_id}"

    # Retrieve history from Redis
    history_raw = redis_client.get(session_key)
    history = json.loads(history_raw) if history_raw else []

    # Append new message
    history.append({"message": chat.message})
    redis_client.set(session_key, json.dumps(history))
    save_chat_file(chat.session_id, chat.message)

    # Query OpenAI with full history
    answer = await query_openai_async(chat.message, chat_history=history)

    # Save bot reply
    history.append({"message": answer})
    redis_client.set(session_key, json.dumps(history))
    save_chat_file(chat.session_id, answer)

    return {"answer": answer, "session_id": chat.session_id}

@app.get("/chat_history/{session_id}")
def get_chat_history(session_id: str):
    session_file = CHAT_DIR / f"session_{session_id}.json"
    if session_file.exists():
        return json.loads(session_file.read_text())
    raw = redis_client.get(f"{REDIS_CHAT_PREFIX}{session_id}")
    if raw:
        return json.loads(raw)
    return []

@app.get("/all_chats")
def list_chats():
    files = list(CHAT_DIR.glob("session_*.json"))
    all_chats = {}
    for f in files:
        session_id = f.stem.replace("session_", "")
        all_chats[session_id] = json.loads(f.read_text())
    return all_chats
