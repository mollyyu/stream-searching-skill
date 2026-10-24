"""Taste model: turn movies into feature vectors, pick informative this-or-that
pairs, learn from the answers, and rank recommendations.

Pure Python with no I/O, so it is easy to test.

The idea, in short:
- Every movie becomes a vector (genres, era, popularity, acclaim, keywords),
  normalized to length 1.
- The user's taste is a weight vector w; a movie's score is w . x.
- Each round shows two movies A and B. The line halfway between them splits
  the remaining candidates into "closer to A" and "closer to B". We choose the
  pair that splits them most evenly (like a binary search) among pairs that
  are clearly different and that the model can't yet tell apart.
- Picking A over B nudges w toward A and away from B (a Bradley-Terry update).
- Each round we also narrow to the best-scoring candidates, so later rounds
  refine within what the user likes.
"""

import math
import random
from collections import Counter

ERAS = [(0, 1979, "pre-1980"), (1980, 1989, "1980s"), (1990, 1999, "1990s"),
        (2000, 2009, "2000s"), (2010, 2019, "2010s"), (2020, 9999, "2020s")]
KEYWORD_WEIGHT = 0.7
MAX_KEYWORDS = 60
# Ranking nudge per rating point above/below 6.5, so a noisy taste profile
# doesn't surface poorly rated movies. Nobody really prefers worse movies.
QUALITY_WEIGHT = 0.15
# Common TMDB keywords that say nothing about taste.
IGNORED_KEYWORDS = {
    "duringcreditsstinger", "aftercreditsstinger", "sequel", "prequel", "remake",
    "based on novel or book", "based on comic", "based on true story", "woman director",
    "live action and animation", "3d", "imax", "duplicate",
}


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def sub(a: list[float], b: list[float]) -> list[float]:
    return [x - y for x, y in zip(a, b)]


def norm(a: list[float]) -> float:
    return math.sqrt(dot(a, a))


def _stats(values: list[float]) -> tuple[float, float]:
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) or 1.0
    return mean, std


def _z(value: float, stats: tuple[float, float]) -> float:
    mean, std = stats
    return max(-2.0, min(2.0, (value - mean) / std)) / 2


def build_space(movies: list[dict], genre_names: dict[int, str]) -> dict:
    """The feature layout for a pool: which genres and keywords count, and the
    popularity/rating scale. Any movie can then be turned into a vector in this
    space with vectorize(), including movies from past quizzes outside the pool."""
    kw_counts = Counter(k for m in movies for k in set(m.get("keywords", []))
                        if k.lower() not in IGNORED_KEYWORDS)
    keywords = [k for k, c in kw_counts.most_common(MAX_KEYWORDS) if c >= 3]
    genre_ids = sorted({g for m in movies for g in m["genre_ids"] if g in genre_names})
    names = ([f"genre:{genre_names[g]}" for g in genre_ids]
             + [f"era:{label}" for _, _, label in ERAS]
             + ["popularity", "acclaim"]
             + [f"kw:{k}" for k in keywords])
    era_base = len(genre_ids)
    kw_base = era_base + len(ERAS) + 2
    return {
        "names": names,
        "genre_index": {g: i for i, g in enumerate(genre_ids)},
        "era_base": era_base,
        "keyword_index": {k: kw_base + i for i, k in enumerate(keywords)},
        "popularity": _stats([math.log1p(m.get("popularity") or 0) for m in movies]),
        "acclaim": _stats([m.get("rating") or 0 for m in movies]),
    }


def vectorize(m: dict, space: dict) -> list[float]:
    """Unit vector for one movie (needs year, genre_ids, keywords, popularity, rating)."""
    x = [0.0] * len(space["names"])
    for g in m.get("genre_ids", []):
        if g in space["genre_index"]:
            x[space["genre_index"][g]] = 1.0
    era_base = space["era_base"]
    year = m.get("year")
    if year:
        for j, (lo, hi, _) in enumerate(ERAS):
            if lo <= year <= hi:
                x[era_base + j] = 1.0
    x[era_base + len(ERAS)] = _z(math.log1p(m.get("popularity") or 0), space["popularity"])
    x[era_base + len(ERAS) + 1] = _z(m.get("rating") or 0, space["acclaim"])
    for k in m.get("keywords", []):
        if k in space["keyword_index"]:
            x[space["keyword_index"][k]] = KEYWORD_WEIGHT
    length = norm(x) or 1.0
    return [v / length for v in x]


