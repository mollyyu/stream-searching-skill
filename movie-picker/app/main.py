"""Movie Picker API.

Flow: pick or create a profile (/api/users, /api/login), GET /api/services
and /api/genres to fill the first two screens, POST /api/quizzes to start a
quiz, then POST /api/next repeatedly with the answers so far. The quiz logic
is stateless: the browser sends the whole history each time and the server
replays it. Answers, recommendations and seen movies are also saved per
profile in a local SQLite database (app/db.py).
"""

import asyncio
import datetime
import hashlib
import random
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import recommender
from .db import DB
from .tmdb import TMDB, TMDBError, image_url

REGION = "US"
ROUNDS = 10              # questions for someone with no history
MIN_ROUNDS = 5           # fewest questions, once past quizzes have taught enough
PAST_WEIGHT = 0.5        # past answers count half as much as tonight's
PAST_LIMIT = 50          # most recent past answers used
POOL_PAGES = 10          # 20 movies per page -> up to 200 candidates
MIN_POOL = 12
RESULTS = 10
RESULT_CANDIDATES = 40   # checked for availability and series, best 10 kept
QUESTION_MAX_AGE = 20    # quiz questions use movies from the last 20 years when possible
COOKIE = "mp_user"
ICONS = ["🍿", "🎬", "🎥", "🦖", "👽", "🤖", "🐉", "🦸", "🧙", "👻", "🐱", "🐶", "🦊", "🐼", "🚀", "🌈"]

# The services offered on the first screen. TMDB often lists one service as
# several tier entries ("Netflix", "Netflix Standard with Ads"); each service
# here covers every entry whose name starts with `prefix`.
SERVICES = [
    ("netflix", "Netflix", "netflix"),
    ("hbomax", "HBO Max", "hbomax"),
    ("appletv", "Apple TV", "appletv"),
    ("prime", "Prime Video", "amazonprimevideo"),
    ("disney", "Disney+", "disneyplus"),
    ("hulu", "Hulu", "hulu"),
    ("peacock", "Peacock", "peacock"),
    ("paramount", "Paramount+", "paramountplus"),
]

tmdb: TMDB
db: DB
_pools: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tmdb, db
    tmdb = TMDB()
    db = DB()
    yield
    await tmdb.close()
    db.close()


app = FastAPI(title="Movie Picker", lifespan=lifespan)


@app.middleware("http")
async def revalidate_static(request, call_next):
    # Make browsers re-check the page files so edits show up on reload.
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(TMDBError)
async def tmdb_error(_, exc: TMDBError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("+", "plus"))


async def service_catalog() -> list[dict]:
    data = await tmdb.get("/watch/providers/movie", watch_region=REGION)
    providers = sorted(data.get("results", []),
                       key=lambda p: p.get("display_priorities", {}).get(REGION, 999))
    catalog = []
    for key, label, prefix in SERVICES:
        # Skip add-on channels ("HBO Max Amazon Channel") and stores ("Apple TV Store").
        group = [p for p in providers if _norm(p["provider_name"]).startswith(prefix)
                 and not re.search(r"channel|store", p["provider_name"], re.I)]
        if group:
            catalog.append({"key": key, "name": label,
                            "logo": image_url(group[0].get("logo_path"), "w92"),
                            "provider_ids": [p["provider_id"] for p in group]})
    return catalog


async def genre_names() -> dict[int, str]:
    data = await tmdb.get("/genre/movie/list")
    return {g["id"]: g["name"] for g in data.get("genres", [])}


def _movie(m: dict) -> dict:
    date = m.get("release_date") or ""
    return {
        "id": m["id"],
        "title": m.get("title"),
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "overview": m.get("overview") or "",
        "poster": image_url(m.get("poster_path")),
        "rating": m.get("vote_average"),
        "votes": m.get("vote_count"),
        "popularity": m.get("popularity"),
        "genre_ids": m.get("genre_ids", []),
    }


