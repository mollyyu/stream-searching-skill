"""API tests against a fake TMDB, so they run without a key or network."""

import random

import pytest
from fastapi.testclient import TestClient

from app import main

GENRES = [{"id": 878, "name": "Science Fiction"}, {"id": 35, "name": "Comedy"}, {"id": 18, "name": "Drama"}]
PROVIDERS = [
    {"provider_id": 8, "provider_name": "Netflix", "logo_path": "/n.png", "display_priorities": {"US": 1}},
    {"provider_id": 1796, "provider_name": "Netflix Standard with Ads", "display_priorities": {"US": 30}},
    {"provider_id": 1899, "provider_name": "HBO Max", "logo_path": "/m.png", "display_priorities": {"US": 3}},
    {"provider_id": 1825, "provider_name": "HBO Max Amazon Channel", "display_priorities": {"US": 40}},
    {"provider_id": 2, "provider_name": "Apple TV Store", "display_priorities": {"US": 9}},
]


class FakeTMDB:
    def __init__(self):
        rng = random.Random(0)
        self.movies = [{"id": i, "title": f"Movie {i}", "release_date": f"{rng.randint(1980, 2024)}-01-01",
                        "overview": "x" * 300, "poster_path": f"/{i}.jpg", "vote_average": 7.0,
                        "vote_count": 1000, "popularity": rng.uniform(1, 100),
                        "genre_ids": [rng.choice([878, 35, 18])]} for i in range(60)]
        self.calls = []

    async def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/watch/providers/movie":
            return {"results": PROVIDERS}
        if path == "/genre/movie/list":
            return {"genres": GENRES}
        if path == "/discover/movie":
            page = params["page"]
            return {"results": self.movies[(page - 1) * 20: page * 20]}
        if path.endswith("/keywords"):
            return {"keywords": [{"name": "space"}] if int(path.split("/")[2]) % 2 else []}
        if path.startswith("/movie/") and params.get("append_to_response") == "watch/providers":
            movie_id = int(path.split("/")[2])
            provider = 8 if movie_id % 3 else 1899
            # Even ids belong to one of two series, to test one-per-series.
            series = {"id": 100 + movie_id % 4} if movie_id % 2 == 0 else None
            return {"belongs_to_collection": series,
                    "watch/providers": {"results": {"US": {"flatrate": [{"provider_id": provider}]}}}}
        raise AssertionError(path)

    async def close(self):
        pass


@pytest.fixture
def anon(monkeypatch, tmp_path):
    """A client with a fresh database and fake TMDB, not logged in."""
    monkeypatch.setenv("MOVIE_PICKER_DB", str(tmp_path / "test.db"))
    fake = FakeTMDB()
    main._pools.clear()
    with TestClient(main.app) as c:
        monkeypatch.setattr(main, "tmdb", fake)
        c.fake = fake
        yield c


@pytest.fixture
def client(anon):
    """Logged in as a fresh profile."""
    assert anon.post("/api/users", json={"name": "Tester", "icon": "🍿"}).status_code == 201
    return anon


def test_services_merge_tiers_and_skip_channels(client):
    services = client.get("/api/services").json()
    assert [s["name"] for s in services] == ["Netflix", "HBO Max"]


def test_full_quiz_to_recommendations(client):
    history = []
    for r in range(main.ROUNDS):
        data = client.post("/api/next", json={"services": ["netflix", "hbomax"], "genres": [878],
                                              "history": history}).json()
        assert data["done"] is False and data["round"] == r + 1
        a, b = data["pair"]
        assert a["overview"].endswith("…") and a["poster"].startswith("https://image.tmdb.org/")
        history.append({"a": a["id"], "b": b["id"], "choice": "a"})

    data = client.post("/api/next", json={"services": ["netflix", "hbomax"], "genres": [878],
                                          "history": history}).json()
    assert data["done"] is True
    recs = data["recommendations"]
    assert 0 < len(recs) <= 10
    shown = {h["a"] for h in history} | {h["b"] for h in history}
    assert not shown & {m["id"] for m in recs}
    assert all(set(m["services"]) <= {"Netflix", "HBO Max"} for m in recs)
    # Ranked by match, with a percentage and a taste summary.
    matches = [m["match"] for m in recs]
    assert matches == sorted(matches, reverse=True) and all(0 <= x <= 100 for x in matches)
    assert data["taste"] and all(m["why"].endswith(".") for m in recs)
    # At most one movie per series (even ids are in series 100 or 102).
    assert sum(1 for m in recs if m["id"] % 2 == 0) <= 2

    discover = [p for path, p in client.fake.calls if path == "/discover/movie"][0]
    assert discover["with_watch_providers"] == "8|1796|1899"
    assert discover["with_genres"] == "878"


def test_only_recommends_on_chosen_service(client):
    data = client.post("/api/next", json={"services": ["netflix"], "finish": True}).json()
    assert data["done"] and all(m["services"] == ["Netflix"] for m in data["recommendations"])