def featurize(movies: list[dict], genre_names: dict[int, str]) -> tuple[dict[int, list[float]], list[str]]:
    """Return ({movie_id: unit vector}, feature names) for a pool of movies."""
    space = build_space(movies, genre_names)
    return {m["id"]: vectorize(m, space) for m in movies}, space["names"]


def update(w: list[float], winner: list[float], loser: list[float], lr: float = 1.0) -> None:
    """Bradley-Terry step: move w toward the winner, scaled by how surprised we were."""
    d = sub(winner, loser)
    p = 1 / (1 + math.exp(-dot(w, d)))
    for i, di in enumerate(d):
        w[i] += lr * (1 - p) * di


TIE_WEIGHT = 0.5  # a tie is softer evidence than a clear pick


def replay(history: list[dict], vectors: dict[int, list[float]], dim: int,
           w: list[float] | None = None, lr: float = 1.0) -> list[float]:
    """Rebuild the taste vector from answers ({a, b, choice}), starting from w
    (e.g. what past quizzes taught) at learning rate lr.

    choice: "a" / "b" = preferred that movie; "tie" = likes both equally;
    "skip" = no opinion (teaches nothing).
    """
    w = list(w) if w else [0.0] * dim
    centroid = None
    for h in history:
        xa, xb = vectors.get(h["a"]), vectors.get(h["b"])
        if xa is None or xb is None:
            continue
        if h["choice"] == "a":
            update(w, xa, xb, lr)
        elif h["choice"] == "b":
            update(w, xb, xa, lr)
        elif h["choice"] == "tie":
            # Liking both means liking what they have in common, compared with
            # an average movie in the pool.
            if centroid is None:
                centroid = [sum(col) / len(vectors) for col in zip(*vectors.values())]
            both = [(a + b) / 2 for a, b in zip(xa, xb)]
            update(w, both, centroid, lr * TIE_WEIGHT)
    return w


def _shortlist(movies, vectors, w, seen, round_idx):
    candidates = [m for m in movies if m["id"] not in seen and m["id"] in vectors]
    candidates.sort(key=lambda m: dot(w, vectors[m["id"]]), reverse=True)
    keep = max(24, int(len(candidates) * 0.8 ** round_idx))
    return candidates[:keep]


def choose_pair(movies: list[dict], vectors: dict[int, list[float]], w: list[float],
                seen: set[int], round_idx: int, rng: random.Random,
                min_year: int | None = None) -> tuple[dict, dict] | None:
    shortlist = _shortlist(movies, vectors, w, seen, round_idx)
    if len(shortlist) < 2:
        return None
    # Only show movies people are likely to recognize: recent ones (when there
    # are enough), and among those the more popular 60%. Without the year
    # filter, old movies win too often because "different decade" makes a pair
    # look informative, but people can't choose between movies they haven't seen.
    candidates = shortlist
    if min_year:
        recent = [m for m in shortlist if (m.get("year") or 0) >= min_year]
        if len(recent) >= 10:
            candidates = recent
    by_pop = sorted(candidates, key=lambda m: m.get("popularity") or 0, reverse=True)
    showable = by_pop[:max(10, int(len(by_pop) * 0.6))]

    pairs = [(a, b) for i, a in enumerate(showable) for b in showable[i + 1:]]
    if len(pairs) > 400:
        pairs = rng.sample(pairs, 400)

    best, best_score = None, -1.0
    for a, b in pairs:
        xa, xb = vectors[a["id"]], vectors[b["id"]]
        d = sub(xa, xb)
        distinct = norm(d)
        if distinct < 1e-6:
            continue
        threshold = (dot(xa, d) + dot(xb, d)) / 2
        closer_to_a = sum(1 for m in shortlist if dot(vectors[m["id"]], d) > threshold)
        frac = closer_to_a / len(shortlist)
        balance = 1 - abs(2 * frac - 1)
        uncertainty = 1 / (1 + abs(dot(w, d)))
        score = balance * distinct * uncertainty * (1 + 0.05 * rng.random())
        if score > best_score:
            best, best_score = (a, b), score
    return best


