import os
import uuid
import json
import logging
from typing import List, Optional, Dict
import asyncio

import docx
import PyPDF2
# import pinecone  # Temporarily disabled
import openai
import redis
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from sentence_transformers import SentenceTransformer

# ----------------- Logger -----------------
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("conversational_rag")

# ----------------- Environment -----------------
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ENV = os.getenv("PINECONE_ENV", "us-east-1")
PINECONE_INDEX = os.getenv("PINECONE_INDEX", "rag-index")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# ----------------- FastAPI -----------------
app = FastAPI(title="Conversational RAG API")

# ----------------- Redis -----------------
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# ----------------- Temporary In-Memory Storage (replacing Pinecone) -----------------
# This is a temporary replacement for Pinecone during development
document_store = []  # List to store document chunks temporarily

# ----------------- Embeddings -----------------
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

# ----------------- File Reading -----------------
def read_text_file(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

def read_pdf_file(file_path: str) -> str:
    text = ""
    with open(file_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

def read_docx_file(file_path: str) -> str:
    doc = docx.Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs])

def read_document(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".txt":
        return read_text_file(file_path)
    elif ext == ".pdf":
        return read_pdf_file(file_path)
    elif ext == ".docx":
        return read_docx_file(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

# ----------------- Text Chunking -----------------
def split_text(text: str, chunk_size: int = 500) -> List[str]:
    sentences = text.replace("\n", " ").split(".")
    chunks: List[str] = []
    current_chunk: List[str] = []
    current_size = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if not sentence.endswith("."):
            sentence += "."
        sentence_size = len(sentence)
        if current_size + sentence_size > chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = [sentence]
            current_size = sentence_size
        else:
            current_chunk.append(sentence)
            current_size += sentence_size

    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

# ----------------- Temporary Document Storage (replacing Pinecone) -----------------
async def add_to_document_store_async(texts: List[str], source: str = "uploaded") -> None:
    """Temporarily store documents in memory instead of Pinecone"""
    if not texts:
        return
    
    for text in texts:
        document_store.append({
            "id": f"id-{uuid.uuid4().hex}",
            "text": text,
            "source": source
        })
    
    LOG.info(f"Added {len(texts)} chunks to document store from {source}")

async def process_and_store_async(file_path: str) -> Dict[str, int]:
    text = await asyncio.to_thread(read_document, file_path)
    chunks = split_text(text, chunk_size=500)
    await add_to_document_store_async(chunks, source=os.path.basename(file_path))
    return {"chunks_added": len(chunks), "file": os.path.basename(file_path)}

# ----------------- Redis Chat -----------------
async def save_chat_history_async(session_id: str, message: str) -> None:
    await asyncio.to_thread(redis_client.rpush, f"chat:{session_id}", message)

async def get_chat_history_async(session_id: str) -> List[str]:
    return await asyncio.to_thread(redis_client.lrange, f"chat:{session_id}", 0, -1)

# ----------------- Redis Booking -----------------
async def save_booking_async(name: str, email: str, date: str, time: str) -> str:
    booking_id = uuid.uuid4().hex
    booking_data = {"name": name, "email": email, "date": date, "time": time}
    await asyncio.to_thread(redis_client.set, f"booking:{booking_id}", json.dumps(booking_data))
    return booking_id

async def get_booking_async(booking_id: str) -> Optional[Dict]:
    data = await asyncio.to_thread(redis_client.get, f"booking:{booking_id}")
    return json.loads(data) if data else None

# ----------------- OpenAI GPT -----------------
async def query_openai_async(prompt: str, chat_history: Optional[List[str]] = None) -> str:
    def call_openai():
        openai.api_key = OPENAI_API_KEY
        messages = [{"role": "system", "content": "You are a helpful assistant."}]
        if chat_history:
            messages += [{"role": "user", "content": m} for m in chat_history]
        messages.append({"role": "user", "content": prompt})
        response = openai.ChatCompletion.create(model="gpt-4-turbo", messages=messages)
        return response.choices[0].message["content"]
    
    return await asyncio.to_thread(call_openai)

# ----------------- FastAPI Endpoints -----------------
@app.post("/upload/")
async def upload_file(file: UploadFile = File(...)):
    temp_path = f"temp_{uuid.uuid4().hex}_{file.filename}"
    with open(temp_path, "wb") as f:
        f.write(await file.read())
    
    result = await process_and_store_async(temp_path)
    os.remove(temp_path)
    return result

@app.post("/chat")
async def chat_endpoint(session_id: str = Form(...), query: str = Form(...)):
    history = await get_chat_history_async(session_id)
    
    # Add context from document store (simple keyword matching for now)
    context = ""
    query_lower = query.lower()
    for doc in document_store:
        if any(word in doc["text"].lower() for word in query_lower.split()):
            context += f"Context from {doc['source']}: {doc['text'][:200]}...\n"
    
    full_prompt = f"Context: {context}\n\nUser question: {query}"
    answer = await query_openai_async(full_prompt, chat_history=history)
    
    await save_chat_history_async(session_id, f"User: {query}")
    await save_chat_history_async(session_id, f"Bot: {answer}")
    return {"answer": answer, "session_id": session_id}

@app.post("/book_interview")
async def book_interview(
    name: str = Form(...),
    email: str = Form(...),
    date: str = Form(...),
    time: str = Form(...)
):
    booking_id = await save_booking_async(name, email, date, time)
    return {"booking_id": booking_id}

@app.get("/booking/{booking_id}")
async def get_booking_info(booking_id: str):
    data = await get_booking_async(booking_id)
    if not data:
        raise HTTPException(status_code=404, detail="Booking not found")
    return data

@app.get("/")
async def root():
    return {"message": "Conversational RAG API is running", "documents_stored": len(document_store)}