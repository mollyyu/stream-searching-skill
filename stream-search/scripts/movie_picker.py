#!/usr/bin/env python3
"""Bridge between the stream-search skill and the Movie Picker web app.

Commands:
  start [--port 8000]   Start the app in the background if it isn't running,
                        wait until it answers, and print its URL.
  status [--port 8000]  Is it running, where, and how much data is there.
  stop                  Stop an app that this script started.
  profiles              List profiles with quiz / answer / seen counts.
  profile NAME [--quizzes 5] [--limit 30]
                        One profile's services, overall taste, liked movies,
                        seen list, recent quizzes, and past recommendations
                        (unseen first, newest first).

Profile data is read straight from the app's SQLite file with writes
disabled (PRAGMA query_only), so it works whether or not the app is running
and can't change anything.
Output is JSON. Standard library only; runs on Python 3.9+.

Where things are (override with environment variables):
  MOVIE_PICKER_DIR  the movie-picker folder (default: ../../movie-picker from this file)
  MOVIE_PICKER_DB   the database file (default: <MOVIE_PICKER_DIR>/data/moviepicker.db)
"""

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Optional

# resolve() follows a symlinked skill install back to the repo.
APP_DIR = Path(os.environ.get("MOVIE_PICKER_DIR")
               or Path(__file__).resolve().parents[2] / "movie-picker")
DB_PATH = Path(os.environ.get("MOVIE_PICKER_DB") or APP_DIR / "data" / "moviepicker.db")
PID_FILE = APP_DIR / "data" / "server.pid"
LOG_FILE = APP_DIR / "data" / "server.log"
START_TIMEOUT = 90  # the first start may install dependencies


class BridgeError(Exception):
    pass


# ---------- running the app ----------