def test_same_answers_give_same_next_pair(client):
    body = {"services": ["netflix"], "history": []}
    assert client.post("/api/next", json=body).json() == client.post("/api/next", json=body).json()


def test_rejects_unknown_service(client):
    assert client.post("/api/next", json={"services": ["blockbuster"]}).status_code == 422


def test_missing_key_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("MOVIE_PICKER_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TMDB_READ_ACCESS_TOKEN", raising=False)
    main._pools.clear()
    with TestClient(main.app) as c:
        res = c.get("/api/services")
    assert res.status_code == 503 and "TMDB_API_KEY" in res.json()["detail"]


def test_accepts_tie_answers(client):
    first = client.post("/api/next", json={"services": ["netflix"]}).json()
    a, b = first["pair"]
    res = client.post("/api/next", json={"services": ["netflix"],
                                         "history": [{"a": a["id"], "b": b["id"], "choice": "tie"}]})
    assert res.status_code == 200 and res.json()["round"] == 2
    assert client.post("/api/next", json={"services": ["netflix"], "history": [
        {"a": a["id"], "b": b["id"], "choice": "both"}]}).status_code == 422


def _finish(client, **extra):
    body = {"services": ["netflix", "hbomax"], "finish": True,
            "history": [{"a": 1, "b": 2, "choice": "a"}], **extra}
    res = client.post("/api/next", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_show_more_returns_new_movies_without_repeating_series(client):
    first = _finish(client)
    page1 = [m["id"] for m in first["recommendations"]]
    second = _finish(client, displayed=page1)
    page2 = [m["id"] for m in second["recommendations"]]
    assert page2 and not set(page1) & set(page2)
    # Even ids belong to series 100 or 102: at most one of each across both pages.
    assert sum(1 for i in page1 + page2 if i % 2 == 0) <= 2


def test_seen_movies_are_never_recommended(client):
    first = [m["id"] for m in _finish(client)["recommendations"]]
    client.post("/api/me/seen", json=[{"movie_id": i} for i in first[:3]])
    again = [m["id"] for m in _finish(client)["recommendations"]]
    assert not set(first[:3]) & set(again)


def test_replacement_for_a_seen_movie(client):
    first = [m["id"] for m in _finish(client)["recommendations"]]
    client.post("/api/me/seen", json=[{"movie_id": first[0], "title": "x"}])
    one = _finish(client, displayed=first[1:], limit=1)["recommendations"]
    assert len(one) == 1 and one[0]["id"] not in first


def test_more_flag_turns_false_when_pool_runs_out(client):
    displayed, pages = [], 0
    while True:
        data = _finish(client, displayed=displayed)
        displayed += [m["id"] for m in data["recommendations"]]
        pages += 1
        if not data["more"]:
            break
        assert pages < 10
    assert len(displayed) == len(set(displayed))


# ---------- profiles and saved data ----------

def test_quiz_needs_a_profile(anon):
    assert anon.post("/api/next", json={"services": ["netflix"]}).status_code == 401
    assert anon.get("/api/me").status_code == 401


def test_create_list_login_and_switch_profiles(anon):
    me = anon.post("/api/users", json={"name": "  Molly  ", "icon": "🦖"}).json()
    assert me["name"] == "Molly" and anon.get("/api/me").json()["id"] == me["id"]
    assert anon.post("/api/users", json={"name": "molly", "icon": "🍿"}).status_code == 409
    assert anon.post("/api/users", json={"name": "Sam", "icon": "not-an-icon"}).status_code == 422
    sam = anon.post("/api/users", json={"name": "Sam", "icon": "🐱"}).json()
    assert [u["name"] for u in anon.get("/api/users").json()["users"]] == ["Molly", "Sam"]
    assert anon.get("/api/me").json()["id"] == sam["id"]
    anon.post("/api/login", json={"user_id": me["id"]})
    assert anon.get("/api/me").json()["name"] == "Molly"
    anon.post("/api/logout")
    assert anon.get("/api/me").status_code == 401


def test_quiz_answers_and_picks_are_saved_to_history(client):
    quiz_id = client.post("/api/quizzes", json={"services": ["netflix"], "genres": [878]}).json()["quiz_id"]
    assert client.get("/api/me").json()["services"] == ["netflix"]
    body = {"services": ["netflix"], "genres": [878], "quiz_id": quiz_id, "history": []}
    for choice in ["a", "b", "tie", "skip"]:
        a, b = client.post("/api/next", json=body).json()["pair"]
        body["history"].append({"a": a["id"], "b": b["id"], "choice": choice})
    picks = client.post("/api/next", json={**body, "finish": True}).json()["recommendations"]
    # Resending the same history (e.g. "Show more") must not duplicate answers.
    client.post("/api/next", json={**body, "finish": True, "displayed": [m["id"] for m in picks]})

    [quiz] = client.get("/api/me/history").json()
    assert quiz["genres"] == [{"id": 878, "name": "Science Fiction"}] and quiz["finished_at"]
    assert [a["choice"] for a in quiz["answers"]] == ["a", "b", "tie", "skip"]
    assert quiz["answers"][0]["title_a"].startswith("Movie ")
    assert len(quiz["recommendations"]) >= len(picks) and quiz["taste"]


def test_history_and_seen_are_per_profile(client):
    client.post("/api/me/seen", json=[{"movie_id": 5, "title": "Movie 5"}])
    client.post("/api/quizzes", json={"services": ["netflix"]})
    assert [m["movie_id"] for m in client.get("/api/me/seen").json()] == [5]
    client.post("/api/users", json={"name": "Other", "icon": "🐼"})
    assert client.get("/api/me/seen").json() == [] and client.get("/api/me/history").json() == []


def test_cannot_write_to_someone_elses_quiz(client):
    quiz_id = client.post("/api/quizzes", json={"services": ["netflix"]}).json()["quiz_id"]
    client.post("/api/users", json={"name": "Intruder", "icon": "👻"})
    res = client.post("/api/next", json={"services": ["netflix"], "quiz_id": quiz_id})
    assert res.status_code == 404


def test_clear_and_remove_seen(client):
    client.post("/api/me/seen", json=[{"movie_id": 1}, {"movie_id": 2}, {"movie_id": 2}])
    client.delete("/api/me/seen/1")
    assert [m["movie_id"] for m in client.get("/api/me/seen").json()] == [2]
    client.delete("/api/me/seen")
    assert client.get("/api/me/seen").json() == []


# ---------- learning from past quizzes ----------

def _play_quiz(client, choices, genres=(), use_history=True):
    quiz_id = client.post("/api/quizzes", json={"services": ["netflix", "hbomax"], "genres": list(genres),
                                                "use_history": use_history}).json()["quiz_id"]
    body = {"services": ["netflix", "hbomax"], "genres": list(genres), "quiz_id": quiz_id, "history": []}
    first = data = client.post("/api/next", json=body).json()
    for choice in choices:
        if data["done"]:
            break
        a, b = data["pair"]
        body["history"].append({"a": a["id"], "b": b["id"], "choice": choice})
        data = client.post("/api/next", json=body).json()
    return quiz_id, first, client.post("/api/next", json={**body, "finish": True}).json()


def test_past_quizzes_shorten_the_next_one_and_shape_results(client):
    _, first, _ = _play_quiz(client, ["a"] * 10)
    assert first["rounds"] == 10 and first["learned"] == {"quizzes": 0, "answers": 0}
    assert client.get("/api/me").json()["past_answers"] == 10

    quiz_id, first, _ = _play_quiz(client, [])
    assert first["rounds"] == 8 and first["learned"] == {"quizzes": 1, "answers": 10}
    # Finishing straight away still gives a taste, learned from the past quiz.
    instant = client.post("/api/next", json={"services": ["netflix", "hbomax"], "quiz_id": quiz_id,
                                             "finish": True}).json()
    assert instant["taste"]


def test_opting_out_of_history_starts_fresh(client):
    _play_quiz(client, ["a"] * 10)
    quiz_id, first, _ = _play_quiz(client, [], use_history=False)
    assert first["rounds"] == 10 and first["learned"]["answers"] == 0
    instant = client.post("/api/next", json={"services": ["netflix", "hbomax"], "quiz_id": quiz_id,
                                             "finish": True}).json()
    assert instant["taste"] == []


def test_skips_dont_count_as_past_answers(client):
    _play_quiz(client, ["skip"] * 10)
    assert client.get("/api/me").json()["past_answers"] == 0


def test_past_movies_outside_tonights_pool_still_count(client):
    _play_quiz(client, ["a"] * 10)
    # Tonight's pool no longer contains the movies compared last time.
    pool = next(iter(main._pools.values()))
    past = main.db.past_answers(client.get("/api/me").json()["id"])[0]
    compared = {i for h in past for i in (h["a"], h["b"])}
    trimmed = {**pool, "vectors": {i: v for i, v in pool["vectors"].items() if i not in compared}}
    w, learned = main._prior(client.get("/api/me").json()["id"], None, trimmed)
    assert learned["answers"] == 10 and any(w)


def test_old_database_files_get_new_columns(tmp_path):
    import sqlite3
    from app.db import DB
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE quizzes (id INTEGER PRIMARY KEY, user_id INTEGER, services TEXT,"
                " genres TEXT, taste TEXT, started_at TEXT, finished_at TEXT)")
    old.commit()
    old.close()
    db = DB(path)
    assert "use_history" in {r["name"] for r in db.conn.execute("PRAGMA table_info(quizzes)")}
    db.save_movies([{"id": 7, "title": "X", "year": 2020, "genre_ids": [1], "keywords": ["k"]}])
    assert db.movies({7})[7]["keywords"] == ["k"]
