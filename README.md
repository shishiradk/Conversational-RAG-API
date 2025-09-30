cha# Conversational RAG API

Conversational RAG API is a FastAPI backend for conversational apps with Retrieval-Augmented Generation (RAG), chat session management, and a booking system. It integrates OpenAI for chat completions and Redis for fast storage, with robust error handling and .env support.

## Features

- **Conversational Chat API**: Store and retrieve chat sessions, interact with OpenAI GPT models.
- **Booking System**: Create, list, and delete bookings with email validation.
- **Persistent Storage**: Uses Redis and local files for chat and booking history.
- **Async OpenAI Integration**: Fast, non-blocking calls to OpenAI's API.
- **Health Endpoints**: Check API, Redis, and OpenAI status.
- **.env Support**: Use a `.env` file for secrets and config.
- **Robust Error Handling**: Graceful fallback if Redis or OpenAI is unavailable.

## Requirements

Install dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file in the project root with:

```
OPENAI_API_KEY=your_openai_key
REDIS_URL=redis://localhost:6379
```

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
- `DELETE /bookings/{booking_id}` — Delete a booking


### Chat

- `POST /chat/` — Send a message to a chat session
- `GET /chat_history/{session_id}` — Get chat history for a session
- `DELETE /chat_history/{session_id}` — Delete chat history for a session
- `GET /all_chats` — List all chat sessions


### Health

- `GET /` — Basic health check
- `GET /health` — Detailed health status (Redis, OpenAI, storage)

## Data Storage

- **Redis**: Stores chat and booking data for fast access
- **Local Files**: Chat sessions in `chat_sessions/`, bookings in `bookings.json`


## Example Request

```bash
curl -X POST "http://localhost:8000/chat/" \
	 -H "Content-Type: application/json" \
	 -d '{"session_id": "abc123", "message": "Hello!"}'
```

## .env Example

```
OPENAI_API_KEY=sk-...
REDIS_URL=redis://localhost:6379
```

## License

MIT