def health(port: int) -> str:
    """'ours' if Movie Picker answers on the port, 'other' if something else does, 'none' if nothing."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            data = json.load(resp)
        return "ours" if data.get("app") == "movie-picker" else "other"
    except urllib.error.HTTPError:
        return "other"
    except (urllib.error.URLError, OSError, ValueError):
        return "none"


def find_uv() -> Optional[str]:
    for candidate in (shutil.which("uv"), Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv"):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def start(port: int) -> dict:
    url = f"http://localhost:{port}"
    state = health(port)
    if state == "ours":
        return {"running": True, "started": False, "url": url}
    if state == "other":
        raise BridgeError(f"Port {port} is used by another program. Try: start --port {port + 1}")
    if not (APP_DIR / "pyproject.toml").exists():
        raise BridgeError(f"Movie Picker not found at {APP_DIR}. Set MOVIE_PICKER_DIR.")
    if not (os.environ.get("TMDB_API_KEY") or os.environ.get("TMDB_READ_ACCESS_TOKEN")):
        raise BridgeError("TMDB_API_KEY is not set in this shell. Run with `source ~/.zshrc && ...` "
                          "or export the key first.")
    uv = find_uv()
    if not uv:
        raise BridgeError("uv is not installed (https://docs.astral.sh/uv/).")

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if os.environ.get("MOVIE_PICKER_DB"):
        env["MOVIE_PICKER_DB"] = str(DB_PATH)
    with open(LOG_FILE, "ab") as log:
        # A new session, so the server keeps running after this script exits
        # and `stop` can end uv and uvicorn together.
        proc = subprocess.Popen(
            [uv, "run", "uvicorn", "app.main:app", "--port", str(port)],
            cwd=APP_DIR, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env=env, start_new_session=True)
    PID_FILE.write_text(f"{proc.pid} {port}")

    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if health(port) == "ours":
            return {"running": True, "started": True, "url": url, "log": str(LOG_FILE)}
        if proc.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            tail = LOG_FILE.read_text(errors="replace").splitlines()[-15:]
            raise BridgeError("The app exited while starting. Last log lines:\n" + "\n".join(tail))
        time.sleep(0.5)
    raise BridgeError(f"The app didn't answer within {START_TIMEOUT}s. See {LOG_FILE}.")


def stop() -> dict:
    if not PID_FILE.exists():
        return {"stopped": False, "reason": "No app started by this script is running."}
    pid, port = (int(x) for x in PID_FILE.read_text().split())
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    PID_FILE.unlink(missing_ok=True)
    for _ in range(20):
        if health(port) == "none":
            break
        time.sleep(0.25)
    return {"stopped": True, "port": port}


def status(port: int) -> dict:
    state = health(port)
    out = {"running": state == "ours", "url": f"http://localhost:{port}" if state == "ours" else None,
           "port_in_use_by_other_program": state == "other",
           "started_by_skill": PID_FILE.exists(), "database": str(DB_PATH),
           "database_exists": DB_PATH.exists()}
    if DB_PATH.exists():
        out["profiles"] = len(profiles())
    return out


# ---------- reading the app's data ----------

def connect() -> Optional[sqlite3.Connection]:
    if not DB_PATH.exists():
        return None
    # Not mode=ro: the app uses WAL mode, and a read-only connection can't create
    # the shared-memory file WAL needs when the app isn't running. query_only
    # makes SQLite refuse any write on this connection instead.
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def profiles() -> list:
    conn = connect()
    if conn is None:
        return []
    rows = conn.execute("""
        SELECT u.id, u.name, u.icon, u.services,
               (SELECT COUNT(*) FROM quizzes q WHERE q.user_id = u.id) AS quizzes,
               (SELECT COUNT(*) FROM answers a JOIN quizzes q ON q.id = a.quiz_id
                 WHERE q.user_id = u.id AND a.choice != 'skip') AS answers,
               (SELECT COUNT(*) FROM seen s WHERE s.user_id = u.id) AS seen,
               (SELECT MAX(started_at) FROM quizzes q WHERE q.user_id = u.id) AS last_quiz
        FROM users u ORDER BY u.name""").fetchall()
    return [{**dict(r), "services": _json(r["services"], [])} for r in rows]


def profile(name: str, quizzes: int = 5, limit: int = 30) -> dict:
    conn = connect()
    if conn is None:
        raise BridgeError("No Movie Picker data yet. Start the app and take a quiz first.")
    user = conn.execute("SELECT * FROM users WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if not user:
        names = [p["name"] for p in profiles()]
        raise BridgeError(f"No profile called '{name}'. Profiles: {', '.join(names) or 'none'}.")
    uid = user["id"]

    all_quizzes = [dict(r) for r in conn.execute(
        "SELECT * FROM quizzes WHERE user_id = ? ORDER BY id DESC", (uid,))]
    taste_counts = Counter(t for q in all_quizzes for t in _json(q.get("taste"), []))

    recent = []
    for q in all_quizzes[:quizzes]:
        answers = conn.execute("SELECT choice FROM answers WHERE quiz_id = ?", (q["id"],)).fetchall()
        recent.append({
            "date": q["started_at"], "finished": bool(q["finished_at"]),
            "services": _json(q["services"], []),
            "genres": [g.get("name") for g in _json(q["genres"], [])],
            "taste": _json(q.get("taste"), []),
            "used_past_answers": bool(q.get("use_history", 1)),
            "answers": len(answers),
            "choices": dict(Counter(a["choice"] for a in answers)),
        })

    # Movies the user picked (or tied) in comparisons, newest first.
    liked, liked_ids = [], set()
    for r in conn.execute("""
            SELECT a.movie_a, a.title_a, a.movie_b, a.title_b, a.choice FROM answers a
            JOIN quizzes q ON q.id = a.quiz_id WHERE q.user_id = ? AND a.choice IN ('a', 'b', 'tie')
            ORDER BY a.quiz_id DESC, a.round DESC""", (uid,)):
        sides = {"a": [("movie_a", "title_a")], "b": [("movie_b", "title_b")],
                 "tie": [("movie_a", "title_a"), ("movie_b", "title_b")]}[r["choice"]]
        for id_col, title_col in sides:
            if r[id_col] not in liked_ids:
                liked_ids.add(r[id_col])
                liked.append({"id": r[id_col], "title": r[title_col],
                              "how": "tie (liked both)" if r["choice"] == "tie" else "picked"})

    seen = [dict(r) for r in conn.execute(
        "SELECT movie_id AS id, title, marked_at FROM seen WHERE user_id = ? ORDER BY marked_at DESC", (uid,))]
    seen_ids = {s["id"] for s in seen}

    # Every movie ever recommended, latest showing of each, unseen first.
    recs = {}
    for r in conn.execute("""
            SELECT r.*, q.started_at AS quiz_date FROM recommendations r
            JOIN quizzes q ON q.id = r.quiz_id WHERE q.user_id = ?
            ORDER BY r.shown_at, r.quiz_id""", (uid,)):
        recs[r["movie_id"]] = {
            "id": r["movie_id"], "title": r["title"], "year": r["year"], "match": r["match"],
            "why": r["why"], "services": _json(r["services"], []),
            "recommended_on": r["quiz_date"], "seen": r["movie_id"] in seen_ids,
            "_quiz": r["quiz_id"],
        }
    # Newest quiz first (by id: two quizzes can share a timestamp), best match first.
    newest_best = sorted(recs.values(), key=lambda m: (m["_quiz"], m["match"] or 0), reverse=True)
    for m in newest_best:
        del m["_quiz"]
    ordered = [m for m in newest_best if not m["seen"]] + [m for m in newest_best if m["seen"]]

    return {
        "profile": {"name": user["name"], "icon": user["icon"],
                    "services": _json(user["services"], []), "created_at": user["created_at"]},
        "overall_taste": [t for t, _ in taste_counts.most_common(8)],
        "totals": {"quizzes": len(all_quizzes), "liked_movies": len(liked), "seen": len(seen),
                   "recommended": len(recs)},
        "recent_quizzes": recent,
        "liked_in_quizzes": liked[:limit],
        "seen": seen,
        "recommendations": ordered[:limit],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("start", "status"):
        sub.add_parser(name).add_argument("--port", type=int, default=8000)
    sub.add_parser("stop")
    sub.add_parser("profiles")
    sp = sub.add_parser("profile")
    sp.add_argument("name")
    sp.add_argument("--quizzes", type=int, default=5, help="recent quizzes to include")
    sp.add_argument("--limit", type=int, default=30, help="max liked movies / recommendations")
    args = p.parse_args()

    try:
        if args.cmd == "start":
            out = start(args.port)
        elif args.cmd == "status":
            out = status(args.port)
        elif args.cmd == "stop":
            out = stop()
        elif args.cmd == "profiles":
            out = profiles()
        else:
            out = profile(args.name, args.quizzes, args.limit)
    except (BridgeError, sqlite3.Error) as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
