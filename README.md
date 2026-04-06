# 🤖 Naukri AI Agent v2

An industry-grade, AI-powered job application agent for Naukri.com.
Automatically finds, evaluates, and applies to jobs that match your profile — using only **free tools**.

---

## ✨ What's new in v2

| Feature | v1 | v2 |
|---|---|---|
| LLM Model | `gemini-3-flash-preview` ❌ (wrong name) | `gemini-1.5-flash` ✅ (free, 1M tokens/day) |
| Memory storage | Flat JSON (crash risk) | SQLite (ACID-safe) |
| LLM caching | ❌ | ✅ saves ~50% API calls |
| Logging | `print()` everywhere | Rich console + rotating file logs |
| Error handling | Silent failures | `tenacity` retry with exponential backoff |
| Session handling | Re-login every run | Cookie persistence (faster, less suspicious) |
| Anti-detection | Basic | Randomized UA, jitter timing, masked webdriver |
| Config | Hardcoded constants | Pydantic-validated `.env` settings |
| Package structure | 1 monolithic file | Proper modules (config/browser/auth/jobs/llm/storage) |
| Tests | ❌ | ✅ pytest — 20+ unit tests |
| Analytics | ❌ | ✅ keyword conversion rates, score insights |
| Notifications | ❌ | ✅ Free Telegram bot |
| Scheduler | ❌ | ✅ APScheduler (runs automatically every morning) |
| Credentials | Plain `.env` | OS keyring support |
| Adaptive threshold | ❌ | ✅ auto-tunes based on interview rate |
| Docker | ❌ | ✅ |

---

## 🚀 Quick start

### 1. Clone and install
```bash
git clone <your-repo>
cd naukri_agent_v2
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.template .env
# Edit .env with your values
```

Required `.env` values:
```env
NAUKRI_EMAIL=your@email.com
NAUKRI_PASSWORD=yourpassword
GEMINI_API_KEY=your_free_key_here   # https://aistudio.google.com/app/apikey
```

### 3. Run
```bash
python main.py
```

---

## 🆓 Free tier details

| Service | Free quota | Cost if exceeded |
|---|---|---|
| Gemini 1.5 Flash | 15 req/min · 1M tokens/day · 1,500 req/day | ~$0.075/1M tokens |
| Telegram Bot | Unlimited | Free forever |
| Chrome / Selenium | Local browser | Free |
| SQLite | Local file | Free |

**Expected daily API usage:** ~5–20 Gemini calls per run (most JDs are cached after first eval).
Well within the free quota.

---

## 📋 All CLI commands

```bash
python main.py                    # Run the agent
python main.py --feedback         # Log interview/rejection outcome
python main.py --plan             # Set today's job search focus
python main.py --blacklist        # Blacklist a company
python main.py --memory           # Show memory & stats overview
python main.py --analytics        # Full analytics report
python main.py --schedule         # Start automated daily scheduler
python main.py --clear-session    # Force re-login next run
python main.py --store-credentials # Save credentials to OS keyring (more secure)
```

---

## 🗺️ Project structure

```
naukri_agent_v2/
├── main.py                  # Entry point, CLI, Rich UI, scheduler
├── config/
│   └── settings.py          # Pydantic-validated settings from .env
├── browser/
│   └── driver.py            # Chrome driver, anti-detection, session cookies
├── auth/
│   └── login.py             # Login with session persistence
├── jobs/
│   ├── evaluator.py         # LLM job scoring & decision engine
│   └── apply.py             # Search, parse cards, fill forms
├── llm/
│   └── provider.py          # Gemini 1.5 Flash with caching + retry
├── storage/
│   └── database.py          # SQLite memory (applied jobs, blacklist, plans)
├── analytics/
│   └── insights.py          # Keyword conversion rates, score analytics
├── notifications/
│   └── telegram.py          # Free Telegram daily summary
├── utils/
│   ├── logger.py            # Rich + rotating file logging
│   └── credentials.py       # OS keyring credential storage
├── tests/
│   └── test_evaluator.py    # 20+ pytest unit tests
├── memory/                  # Auto-created: agent.db, llm_cache.json, cookies
├── logs/                    # Auto-created: agent.log, summary_YYYY-MM-DD.txt
├── debug_pages/             # Auto-created: HTML/PNG snapshots on errors
├── .env.template            # Copy to .env and fill in
├── requirements.txt
├── pyproject.toml
└── Dockerfile
```

---

## 📊 Adaptive learning

After each run, record outcomes:
```bash
python main.py --feedback
```

The agent automatically:
- Adjusts the `MATCH_THRESHOLD` suggestion based on your interview rate
- Learns which keywords produce interviews
- Builds a blacklist from repeated rejections

---

## 🔔 Telegram setup (2 minutes, free)

1. Message `@BotFather` on Telegram → `/newbot` → copy the token
2. Message `@userinfobot` on Telegram → copy your chat ID
3. Add to `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGhIjKlMnOpQrStUvWxYz
   TELEGRAM_CHAT_ID=987654321
   ```

---

## ⏰ Automatic daily runs

```bash
# Set time in .env
SCHEDULE_TIME=09:00

# Start the scheduler (run in tmux/screen to keep it alive)
python main.py --schedule
```

---

## 🔒 Secure credential storage

Instead of storing passwords in `.env`, use the OS keyring:
```bash
python main.py --store-credentials
# Then in .env:
CREDENTIAL_STORE=keyring
```

---

## 🧪 Running tests

```bash
pytest tests/ -v
# With coverage:
pytest tests/ --cov=. --cov-report=term-missing
```

---

## 🐳 Docker

```bash
docker build -t naukri-agent .
docker run --env-file .env \
  -v $(pwd)/memory:/app/memory \
  -v $(pwd)/logs:/app/logs \
  naukri-agent
```
