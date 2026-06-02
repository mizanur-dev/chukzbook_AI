# AI Chatbot (REST)

Simple AI chatbot using Django REST Framework and Gemini via LangChain. The bot uses a single, global system prompt and validates users via an email-based session flow.

## Features

- Single system prompt for consistent behavior.
- Email-based session validation (no login).
- Simple REST endpoints for easy integration.

## Endpoints

- `POST /api/set_email/`
  - Body: `{ "email": "user@example.com" }`
  - Returns: `{ "message": "...", "session_id": "<email_xxxxxx>" }`

- `POST /api/chat/`
  - Body: `{ "message": "Hello", "session_id": "<from set_email>" }`
  - Returns: `{ "response": "..." }`

## System Prompt

Defined once in `chatbot/views.py` as `SYSTEM_PROMPT`. Update it there to change the assistant persona.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate    # PowerShell on Windows
pip install -r requirements.txt

# Add your Gemini API key to .env
echo DEEPSEEK_API_KEY=your_key_here > .env

python manage.py migrate
python manage.py runserver
```

## Quick Try (PowerShell)

```bash
curl -X POST http://127.0.0.1:8000/api/set_email/ -H "Content-Type: application/json" -d '{"email":"me@example.com"}'
# Copy session_id from the response

curl -X POST http://127.0.0.1:8000/api/chat/ -H "Content-Type: application/json" -d '{"message":"Hi!","session_id":"<paste>"}'
```

## Notes

- Ensure `.env` contains `GEMINI_API_KEY`.
- Authentication is disabled on these endpoints for simplicity; email + `session_id` gates access.
