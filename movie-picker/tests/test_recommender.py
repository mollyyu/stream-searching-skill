import random

from app import recommender

GENRES = {878: "Science Fiction", 10749: "Romance", 35: "Comedy", 27: "Horror", 18: "Drama"}


def make_movies(n=200, seed=1):
    rng = random.Random(seed)
    movies = []
    for i in range(n):
        g = rng.sample(list(GENRES), k=rng.choice([1, 2]))
        kws = []
        if 878 in g:
            kws += rng.sample(["space", "time travel", "alien", "robot"], 2)
        if 10749 in g:
            kws += rng.sample(["love", "wedding", "first love"], 1)
        movies.append({"id": i, "title": f"M{i}", "year": rng.randint(1970, 2025),
                       "genre_ids": g, "keywords": kws,
                       "popularity": rng.uniform(5, 300), "rating": rng.uniform(5, 8.5), "votes": 500})
    return movies


def run_quiz(movies, likes, rounds=10):
    vectors, names = recommender.featurize(movies, GENRES)
    history = []
    for r in range(rounds):
        w = recommender.replay(history, vectors, len(names))
        seen = {h["a"] for h in history} | {h["b"] for h in history}
        a, b = recommender.choose_pair(movies, vectors, w, seen, r, random.Random(r))
        score = lambda m: sum(1 for x in m["genre_ids"] if x in likes)
        choice = "a" if score(a) > score(b) else "b" if score(b) > score(a) else "skip"
        history.append({"a": a["id"], "b": b["id"], "choice": choice})
    w = recommender.replay(history, vectors, len(names))
    seen = {h["a"] for h in history} | {h["b"] for h in history}
    return recommender.recommend(movies, vectors, w, seen), w, vectors, names, seen


def test_featurize_unit_vectors():
    movies = make_movies()
    vectors, names = recommender.featurize(movies, GENRES)
    assert len(vectors) == len(movies)
    assert all(abs(recommender.norm(v) - 1) < 1e-9 for v in vectors.values())
    assert "genre:Science Fiction" in names and "kw:space" in names


def test_quiz_learns_sci_fi_fan():
    movies = make_movies()
    recs, *_ = run_quiz(movies, likes={878})
    assert len(recs) == 10
    sci_fi = sum(1 for m in recs if 878 in m["genre_ids"])
    assert sci_fi >= 8, [m["genre_ids"] for m in recs]


def test_quiz_learns_romance_fan():
    recs, *_ = run_quiz(make_movies(seed=2), likes={10749})
    assert sum(1 for m in recs if 10749 in m["genre_ids"]) >= 8


def test_recommendations_exclude_shown_and_explain():
    movies = make_movies()
    recs, w, vectors, names, seen = run_quiz(movies, likes={878})
    assert not seen & {m["id"] for m in recs}
    reasons = recommender.explain(vectors[recs[0]["id"]], w, names)
    assert reasons and len(reasons) <= 3


def test_pairs_are_never_repeated():
    movies = make_movies()
    vectors, names = recommender.featurize(movies, GENRES)
    seen, history = set(), []
    for r in range(10):
        w = recommender.replay(history, vectors, len(names))
        a, b = recommender.choose_pair(movies, vectors, w, seen, r, random.Random(r))
        assert a["id"] not in seen and b["id"] not in seen and a["id"] != b["id"]
        seen |= {a["id"], b["id"]}
        history.append({"a": a["id"], "b": b["id"], "choice": "a"})


def test_all_skips_falls_back_to_well_rated():
    movies = make_movies()
    vectors, names = recommender.featurize(movies, GENRES)
    w = [0.0] * len(names)
    recs = recommender.recommend(movies, vectors, w, set())
    assert len(recs) == 10


def test_explain_ignores_negative_times_negative():
    names = ["genre:Horror", "acclaim"]
    assert recommender.explain([0.5, -0.4], [1.0, -2.0], names) == ["Horror"]


def test_quality_breaks_ties_toward_better_rated():
    movies = [{"id": 1, "year": 2000, "genre_ids": [27], "keywords": [], "popularity": 10, "rating": 5.0},
              {"id": 2, "year": 2000, "genre_ids": [27], "keywords": [], "popularity": 10, "rating": 8.0}]
    vectors = {1: [1.0, 0.0], 2: [1.0, 0.0]}
    assert [m["id"] for m in recommender.recommend(movies, vectors, [1.0, 0.0], set(), n=2)] == [2, 1]


