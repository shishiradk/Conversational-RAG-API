from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, validator
from uuid import uuid4
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import redis
import json
from pathlib import Path
import asyncio
from openai import OpenAI
import os
import re
import dateparser

load_dotenv()

# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CHAT_PREFIX = "chat:"
REDIS_BOOKING_PREFIX = "booking:"
REDIS_BOOKING_SESSION_PREFIX = "booking_session:"
CHAT_DIR = Path("chat_sessions")
CHAT_DIR.mkdir(exist_ok=True)
BOOKING_FILE = Path("bookings.json")

# Initialize OpenAI client
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY not set. Chat endpoint will not work.")
    openai_client = None
else:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Initialize Redis
try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print(f"✓ Connected to Redis at {REDIS_URL}")
except Exception as e:
    print(f"WARNING: Could not connect to Redis: {e}")
    redis_client = None

app = FastAPI(title="Conversational RAG API with Enhanced Booking")

# Models
class Booking(BaseModel):
    name: str
    email: EmailStr
    date: str
    time: str
    
    @validator('date')
    def validate_date(cls, v):
        try:
            booking_date = datetime.fromisoformat(v).date()
            if booking_date < datetime.now().date():
                raise ValueError("Booking date cannot be in the past")
            return v
        except ValueError as e:
            raise ValueError(f"Invalid date format or past date: {e}")

class ChatMessage(BaseModel):
    session_id: str
    message: str

class BookingSession:
    """Helper class to manage booking session state"""
    def __init__(self, data: dict = None):
        self.data = data or {}
        self.step = self.data.get('step', 'intent')
        self.name = self.data.get('name')
        self.email = self.data.get('email')
        self.date = self.data.get('date')
        self.time = self.data.get('time')
        self.confirmed = self.data.get('confirmed', False)
    
    def to_dict(self):
        return {
            'step': self.step,
            'name': self.name,
            'email': self.email,
            'date': self.date,
            'time': self.time,
            'confirmed': self.confirmed
        }
    
    def is_complete(self):
        return all([self.name, self.email, self.date, self.time])
    
    def get_missing_fields(self):
        fields = []
        if not self.name:
            fields.append('name')
        if not self.email:
            fields.append('email')
        if not self.date:
            fields.append('date')
        if not self.time:
            fields.append('time')
        return fields

# Helpers
def save_booking_file(booking_id: str, data: dict):
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

def extract_name(text: str) -> Optional[str]:
    """Extract name from user message"""
    patterns = [
        r"(?:my name is|i'm|i am|this is|name's|call me)\s+([a-zA-Z\s]+?)(?:\.|,|$|\s+and)",
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)(?:\s|$)"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) > 1 and not any(word in name.lower() for word in ['book', 'appointment', 'schedule']):
                return name.title()
    return None

def extract_email(text: str) -> Optional[str]:
    """Extract email from user message"""
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    match = re.search(pattern, text)
    return match.group(0) if match else None

