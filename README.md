# Chukzbook AI: Book Idea Check — Project Brief

The **AI Book Idea Check** is a robust, Django-based backend application designed to evaluate an author's raw book idea. It processes user input through a sophisticated, multi-stage AI and data-gathering pipeline, producing a verified market briefing (in JSON and PDF format) that assesses the viability, genre, keywords, and competitive landscape of the proposed book.

---

## 🏗️ Architecture & Technology Stack

- **Framework**: Django & Django REST Framework (DRF)
- **AI Orchestration**: LangChain (`langchain-core`, `langchain-deepseek`, `langchain-anthropic`)
- **Primary LLM**: DeepSeek (`deepseek-chat`)
- **Fallback LLM**: Anthropic Claude (`claude-3-haiku-20240307`, `claude-3-sonnet-20240229`)
- **Market Data Engine**: SerpApi (Amazon Search Engine)
- **PDF Generation**: `fpdf2` (with automated Unicode-to-ASCII sanitization)
- **Task Management**: Python native `threading.Thread` and `concurrent.futures.ThreadPoolExecutor`
- **Caching**: Django's native caching mechanism (Memcached/Local-memory)

---

## ⚙️ The 4-Stage Pipeline

The core of the application resides in `book_idea/views.py` (`BookIdeaCheckAPIView`), which receives a user's name, email, and a free-text "author brief." It processes the data sequentially through four distinct stages:

### Stage 1: Classification (`services/ai_engine.py`)
- **Goal**: Parse the unstructured author brief into structured metadata (genre, sub-genres, target audience, and 5 "seed keywords").
- **Mechanics**: Uses DeepSeek with a low temperature (0.2) to ensure strict adherence to a predefined JSON schema (`STAGE_1_SCHEMA`).
- **Resilience**: Features an automatic fallback chain. If DeepSeek fails, it retries with an even lower temperature (0.1), and if that fails, it falls back to Claude 3 Haiku.

### Stage 2: Market Data Acquisition (`services/market_data.py`)
- **Goal**: Gather real-world Amazon search data to ground the AI's recommendations in reality.
- **Mechanics**: Takes the 5 "seed keywords" from Stage 1 and queries SerpApi's Amazon engine concurrently using a `ThreadPoolExecutor` (max 5 workers). It extracts real-world book titles, prices, ratings, and review counts for the top 10 organic results per keyword.
- **Cost-Optimization**: Implements a 24-hour Django cache using the search keyword as a key to prevent redundant and expensive API calls.
- **Graceful Degradation**: If an API call times out, it flags the overall data quality as "partial" rather than crashing the pipeline.

### Stage 3: Analyst Synthesis (`services/ai_engine.py`)
- **Goal**: Synthesize the original author brief, the Stage 1 classification, and the raw Stage 2 market data into a cohesive, professional market briefing.
- **Mechanics**: Feeds all collected data back into the LLM (DeepSeek as primary, Claude 3 Sonnet as fallback) with a temperature of 0.4 for slightly more creative synthesis. It outputs a structured JSON object (`STAGE_3_SCHEMA`) containing a viability line, genre summary, keyword recommendations, a competitive snapshot, and a draft book description.

### Stage 4: Anti-Hallucination Validation (`services/validator.py`)
- **Goal**: Ensure the LLM did not hallucinate or fabricate market statistics in Stage 3.
- **Mechanics**: 
  - Extracts every number (prices, ratings, review counts) present in the raw Stage 2 market data.
  - Scans all text generated in Stage 3. If the LLM cited a number that does *not* exist in the raw market data, it aggressively scrubs it and replaces it with `[data unavailable]`.
  - Enforces word count limits on the draft description (80–200 words).
  - Automatically prepends a visible warning banner (`⚠ Based on limited market data for this niche.`) to the final briefing if Stage 2 experienced API timeouts.

---

## 📧 Notifications & Persistence

Once the JSON briefing survives Stage 4:
1. **Database Persistence**: The raw input, validated JSON output, IP address, and the LLM provider used are saved to the SQLite database via the `IdeaSubmission` model.
2. **Background Processing**: The server immediately returns an HTTP 200 response to the frontend client with the JSON data.
3. **PDF Generation**: In the background, `services/notifications.py` uses `fpdf2` to lay out a clean, 1-page PDF document. A custom sanitizer runs over the LLM text to ensure smart-quotes and em-dashes don't break the PDF's ASCII/Latin-1 font encoder.
4. **Email Delivery**: The PDF is attached to a standard Django `EmailMessage` and dispatched to the user's provided email address using the configured SMTP backend.

---

## 🔒 Security & Rate Limiting

- **Throttles**: Protects the API from spam and abuse (`book_idea/throttles.py`). 
  - `EmailRateThrottle`: Limits submissions to 1 per day, per email address.
  - `IPRateThrottle`: Limits submissions to 4 per day, per IP address.
- **Input Validation**: `BookIdeaCheckSerializer` requires the author brief to be a minimum of 50 characters (preventing empty/useless API calls) and truncates the input at 500 words to prevent excessive token usage.

---

## 📁 Repository Structure

```text
chukzbook_AI/
├── book_idea/             # Primary Application
│   ├── services/          
│   │   ├── ai_engine.py       # Stages 1 & 3 (LLM interactions)
│   │   ├── market_data.py     # Stage 2 (SerpApi & Caching)
│   │   ├── validator.py       # Stage 4 (Anti-hallucination)
│   │   └── notifications.py   # PDF rendering & SMTP emailing
│   ├── models.py          # Database schema (IdeaSubmission)
│   ├── serializers.py     # Input validation (DRF)
│   ├── throttles.py       # Rate limiting logic
│   ├── spec_constants.py  # Prompt templates & schemas
│   ├── views.py           # API Orchestration (BookIdeaCheckAPIView)
│   └── urls.py            # Route: POST /api/book-idea/check/
├── core/                  # Django Configuration
│   ├── settings.py        # API keys, DB config, Email backend
│   └── urls.py            # Global routing
└── manage.py
```
