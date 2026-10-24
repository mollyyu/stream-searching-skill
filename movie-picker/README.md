# Movie Picker

Pick your streaming services, answer 5–10 "which would you rather watch?" questions, and get movies matched to your taste that you can watch on your services right now. FastAPI and plain HTML/JS, data from TMDB, everything stored locally in SQLite. No LLM calls.

**Full documentation is in the [main README](../README.md):**
- [using the app](../README.md#using-the-app)
- [architecture](../README.md#architecture)
- [recommendation engine](../README.md#the-recommendation-engine)
- [data model](../README.md#data-model)
- [HTTP API](../README.md#http-api)
- [how the skill talks to this app](../README.md#how-the-skill-and-the-app-communicate)

## Quick start

```bash
export TMDB_API_KEY=your_key          # in your shell profile, never in a file in this repo
uv run uvicorn app.main:app --reload  # http://localhost:8000
uv run pytest                         # tests use a fake TMDB, so no key or network needed
```

Develop against a scratch database so your real profiles stay untouched:

```bash
MOVIE_PICKER_DB=/tmp/mp-dev.db uv run uvicorn app.main:app --reload --port 8001
```

| Path | What's there |
|---|---|
| `app/main.py` | routes, profiles, candidate pool, quiz flow |
| `app/recommender.py` | the taste model (pure functions) |
| `app/db.py` | SQLite schema, migrations, queries |
| `app/tmdb.py` | async TMDB client with caching |
| `static/` | the web page |
| `tests/` | API, recommender, skill-bridge and secrets tests |
| `data/` | your database and logs (git-ignored) |