def extract_datetime(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract date and time from user message"""
    parsed = dateparser.parse(
        text,
        settings={
            'PREFER_DATES_FROM': 'future',
            'RELATIVE_BASE': datetime.now()
        }
    )
    
    if parsed:
        date_str = parsed.date().isoformat()
        time_str = parsed.time().strftime("%H:%M")
        
        # If time is midnight (00:00), it might not have been specified
        if time_str == "00:00" and not any(word in text.lower() for word in ['midnight', '12am', '00:00']):
            time_str = None
            
        return date_str, time_str
    return None, None

def check_booking_intent(text: str) -> bool:
    """Check if message has booking intent"""
    keywords = [
        'book', 'booking', 'appointment', 'schedule', 'meeting', 
        'reservation', 'reserve', 'make an appointment', 'set up',
        'want to book', 'need to book', 'like to book'
    ]
    return any(keyword in text.lower() for keyword in keywords)

def format_booking_summary(booking_session: BookingSession) -> str:
    """Format booking details for confirmation"""
    return f"""
Please confirm your booking details:
• Name: {booking_session.name}
• Email: {booking_session.email}
• Date: {booking_session.date}
• Time: {booking_session.time}

Reply with 'yes' or 'confirm' to complete the booking, or tell me what you'd like to change.
"""

def get_redis_booking_session(session_id: str) -> Optional[BookingSession]:
    """Get booking session from Redis"""
    if not redis_client:
        return None
    
    try:
        key = f"{REDIS_BOOKING_SESSION_PREFIX}{session_id}"
        data = redis_client.get(key)
        if data:
            return BookingSession(json.loads(data))
    except Exception as e:
        print(f"Redis error getting booking session: {e}")
    return None

def save_redis_booking_session(session_id: str, booking_session: BookingSession):
    """Save booking session to Redis"""
    if not redis_client:
        return
    
    try:
        key = f"{REDIS_BOOKING_SESSION_PREFIX}{session_id}"
        redis_client.set(key, json.dumps(booking_session.to_dict()), ex=86400)
    except Exception as e:
        print(f"Redis error saving booking session: {e}")

def delete_redis_booking_session(session_id: str):
    """Delete booking session from Redis"""
    if not redis_client:
        return
    
    try:
        key = f"{REDIS_BOOKING_SESSION_PREFIX}{session_id}"
        redis_client.delete(key)
    except Exception as e:
        print(f"Redis error deleting booking session: {e}")

async def query_openai_async(prompt: str, chat_history: Optional[List[Dict[str, str]]] = None) -> str:
    if not openai_client:
        raise HTTPException(status_code=500, detail="OpenAI client not initialized. Please set OPENAI_API_KEY.")
    
    chat_history = chat_history or []
    messages = [{"role": "system", "content": "You are a helpful assistant."}]
    for msg in chat_history:
        messages.append({"role": msg.get("role", "user"), "content": msg.get("message", "")})
    messages.append({"role": "user", "content": prompt})

    def call_openai():
        response = openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=messages,
            temperature=0.7
        )
        return response.choices[0].message.content

    return await asyncio.to_thread(call_openai)

# ------------------ Booking Endpoints ------------------
@app.post("/book/", status_code=200)
def create_booking(booking: Booking):
    booking_id = str(uuid4())
    data = booking.model_dump()
    if redis_client:
        try:
            redis_client.set(f"{REDIS_BOOKING_PREFIX}{booking_id}", json.dumps(data))
        except Exception as e:
            print(f"Redis error: {e}")
    save_booking_file(booking_id, data)
    return {"booking_id": booking_id, "status": "saved", "data": data}

@app.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    if redis_client:
        try:
            raw = redis_client.get(f"{REDIS_BOOKING_PREFIX}{booking_id}")
            if raw:
                return json.loads(raw)
        except Exception as e:
            print(f"Redis error: {e}")
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

@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: str):
    if redis_client:
        try:
            redis_client.delete(f"{REDIS_BOOKING_PREFIX}{booking_id}")
        except Exception as e:
            print(f"Redis error: {e}")
    if BOOKING_FILE.exists():
        all_bookings = json.loads(BOOKING_FILE.read_text())
        if booking_id in all_bookings:
            del all_bookings[booking_id]
            BOOKING_FILE.write_text(json.dumps(all_bookings, indent=2))
            return {"status": "deleted", "booking_id": booking_id}
    raise HTTPException(status_code=404, detail="Booking not found")

# ------------------ Enhanced Chat Endpoint ------------------
@app.post("/chat/")
async def chat_endpoint(chat: ChatMessage):
    session_key = f"{REDIS_CHAT_PREFIX}{chat.session_id}"
    history = []

    # Get chat history
    if redis_client:
        try:
            history_raw = redis_client.lrange(session_key, 0, -1)
            history = [json.loads(h) for h in history_raw] if history_raw else []
        except Exception as e:
            print(f"Redis error: {e}")

    # Save user message
    user_msg = {"role": "user", "message": chat.message}
    if redis_client:
        try:
            redis_client.rpush(session_key, json.dumps(user_msg))
            redis_client.expire(session_key, 86400)
        except Exception as e:
            print(f"Redis error: {e}")
    save_chat_file(chat.session_id, user_msg)

    # Get or create booking session
    booking_session = get_redis_booking_session(chat.session_id)
    has_booking_intent = check_booking_intent(chat.message)
    
    # Initialize booking session if intent detected
    if has_booking_intent and not booking_session:
        booking_session = BookingSession()
    
    answer = None
    
    # Handle booking flow
    if booking_session:
        user_message_lower = chat.message.lower()
        
        # Handle cancellation
        if any(word in user_message_lower for word in ['cancel', 'stop', 'nevermind', 'forget it']):
            delete_redis_booking_session(chat.session_id)
            answer = "No problem! I've cancelled the booking process. How else can I help you?"
        
        # Handle confirmation
        elif booking_session.is_complete() and any(word in user_message_lower for word in ['yes', 'confirm', 'correct', 'looks good', 'perfect']):
            try:
                # Create the booking
                new_booking = Booking(
                    name=booking_session.name,
                    email=booking_session.email,
                    date=booking_session.date,
                    time=booking_session.time
                )
                booking_id = str(uuid4())
                data = new_booking.model_dump()
                
                # Save to Redis and file
                if redis_client:
                    redis_client.set(f"{REDIS_BOOKING_PREFIX}{booking_id}", json.dumps(data))
                save_booking_file(booking_id, data)
                
                # Clean up booking session
                delete_redis_booking_session(chat.session_id)
                
                answer = f"""✅ Booking confirmed!

Booking ID: {booking_id}
Name: {booking_session.name}
Email: {booking_session.email}
Date: {booking_session.date}
Time: {booking_session.time}

You'll receive a confirmation email shortly. Is there anything else I can help you with?"""
            
            except Exception as e:
                answer = f"I'm sorry, there was an error creating your booking: {str(e)}. Please try again."
                delete_redis_booking_session(chat.session_id)
        
        else:
            # Extract information from message
            extracted_name = extract_name(chat.message)
            extracted_email = extract_email(chat.message)
            extracted_date, extracted_time = extract_datetime(chat.message)
            
            # Update booking session with extracted info
            if extracted_name and not booking_session.name:
                booking_session.name = extracted_name
            if extracted_email and not booking_session.email:
                booking_session.email = extracted_email
            if extracted_date and not booking_session.date:
                booking_session.date = extracted_date
            if extracted_time and not booking_session.time:
                booking_session.time = extracted_time
            
            # Save updated session
            save_redis_booking_session(chat.session_id, booking_session)
            
            # Check if all information is collected
            if booking_session.is_complete():
                answer = format_booking_summary(booking_session)
            else:
                # Ask for missing information
                missing = booking_session.get_missing_fields()
                
                if 'name' in missing:
                    answer = "Great! I'd be happy to help you book an appointment. What's your name?"
                elif 'email' in missing:
                    answer = f"Thanks, {booking_session.name}! What's your email address?"
                elif 'date' in missing:
                    answer = "When would you like to schedule the appointment? (e.g., tomorrow, next Monday, December 5th)"
                elif 'time' in missing:
                    answer = "What time works best for you? (e.g., 2pm, 14:00, 2:30 PM)"
    
    else:
        # No booking in progress - use OpenAI for general conversation
        try:
            answer = await query_openai_async(chat.message, chat_history=history)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    # Save bot response
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
        "timestamp": datetime.now().isoformat(),
        "booking_in_progress": booking_session is not None,
        "booking_complete": booking_session.is_complete() if booking_session else False
    }

