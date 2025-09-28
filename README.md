# Conversational RAG API

Conversational RAG API is a FastAPI-based backend for building conversational applications with Retrieval-Augmented Generation (RAG) and booking/chat session management. It integrates with OpenAI for chat completions and Redis for fast data storage.

## Features

- **Conversational Chat API**: Store and retrieve chat sessions, interact with OpenAI GPT models.
- **Booking System**: Create and manage bookings with email validation.
- **Persistent Storage**: Uses Redis and local files for chat and booking history.
- **Async OpenAI Integration**: Fast, non-blocking calls to OpenAI's API.

## Requirements

Install dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Environment Variables

- `OPENAI_API_KEY`: Your OpenAI API key
- `REDIS_URL`: Redis connection string (default: `redis://localhost:6379`)

## Usage

Start the FastAPI server:

```bash
uvicorn main:app --reload
```

## API Endpoints

### Booking

- `POST /book/` — Create a booking
- `GET /bookings/{booking_id}` — Retrieve a booking by ID
- `GET /all_bookings` — List all bookings

### Chat

- `POST /chat/` — Send a message to a chat session
- `GET /chat_history/{session_id}` — Get chat history for a session
- `GET /all_chats` — List all chat sessions

## Data Storage

- **Redis**: Stores chat and booking data for fast access
- **Local Files**: Chat sessions in `chat_sessions/`, bookings in `bookings.json`

## Example Request

```bash
curl -X POST "http://localhost:8000/chat/" \
	-H "Content-Type: application/json" \
	-d '{"session_id": "abc123", "message": "Hello!"}'
```

## License

MIT