def recommend(movies: list[dict], vectors: dict[int, list[float]], w: list[float],
              seen: set[int], n: int = 10, diversity: float = 0.15) -> list[dict]:
    """Top-n by score, re-ranked so the list isn't n near-identical movies (MMR)."""
    pool = [m for m in movies if m["id"] not in seen and m["id"] in vectors]
    if not any(w):
        # No signal yet (all skips): fall back to well-rated popular titles.
        pool.sort(key=lambda m: (m.get("rating") or 0) * math.log1p(m.get("votes") or 0), reverse=True)
        return pool[:n]
    def score(m):
        return dot(w, vectors[m["id"]]) + QUALITY_WEIGHT * ((m.get("rating") or 6.5) - 6.5)

    pool.sort(key=score, reverse=True)
    pool = pool[:n * 4]

    chosen: list[dict] = []
    while pool and len(chosen) < n:
        def mmr(m):
            x = vectors[m["id"]]
            similarity = max((dot(x, vectors[c["id"]]) for c in chosen), default=0.0)
            return (1 - diversity) * score(m) - diversity * similarity
        pick = max(pool, key=mmr)
        chosen.append(pick)
        pool.remove(pick)
    return chosen


def match_percentiles(movies: list[dict], vectors: dict[int, list[float]], w: list[float],
                      seen: set[int]) -> dict[int, int]:
    """Match % for each unseen movie: the share of the other candidates it beats
    on taste alone. 95 means a better fit than 95% of what's on your services."""
    pool = [m["id"] for m in movies if m["id"] not in seen and m["id"] in vectors]
    if not any(w) or len(pool) < 2:
        return {}
    ranked = sorted(pool, key=lambda i: dot(w, vectors[i]))
    return {movie_id: round(100 * rank / (len(ranked) - 1)) for rank, movie_id in enumerate(ranked)}


def taste_summary(w: list[float], names: list[str], limit: int = 5) -> list[str]:
    """The features the user leans toward most, for the results header."""
    return explain([1.0] * len(names), w, names, limit)


def explain(x: list[float], w: list[float], names: list[str], limit: int = 3) -> list[str]:
    """Human-readable features that contributed most to this movie's score."""
    # Only features the user leans toward AND this movie has; a negative weight
    # times a negative value (e.g. "dislikes acclaim" x "poorly rated") isn't a reason.
    contributions = sorted(((wi * xi, name) for wi, xi, name in zip(w, x, names)
                            if wi > 0 and xi > 0), reverse=True)
    reasons = []
    for value, name in contributions:
        if value <= 0 or len(reasons) >= limit:
            break
        if name == "popularity":
            reasons.append("crowd favorite")
        elif name == "acclaim":
            reasons.append("highly rated")
        else:
            reasons.append(name.split(":", 1)[1])
    return reasons


GENRE_WORDS = {"Science Fiction": "sci-fi", "TV Movie": ""}


def why_sentence(movie_id: int, vectors: dict[int, list[float]], w: list[float],
                 names: list[str], liked: dict[int, str]) -> str:
    """One short sentence on why this movie was recommended.

    liked: {movie_id: title} of movies the user picked (or tied) in the quiz.
    Example: "A highly rated 2020s sci-fi movie with space travel, like Dune: Part Two, which you picked."
    """
    x = vectors[movie_id]
    contributions = sorted(((wi * xi, name) for wi, xi, name in zip(w, x, names)
                            if wi > 0 and xi > 0), reverse=True)[:4]
    top = [name for _, name in contributions]

    adjective = ("highly rated " if "acclaim" in top
                 else "crowd-pleasing " if "popularity" in top else "")
    era = next((n.split(":", 1)[1] + " " for n in top if n.startswith("era:")), "")
    genres = [GENRE_WORDS.get(g, g.lower()) for g in
              (n.split(":", 1)[1] for n in top if n.startswith("genre:"))]
    genres = [g for g in genres if g]
    animated = "animated " if "animation" in genres else ""
    genre = animated + " and ".join([g for g in genres if g != "animation"][:2])
    genre = genre.strip()
    keywords: list[str] = []
    for kw in (n.split(":", 1)[1] for n in top if n.startswith("kw:")):
        # "alien" + "alien invasion" -> just "alien invasion".
        if any(kw in k for k in keywords):
            continue
        keywords = [k for k in keywords if k not in kw] + [kw]

    phrase = f"{adjective}{era}{genre + ' ' if genre else ''}movie"
    phrase += f" with {' and '.join(keywords[:2])}" if keywords else ""
    article = "An" if phrase[0] in "aeiou8" else "A"

    # The most similar movie the user chose, if it's genuinely close.
    like = ""
    if liked:
        best = max(liked, key=lambda i: dot(x, vectors[i]) if i in vectors else -1)
        if best in vectors and dot(x, vectors[best]) > 0.4:
            like = f", like {liked[best]}, which you picked"

    if not top and not like:
        return "A well-rated, popular pick on your services."
    return f"{article} {phrase}{like}."
