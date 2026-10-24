"""Local SQLite storage: profiles, quizzes, answers, recommendations, seen movies.

One file (data/moviepicker.db by default, or $MOVIE_PICKER_DB). Python's
built-in sqlite3, no extra dependencies.
"""

import json
import os
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent.parent / "data" / "moviepicker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    icon        TEXT NOT NULL,
    services    TEXT NOT NULL DEFAULT '[]',   -- JSON list of service keys
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quizzes (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    services     TEXT NOT NULL,               -- JSON list of service keys
    genres       TEXT NOT NULL,               -- JSON list of {id, name}
    taste        TEXT,                        -- JSON list, set when results are shown
    use_history  INTEGER NOT NULL DEFAULT 1,  -- start from what past quizzes taught
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS answers (
    quiz_id      INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    round        INTEGER NOT NULL,
    movie_a      INTEGER NOT NULL,
    title_a      TEXT,
    movie_b      INTEGER NOT NULL,
    title_b      TEXT,
    choice       TEXT NOT NULL CHECK (choice IN ('a', 'b', 'tie', 'skip')),
    answered_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (quiz_id, round)
);

CREATE TABLE IF NOT EXISTS recommendations (
    quiz_id    INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    movie_id   INTEGER NOT NULL,
    title      TEXT,
    year       INTEGER,
    poster     TEXT,
    match      INTEGER,
    why        TEXT,
    services   TEXT,                          -- JSON list of service names
    shown_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (quiz_id, movie_id)
);

-- Details of every movie shown in a comparison, so past answers can still be
-- learned from when a later quiz uses different services or genres.
CREATE TABLE IF NOT EXISTS movies (
    id          INTEGER PRIMARY KEY,
    title       TEXT,
    year        INTEGER,
    genre_ids   TEXT NOT NULL DEFAULT '[]',  -- JSON
    keywords    TEXT NOT NULL DEFAULT '[]',  -- JSON
    popularity  REAL,
    rating      REAL,
    poster      TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS seen (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    movie_id   INTEGER NOT NULL,
    title      TEXT,
    poster     TEXT,
    marked_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, movie_id)
);
"""


class DB:
    def __init__(self, path: str | Path | None = None):
        path = Path(path or os.environ.get("MOVIE_PICKER_DB") or DEFAULT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Add columns introduced after a database file was first created."""
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(quizzes)")}
        if "use_history" not in columns:
            self.conn.execute("ALTER TABLE quizzes ADD COLUMN use_history INTEGER NOT NULL DEFAULT 1")

    def close(self):
        self.conn.close()

    # ---------- users ----------

    def users(self) -> list[dict]:
        rows = self.conn.execute("SELECT id, name, icon FROM users ORDER BY name")
        return [dict(r) for r in rows]

    def user(self, user_id: int) -> dict | None:
        row = self.conn.execute("SELECT id, name, icon, services FROM users WHERE id = ?",
                                (user_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "services": json.loads(row["services"])}

    def create_user(self, name: str, icon: str) -> dict:
        """Raises sqlite3.IntegrityError if the name is taken (case-insensitive)."""
        cur = self.conn.execute("INSERT INTO users (name, icon) VALUES (?, ?)", (name, icon))
        return self.user(cur.lastrowid)

    def set_services(self, user_id: int, services: list[str]) -> None:
        self.conn.execute("UPDATE users SET services = ? WHERE id = ?",
                          (json.dumps(services), user_id))

    # ---------- quizzes, answers, recommendations ----------

    def create_quiz(self, user_id: int, services: list[str], genres: list[dict],
                    use_history: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO quizzes (user_id, services, genres, use_history) VALUES (?, ?, ?, ?)",
            (user_id, json.dumps(services), json.dumps(genres), int(use_history)))
        return cur.lastrowid

    def quiz(self, quiz_id: int) -> dict | None:
        row = self.conn.execute("SELECT id, user_id, use_history FROM quizzes WHERE id = ?",
                                (quiz_id,)).fetchone()
        return {**dict(row), "use_history": bool(row["use_history"])} if row else None

    def quiz_owner(self, quiz_id: int) -> int | None:
        quiz = self.quiz(quiz_id)
        return quiz["user_id"] if quiz else None

    def past_answers(self, user_id: int, exclude_quiz: int | None = None,
                     limit: int = 50) -> tuple[list[dict], int]:
        """The user's most recent answers from other quizzes, oldest first, and
        how many quizzes they came from. Skips ("neither") are left out."""
        rows = [dict(r) for r in self.conn.execute(
            "SELECT a.quiz_id, a.round, a.movie_a AS a, a.movie_b AS b, a.choice"
            " FROM answers a JOIN quizzes q ON q.id = a.quiz_id"
            " WHERE q.user_id = ? AND q.id IS NOT ? AND a.choice != 'skip'"
            " ORDER BY a.quiz_id DESC, a.round DESC LIMIT ?", (user_id, exclude_quiz, limit))]
        rows.reverse()
        return rows, len({r["quiz_id"] for r in rows})

    def answer_count(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM answers a JOIN quizzes q ON q.id = a.quiz_id"
            " WHERE q.user_id = ? AND a.choice != 'skip'", (user_id,)).fetchone()
        return row["n"]

    # ---------- movies ----------

    def save_movies(self, movies: list[dict]) -> None:
        self.conn.executemany(
            "INSERT INTO movies (id, title, year, genre_ids, keywords, popularity, rating, poster)"
            " VALUES (:id, :title, :year, :genre_ids, :keywords, :popularity, :rating, :poster)"
            " ON CONFLICT(id) DO UPDATE SET title = excluded.title, year = excluded.year,"
            " genre_ids = excluded.genre_ids, keywords = excluded.keywords,"
            " popularity = excluded.popularity, rating = excluded.rating, poster = excluded.poster,"
            " updated_at = datetime('now')",
            [{"id": m["id"], "title": m.get("title"), "year": m.get("year"),
              "genre_ids": json.dumps(m.get("genre_ids", [])), "keywords": json.dumps(m.get("keywords", [])),
              "popularity": m.get("popularity"), "rating": m.get("rating"), "poster": m.get("poster")}
             for m in movies])

    def movies(self, ids: set[int]) -> dict[int, dict]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT * FROM movies WHERE id IN ({marks})", list(ids))
        return {r["id"]: {**dict(r), "genre_ids": json.loads(r["genre_ids"]),
                          "keywords": json.loads(r["keywords"])} for r in rows}

    def save_answers(self, quiz_id: int, answers: list[dict]) -> None:
        """answers: [{round, movie_a, title_a, movie_b, title_b, choice}]. Rounds
        already stored are left alone, so resending the whole history is safe."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO answers (quiz_id, round, movie_a, title_a, movie_b, title_b, choice)"
            " VALUES (:quiz_id, :round, :movie_a, :title_a, :movie_b, :title_b, :choice)",
            [{**a, "quiz_id": quiz_id} for a in answers])

    def save_recommendations(self, quiz_id: int, taste: list[str], cards: list[dict]) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "UPDATE quizzes SET taste = ?, finished_at = COALESCE(finished_at, datetime('now'))"
                " WHERE id = ?", (json.dumps(taste), quiz_id))
            self.conn.executemany(
                "INSERT OR IGNORE INTO recommendations"
                " (quiz_id, movie_id, title, year, poster, match, why, services)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(quiz_id, c["id"], c["title"], c["year"], c["poster"], c.get("match"),
                  c.get("why"), json.dumps(c.get("services", []))) for c in cards])

    def history(self, user_id: int, limit: int = 20) -> list[dict]:
        quizzes = [dict(r) for r in self.conn.execute(
            "SELECT id, services, genres, taste, started_at, finished_at FROM quizzes"
            " WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))]
        for q in quizzes:
            for key in ("services", "genres", "taste"):
                q[key] = json.loads(q[key]) if q[key] else []
            q["answers"] = [dict(r) for r in self.conn.execute(
                "SELECT round, movie_a, title_a, movie_b, title_b, choice FROM answers"
                " WHERE quiz_id = ? ORDER BY round", (q["id"],))]
            q["recommendations"] = [
                {**dict(r), "services": json.loads(r["services"] or "[]")}
                for r in self.conn.execute(
                    "SELECT movie_id, title, year, poster, match, why, services FROM recommendations"
                    " WHERE quiz_id = ? ORDER BY match DESC", (q["id"],))]
        return quizzes

    # ---------- seen ----------

    def seen(self, user_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT movie_id, title, poster, marked_at FROM seen"
                                 " WHERE user_id = ? ORDER BY marked_at DESC", (user_id,))
        return [dict(r) for r in rows]

    def seen_ids(self, user_id: int) -> set[int]:
        rows = self.conn.execute("SELECT movie_id FROM seen WHERE user_id = ?", (user_id,))
        return {r["movie_id"] for r in rows}

    def add_seen(self, user_id: int, movies: list[dict]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen (user_id, movie_id, title, poster) VALUES (?, ?, ?, ?)",
            [(user_id, m["movie_id"], m.get("title"), m.get("poster")) for m in movies])

    def remove_seen(self, user_id: int, movie_id: int | None = None) -> None:
        if movie_id is None:
            self.conn.execute("DELETE FROM seen WHERE user_id = ?", (user_id,))
        else:
            self.conn.execute("DELETE FROM seen WHERE user_id = ? AND movie_id = ?", (user_id, movie_id))