async def build_pool(provider_ids: list[int], genre_ids: list[int]) -> dict:
    """Candidate movies on the user's services + their feature vectors (cached 1h)."""
    key = (tuple(sorted(provider_ids)), tuple(sorted(genre_ids)))
    hit = _pools.get(key)
    if hit and hit["expires"] > time.time():
        return hit

    params = {
        "watch_region": REGION,
        "with_watch_providers": "|".join(map(str, provider_ids)),
        "with_watch_monetization_types": "flatrate",
        "with_genres": "|".join(map(str, genre_ids)) or None,  # any of the chosen genres
        "sort_by": "popularity.desc",
        "vote_count.gte": 150,
        "include_adult": "false",
    }
    pages = await asyncio.gather(*(tmdb.get("/discover/movie", page=p, **params)
                                   for p in range(1, POOL_PAGES + 1)))
    movies, seen = [], set()
    for page in pages:
        for m in page.get("results", []):
            if m["id"] not in seen:
                seen.add(m["id"])
                movies.append(_movie(m))

    keyword_lists = await asyncio.gather(*(tmdb.get(f"/movie/{m['id']}/keywords") for m in movies))
    for m, kws in zip(movies, keyword_lists):
        m["keywords"] = [k["name"] for k in kws.get("keywords", [])]

    space = recommender.build_space(movies, await genre_names()) if movies else None
    vectors = {m["id"]: recommender.vectorize(m, space) for m in movies}
    pool = {"movies": movies, "by_id": {m["id"]: m for m in movies}, "space": space,
            "vectors": vectors, "names": space["names"] if space else [],
            "expires": time.time() + 3600}
    _pools[key] = pool
    return pool


def _card(m: dict) -> dict:
    keys = ("id", "title", "year", "poster", "rating")
    card = {k: m[k] for k in keys}
    card["overview"] = m["overview"][:220] + ("…" if len(m["overview"]) > 220 else "")
    card["tmdb_url"] = f"https://www.themoviedb.org/movie/{m['id']}"
    return card


@app.get("/api/health")
async def health():
    # Used by the stream-search skill to tell this app apart from anything else on the port.
    return {"ok": True, "app": "movie-picker"}


# ---------- Profiles (name + icon, no password) ----------

def current_user(request: Request) -> dict:
    uid = request.cookies.get(COOKIE, "")
    user = db.user(int(uid)) if uid.isdigit() else None
    if not user:
        raise HTTPException(401, "Pick or create a profile first.")
    return user


def _login(response: Response, user: dict) -> dict:
    response.set_cookie(COOKIE, str(user["id"]), max_age=365 * 24 * 3600,
                        httponly=True, samesite="lax")
    return user


class NewUser(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    icon: str


@app.get("/api/users")
async def list_users():
    return {"users": db.users(), "icons": ICONS}


@app.post("/api/users", status_code=201)
async def create_user(body: NewUser, response: Response):
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(422, "Enter a name.")
    if body.icon not in ICONS:
        raise HTTPException(422, "Pick one of the icons.")
    try:
        user = db.create_user(name, body.icon)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"There's already a profile called {name}.")
    return _login(response, user)


class Login(BaseModel):
    user_id: int


@app.post("/api/login")
async def login(body: Login, response: Response):
    user = db.user(body.user_id)
    if not user:
        raise HTTPException(404, "That profile doesn't exist.")
    return _login(response, user)


