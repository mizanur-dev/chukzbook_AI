 # Chukzbook AI — Book Idea Check

A Django backend that evaluates an author's raw book idea using a
four-stage pipeline (LLM classification, Amazon market data, LLM
synthesis, and automated validation). The service returns a
structured market briefing (JSON) and emails a one-page PDF briefing
to the submitting author.

This README covers installation, configuration, architecture, and
usage for local development and basic deployment.

---

## Key features

- Four-stage pipeline: classification → market data → synthesis →
  validation.
- Resilient LLM orchestration with DeepSeek primary and Anthropic
  Claude fallback.
- Amazon/SerpApi scraping with 24-hour caching and graceful
  degradation.
- Anti-hallucination validator that scrubs unsupported market
  metrics and enforces description length limits.
- Background PDF rendering and email delivery.
- Simple rate-limiting: one free check per email per day; four per
  IP per day.

---

## Quickstart (local development)

Prerequisites:

- Python 3.10+ (recommended)
- Git
- An SMTP account for sending emails (or configure console backend)

Setup:

```bash
python -m venv .venv
.venv\\Scripts\\activate   # Windows
# or: source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt
cp .env.example .env      # edit with your keys
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000 and POST to `/api/book-idea/check/`.

---

## Environment variables

Create a `.env` file in the project root with the following keys:

- `SECRET_KEY` — Django secret key (development only).
- `DEEPSEEK_API_KEY` — DeepSeek LLM API key (optional if only using Claude).
- `ANTHROPIC_API_KEY` — Anthropic API key for Claude fallbacks.
- `SERPAPI_API_KEY` — SerpApi API key for Amazon scraping.
- `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_SMTP_USER`,
  `EMAIL_SMTP_PASSWORD` — SMTP configuration for outgoing mail.
- `DEBUG` — `True`/`False`.

Example `.env` (development):

```dotenv
SECRET_KEY=changeme
DEBUG=True
DEEPSEEK_API_KEY=
ANTHROPIC_API_KEY=
SERPAPI_API_KEY=
EMAIL_SMTP_HOST=smtp.example.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USER=you@example.com
EMAIL_SMTP_PASSWORD=app-password
```

---

## API

POST `/api/book-idea/check/`

Request JSON:

```json
{
  "name": "Alicia",
  "email": "alicia@example.com",
  "author_brief_text": "A warm small-town second-chance romance about two childhood friends reunited after ten years..."
}
```

Responses:

- 200 OK — Validated briefing JSON (normal flow).
- 200 OK — GRACEFUL_FALLBACK when all LLMs fail; `viability_line` will contain the "tool is busy" message.
- 400 Bad Request — When Stage 1 cannot classify the brief; exact message:
  "We couldn't read your book idea. Please add a little more detail and try again.".
- 429 Too Many Requests — Rate-limited; exact message:
  "You've used your free check for today. Talk to a specialist for a deeper analysis →"

---

## Pipeline details

- Stage 1 (`book_idea/services/ai_engine.py`) — LLM classifies the
  author's brief into `primary_genre`, `subgenres`, `themes`,
  `target_reader`, `format`, `comp_authors`, and `seed_keywords` (5–7
  phrases). DeepSeek is primary; Claude is a fallback.
- Stage 2 (`book_idea/services/market_data.py`) — Queries SerpApi
  (Amazon engine) concurrently for each `seed_keyword`. Results are
  cached with an MD5 hash key for 24 hours to reduce API cost. When
  queries fail/time out, `data_quality` is set to `partial` and the
  pipeline continues.
- Stage 3 (`book_idea/services/ai_engine.py`) — LLM synthesizes a
  market briefing (viability_line, genre_summary, top_keywords,
  recommended_categories, competitive_snapshot, draft_description,
  next_step). DeepSeek is primary; Claude is fallback.
- Stage 4 (`book_idea/services/validator.py`) — Validates the Stage 3
  output against Stage 2 market data. Any numeric market metrics not
  present in the raw market data (and not in the author's brief) are
  replaced with `[data unavailable]`. If many fields are censored
  (>50% of string fields), a low-confidence banner is prepended to
  the `genre_summary`. Draft descriptions are checked to be 80–200
  words.

---

## Caching

- SerpApi results are cached for 24 hours using deterministic MD5
  keys (`amz_<md5(keyword)>`) in Django's cache backend (see
  `book_idea/services/market_data.py`).

---

## Logging & Monitoring

- LLM calls log the provider that produced the answer (`deepseek`,
  `claude`, or `fallback/failed`) to help monitor reliability and
  fallback frequency.
- Application logs use Python's `logging` module — configure handlers
  in `core/settings.py` as needed.

---

## Rate limiting

- `EmailRateThrottle`: 1 submission per email address per day.
- `IPRateThrottle`: 4 submissions per IP address per day.

When a client is throttled, the API returns the exact message required
by the Harmony spec: "You've used your free check for today. Talk to a
specialist for a deeper analysis →".

---

## Tests

Run Django tests:

```bash
python manage.py test
```

There are currently no project tests included; please add unit
coverage for each service module and the API view.

---

## Cleanup & housekeeping

- Remove `__pycache__/` directories before committing.
- Keep `db.sqlite3` and `.env` out of source control; add them to
  `.gitignore`.

---

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Open a PR with a clear description and tests.

---

## Contact

For questions about the Harmony Publishing spec or integration,
contact the maintainers.

---

## License

See `LICENSE` (if present) or agree license terms before redistributing.