# ------------------ Chat History Endpoints ------------------
@app.get("/chat_history/{session_id}")
def get_chat_history(session_id: str):
    session_key = f"{REDIS_CHAT_PREFIX}{session_id}"
    if redis_client:
        try:
            history_raw = redis_client.lrange(session_key, 0, -1)
            if history_raw:
                return [json.loads(h) for h in history_raw]
        except Exception as e:
            print(f"Redis error: {e}")
    session_file = CHAT_DIR / f"session_{session_id}.json"
    if session_file.exists():
        return json.loads(session_file.read_text())
    return []

@app.delete("/chat_history/{session_id}")
def delete_chat_history(session_id: str):
    session_key = f"{REDIS_CHAT_PREFIX}{session_id}"
    booking_session_key = f"{REDIS_BOOKING_SESSION_PREFIX}{session_id}"
    
    if redis_client:
        try:
            redis_client.delete(session_key)
            redis_client.delete(booking_session_key)
        except Exception as e:
            print(f"Redis error: {e}")
    
    session_file = CHAT_DIR / f"session_{session_id}.json"
    if session_file.exists():
        session_file.unlink()
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="Chat session not found")

@app.get("/all_chats")
def list_chats():
    files = list(CHAT_DIR.glob("session_*.json"))
    all_chats = {}
    for f in files:
        session_id = f.stem.replace("session_", "")
        try:
            all_chats[session_id] = json.loads(f.read_text())
        except Exception as e:
            print(f"Error reading {f}: {e}")
    return all_chats

# ------------------ Booking Session Management ------------------
@app.get("/booking_session/{session_id}")
def get_booking_session_status(session_id: str):
    """Get the current booking session status"""
    booking_session = get_redis_booking_session(session_id)
    if booking_session:
        return {
            "active": True,
            "data": booking_session.to_dict(),
            "missing_fields": booking_session.get_missing_fields(),
            "is_complete": booking_session.is_complete()
        }
    return {"active": False}

@app.delete("/booking_session/{session_id}")
def cancel_booking_session(session_id: str):
    """Cancel an active booking session"""
    delete_redis_booking_session(session_id)
    return {"status": "cancelled", "session_id": session_id}

# ------------------ Health Checks ------------------
@app.get("/")
def health_check():
    return {
        "status": "running",
        "redis_connected": redis_client is not None,
        "openai_configured": openai_client is not None,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
def detailed_health():
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