@app.post("/api/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(COOKIE)


@app.get("/api/me")
async def me(user: dict = Depends(current_user)):
    return {**user, "past_answers": db.answer_count(user["id"])}


@app.get("/api/me/history")
async def my_history(user: dict = Depends(current_user)):
    return db.history(user["id"])


class SeenMovie(BaseModel):
    movie_id: int
    title: str | None = None
    poster: str | None = None


@app.get("/api/me/seen")
async def my_seen(user: dict = Depends(current_user)):
    return db.seen(user["id"])


@app.post("/api/me/seen", status_code=204)
async def add_seen(movies: list[SeenMovie], user: dict = Depends(current_user)):
    db.add_seen(user["id"], [m.model_dump() for m in movies])


@app.delete("/api/me/seen", status_code=204)
async def clear_seen(user: dict = Depends(current_user)):
    db.remove_seen(user["id"])


@app.delete("/api/me/seen/{movie_id}", status_code=204)
async def remove_seen(movie_id: int, user: dict = Depends(current_user)):
    db.remove_seen(user["id"], movie_id)


# ---------- Quiz ----------

@app.get("/api/services")
async def get_services():
    return [{k: s[k] for k in ("key", "name", "logo")} for s in await service_catalog()]


@app.get("/api/genres")
async def get_genres():
    names = await genre_names()
    return [{"id": i, "name": n} for i, n in sorted(names.items(), key=lambda x: x[1])]


class Pick(BaseModel):
    a: int
    b: int
    choice: Literal["a", "b", "tie", "skip"]


class NextRequest(BaseModel):
    services: list[str] = Field(min_length=1)
    genres: list[int] = []
    history: list[Pick] = []
    finish: bool = False
    # Results paging: movies already on the results page (excluded, and their
    # series too), movies the user has already seen (excluded), and how many to return.
    displayed: list[int] = []
    limit: int = Field(RESULTS, ge=1, le=20)
    quiz_id: int | None = None   # from POST /api/quizzes; answers and results are saved to it


class NewQuiz(BaseModel):
    services: list[str] = Field(min_length=1)
    genres: list[int] = []
    use_history: bool = True     # start from what past quizzes taught


@app.post("/api/quizzes", status_code=201)
async def start_quiz(body: NewQuiz, user: dict = Depends(current_user)):
    names = await genre_names()
    db.set_services(user["id"], body.services)
    quiz_id = db.create_quiz(user["id"], body.services,
                             [{"id": g, "name": names.get(g, str(g))} for g in body.genres],
                             body.use_history)
    return {"quiz_id": quiz_id}


@app.post("/api/next")
async def next_step(req: NextRequest, user: dict = Depends(current_user)):
    if req.quiz_id is not None and db.quiz_owner(req.quiz_id) != user["id"]:
        raise HTTPException(404, "That quiz doesn't belong to this profile.")
    catalog = {s["key"]: s for s in await service_catalog()}
    chosen = [catalog[k] for k in req.services if k in catalog]
    if not chosen:
        raise HTTPException(422, "Pick at least one streaming service.")
    provider_ids = [pid for s in chosen for pid in s["provider_ids"]]

    pool = await build_pool(provider_ids, req.genres)
    if len(pool["movies"]) < MIN_POOL:
        raise HTTPException(422, "Not enough movies match. Try adding a service or another genre.")

    history = [h.model_dump() for h in req.history]
    vectors, names = pool["vectors"], pool["names"]
    prior, learned = _prior(user["id"], req.quiz_id, pool)
    w = recommender.replay(history, vectors, len(names), w=prior)
    shown = {h["a"] for h in history} | {h["b"] for h in history}
    rounds = _rounds(learned["answers"])

    if req.quiz_id is not None:
        title = lambda i: (pool["by_id"].get(i) or {}).get("title")
        db.save_movies([pool["by_id"][i] for i in shown if i in pool["by_id"]])
        db.save_answers(req.quiz_id, [
            {"round": n, "movie_a": h["a"], "title_a": title(h["a"]),
             "movie_b": h["b"], "title_b": title(h["b"]), "choice": h["choice"]}
            for n, h in enumerate(history, start=1)])

    if not req.finish and len(history) < rounds:
        # Seed from the history so the same answers always give the same next pair.
        seed = hashlib.sha256(repr((req.services, req.genres, history)).encode()).hexdigest()
        pair = recommender.choose_pair(pool["movies"], vectors, w, shown,
                                       len(history), random.Random(seed),
                                       min_year=datetime.date.today().year - QUESTION_MAX_AGE)
        if pair:
            return {"done": False, "round": len(history) + 1, "rounds": rounds,
                    "learned": learned, "pair": [_card(pair[0]), _card(pair[1])]}

    liked = {}
    for h in history:
        for side in (("a",) if h["choice"] == "a" else ("b",) if h["choice"] == "b"
                     else ("a", "b") if h["choice"] == "tie" else ()):
            movie = pool["by_id"].get(h[side])
            if movie:
                liked[movie["id"]] = movie["title"]
    result = await _recommendations(pool, w, shown, chosen, liked,
                                    set(req.displayed), db.seen_ids(user["id"]), req.limit)
    if req.quiz_id is not None:
        db.save_recommendations(req.quiz_id, result["taste"], result["recommendations"])
    return {"done": True, **result}


def _rounds(past_answers: int) -> int:
    """10 questions with no history, then fewer: 8 at 10 past answers, 5 at 20+."""
    if past_answers < 10:
        return ROUNDS
    return max(MIN_ROUNDS, ROUNDS - past_answers // 4)


def _prior(user_id: int, quiz_id: int | None, pool: dict) -> tuple[list[float] | None, dict]:
    """Starting taste from the user's past quizzes, in this pool's feature space.

    Past movies outside tonight's pool are rebuilt from the saved movie details,
    so a Netflix sci-fi quiz still informs an HBO Max comedy one through shared
    features (decade, rating, keywords, overlapping genres)."""
    none = {"quizzes": 0, "answers": 0}
    quiz = db.quiz(quiz_id) if quiz_id is not None else None
    if quiz and not quiz["use_history"]:
        return None, none
    past, _ = db.past_answers(user_id, exclude_quiz=quiz_id, limit=PAST_LIMIT)
    if not past or not pool["space"]:
        return None, none

    vectors = dict(pool["vectors"])
    missing = {i for h in past for i in (h["a"], h["b"])} - vectors.keys()
    for movie_id, movie in db.movies(missing).items():
        vectors[movie_id] = recommender.vectorize(movie, pool["space"])
    usable = [h for h in past if h["a"] in vectors and h["b"] in vectors]
    if not usable:
        return None, none
    w = recommender.replay(usable, vectors, len(pool["names"]), lr=PAST_WEIGHT)
    return w, {"quizzes": len({h["quiz_id"] for h in usable}), "answers": len(usable)}


async def _details(movie_id: int) -> dict:
    return await tmdb.get(f"/movie/{movie_id}", append_to_response="watch/providers")


async def _recommendations(pool: dict, w: list[float], shown: set[int], chosen: list[dict],
                           liked: dict[int, str], displayed: set[int], seen: set[int],
                           limit: int) -> dict:
    vectors, names = pool["vectors"], pool["names"]
    candidates = recommender.recommend(pool["movies"], vectors, w, shown | displayed | seen,
                                       n=RESULT_CANDIDATES)
    # Match % is relative to everything available (minus quiz movies), so it
    # stays the same for a movie whether it shows up on page 1 or page 3.
    match = recommender.match_percentiles(pool["movies"], vectors, w, shown)

    # One request per candidate gets both its series ("collection") and where it
    # streams now. Keep the best match from each series, and only movies that are
    # on one of the user's services.
    provider_to_service = {pid: s["name"] for s in chosen for pid in s["provider_ids"]}
    details = await asyncio.gather(*(_details(m["id"]) for m in candidates))
    # Series already on the page stay blocked when loading more. These lookups
    # hit the cache, since those movies were fetched for the earlier page.
    on_page = await asyncio.gather(*(_details(i) for i in displayed if i in pool["by_id"]))
    series_used = {(info.get("belongs_to_collection") or {}).get("id") for info in on_page} - {None}
    results = []
    for m, info in zip(candidates, details):
        flatrate = info.get("watch/providers", {}).get("results", {}).get(REGION, {}).get("flatrate", [])
        on = sorted({provider_to_service[p["provider_id"]] for p in flatrate
                     if p["provider_id"] in provider_to_service})
        if not on:
            continue
        series = (info.get("belongs_to_collection") or {}).get("id")
        if series in series_used:
            continue
        if series:
            series_used.add(series)
        card = _card(m)
        card["services"] = on
        card["match"] = match.get(m["id"])
        card["reasons"] = recommender.explain(vectors[m["id"]], w, names)
        card["why"] = recommender.why_sentence(m["id"], vectors, w, names, liked)
        results.append(card)
        if len(results) == limit:
            break
    if match:
        results.sort(key=lambda c: c["match"], reverse=True)
    return {"taste": recommender.taste_summary(w, names), "recommendations": results,
            "more": len(results) == limit}


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True), name="static")
