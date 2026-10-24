"""The stream-search skill reads this app's database through
stream-search/scripts/movie_picker.py. These tests fill a database with the
app's own code and check the skill script reads it correctly, so a schema
change here that would break the skill fails in this repo's tests."""

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app.db import DB

SCRIPT = Path(__file__).resolve().parents[2] / "stream-search" / "scripts" / "movie_picker.py"
# Claude runs skill scripts with whatever `python3` is on PATH, which may be
# the macOS system Python 3.9, so test that too when it's there.
PYTHONS = [sys.executable] + [p for p in ["/usr/bin/python3"] if Path(p).exists()]


def run(python, db_path, *args):
    env = {"MOVIE_PICKER_DB": str(db_path), "PATH": "/usr/bin:/bin"}
    res = subprocess.run([python, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30)
    return res.returncode, json.loads(res.stdout)


@pytest.fixture
def filled_db(tmp_path):
    path = tmp_path / "app.db"
    db = DB(path)
    molly = db.create_user("Molly", "🦖")
    db.set_services(molly["id"], ["netflix", "hbomax"])
    db.create_user("Sam", "🐱")

    q1 = db.create_quiz(molly["id"], ["netflix"], [{"id": 878, "name": "Science Fiction"}])
    db.save_answers(q1, [
        {"round": 1, "movie_a": 1, "title_a": "Dune", "movie_b": 2, "title_b": "Barbie", "choice": "a"},
        {"round": 2, "movie_a": 3, "title_a": "Alien", "movie_b": 4, "title_b": "Arrival", "choice": "tie"},
        {"round": 3, "movie_a": 5, "title_a": "Cats", "movie_b": 6, "title_b": "Heat", "choice": "skip"},
    ])
    card = lambda i, t, m: {"id": i, "title": t, "year": 2020, "poster": None, "match": m,
                            "why": f"Because {t}.", "services": ["Netflix"]}
    db.save_recommendations(q1, ["Science Fiction", "space"], [card(10, "Interstellar", 99), card(11, "Gravity", 90)])
    q2 = db.create_quiz(molly["id"], ["hbomax"], [], use_history=False)
    db.save_recommendations(q2, ["Science Fiction", "2010s"], [card(12, "Edge of Tomorrow", 95)])
    db.add_seen(molly["id"], [{"movie_id": 11, "title": "Gravity"}])
    db.close()
    return path


@pytest.mark.parametrize("python", PYTHONS)
def test_profiles_lists_everyone_with_counts(python, filled_db):
    code, out = run(python, filled_db, "profiles")
    assert code == 0
    molly = next(p for p in out if p["name"] == "Molly")
    assert molly["services"] == ["netflix", "hbomax"]
    assert (molly["quizzes"], molly["answers"], molly["seen"]) == (2, 2, 1)  # skips don't count
    assert [p["name"] for p in out] == ["Molly", "Sam"]


@pytest.mark.parametrize("python", PYTHONS)
def test_profile_has_taste_likes_seen_and_recommendations(python, filled_db):
    code, out = run(python, filled_db, "profile", "molly")  # case-insensitive
    assert code == 0
    assert out["profile"]["icon"] == "🦖"
    assert out["overall_taste"][0] == "Science Fiction"
    assert [m["title"] for m in out["liked_in_quizzes"]] == ["Alien", "Arrival", "Dune"]
    assert [m["title"] for m in out["seen"]] == ["Gravity"]
    # Unseen first (newest quiz first), seen ones last and flagged.
    assert [(m["title"], m["seen"]) for m in out["recommendations"]] == [
        ("Edge of Tomorrow", False), ("Interstellar", False), ("Gravity", True)]
    assert out["recommendations"][0]["why"] == "Because Edge of Tomorrow."
    newest = out["recent_quizzes"][0]
    assert newest["used_past_answers"] is False and out["recent_quizzes"][1]["choices"] == {"a": 1, "tie": 1, "skip": 1}


def test_unknown_profile_and_missing_database(filled_db, tmp_path):
    code, out = run(sys.executable, filled_db, "profile", "Nobody")
    assert code == 1 and "Molly" in out["error"] and "Sam" in out["error"]
    code, out = run(sys.executable, tmp_path / "missing.db", "profiles")
    assert code == 0 and out == []


def test_reading_never_changes_the_database(filled_db):
    before = filled_db.read_bytes()
    run(sys.executable, filled_db, "profile", "Molly")
    run(sys.executable, filled_db, "profiles")
    assert filled_db.read_bytes() == before


def test_status_when_nothing_is_running(filled_db):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    code, out = run(sys.executable, filled_db, "status", "--port", str(free_port))
    assert code == 0 and out["running"] is False and out["profiles"] == 2