def test_questions_prefer_recent_movies():
    movies = make_movies()
    vectors, names = recommender.featurize(movies, GENRES)
    w = [0.0] * len(names)
    for r in range(10):
        a, b = recommender.choose_pair(movies, vectors, w, set(), r, random.Random(r), min_year=2006)
        assert a["year"] >= 2006 and b["year"] >= 2006


def test_questions_fall_back_when_too_few_recent():
    movies = make_movies()
    for m in movies:
        m["year"] = 1980
    vectors, names = recommender.featurize(movies, GENRES)
    assert recommender.choose_pair(movies, vectors, [0.0] * len(names), set(), 0,
                                   random.Random(0), min_year=2006) is not None


def test_match_percentiles_rank_best_fit_highest():
    movies = make_movies()
    recs, w, vectors, names, seen = run_quiz(movies, likes={878})
    match = recommender.match_percentiles(movies, vectors, w, seen)
    assert max(match.values()) == 100 and min(match.values()) == 0
    sci_fi = [match[m["id"]] for m in movies if m["id"] in match and 878 in m["genre_ids"]]
    other = [match[m["id"]] for m in movies if m["id"] in match and 878 not in m["genre_ids"]]
    assert sum(sci_fi) / len(sci_fi) > sum(other) / len(other) + 30
    assert "Science Fiction" in recommender.taste_summary(w, names)


def test_tie_learns_what_both_movies_share():
    movies = make_movies()
    vectors, names = recommender.featurize(movies, GENRES)
    sci = [m for m in movies if m["genre_ids"] == [878]][:2]
    w = recommender.replay([{"a": sci[0]["id"], "b": sci[1]["id"], "choice": "tie"}], vectors, len(names))
    assert w[names.index("genre:Science Fiction")] > 0
    assert w[names.index("genre:Romance")] <= 0


def test_skip_teaches_nothing_but_tie_does():
    movies = make_movies()
    vectors, names = recommender.featurize(movies, GENRES)
    a, b = movies[0]["id"], movies[1]["id"]
    assert not any(recommender.replay([{"a": a, "b": b, "choice": "skip"}], vectors, len(names)))
    assert any(recommender.replay([{"a": a, "b": b, "choice": "tie"}], vectors, len(names)))


def test_why_sentence_names_features_and_similar_pick():
    movies = make_movies()
    recs, w, vectors, names, seen = run_quiz(movies, likes={878})
    liked = {m["id"]: m["title"] for m in movies if m["id"] in seen and 878 in m["genre_ids"]}
    sentence = recommender.why_sentence(recs[0]["id"], vectors, w, names, liked)
    assert sentence.endswith(".") and len(sentence) < 160
    assert "sci-fi" in sentence
    assert "which you picked" in sentence


def test_why_sentence_without_signal():
    movies = make_movies()
    vectors, names = recommender.featurize(movies, GENRES)
    assert recommender.why_sentence(0, vectors, [0.0] * len(names), names, {}) == \
        "A well-rated, popular pick on your services."


def test_why_sentence_drops_overlapping_keywords():
    names = ["kw:alien", "kw:alien invasion"]
    vectors = {1: [0.7, 0.7]}
    assert recommender.why_sentence(1, vectors, [1.0, 1.0], names, {}) == "A movie with alien invasion."


def test_vectorize_matches_featurize_and_handles_outside_movies():
    movies = make_movies()
    space = recommender.build_space(movies, GENRES)
    vectors, names = recommender.featurize(movies, GENRES)
    assert vectors[5] == recommender.vectorize(movies[5], space) and names == space["names"]
    outsider = {"id": 999, "year": 2015, "genre_ids": [878, 12345], "keywords": ["space", "unknown kw"],
                "popularity": 50, "rating": 7}
    x = recommender.vectorize(outsider, space)
    assert abs(recommender.norm(x) - 1) < 1e-9
    assert x[names.index("genre:Science Fiction")] > 0 and x[names.index("kw:space")] > 0


def test_replay_starts_from_prior_at_given_strength():
    movies = make_movies()
    vectors, names = recommender.featurize(movies, GENRES)
    answer = [{"a": 0, "b": 1, "choice": "a"}]
    full = recommender.replay(answer, vectors, len(names))
    half = recommender.replay(answer, vectors, len(names), lr=0.5)
    assert all(abs(h - f / 2) < 1e-9 for h, f in zip(half, full))
    assert recommender.replay([], vectors, len(names), w=half) == half
