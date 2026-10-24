#!/usr/bin/env python3
"""Look up streaming availability with the TMDB API.

TMDB's watch-provider data comes from JustWatch. Only the Python standard
library is used, so there is nothing to install.

Commands:
  where "<title>" [--year Y] [--type movie|tv] [--seasons]
      Search for a title and show where the best match is available, plus
      other close matches so you can tell same-name titles apart.
  search "<title>" [--year Y] [--type movie|tv]
      List matching titles with their TMDB ids.
  providers <movie|tv> <id> [--seasons]
      Availability for a known TMDB id. --seasons checks each TV season.
  discover --service NAME [--type movie|tv] [--genre NAME] [--year-from Y]
           [--year-to Y] [--min-rating R] [--sort popular|rating|newest]
           [--limit N] [--include-free]
      Browse what a service carries. Several services: --service "netflix,hbo max".
  services
      List the streaming services TMDB knows for the region, with ids.
  genres [--type movie|tv]
      List genre names.

Every command takes --region (default US). Output is JSON.

Auth: set TMDB_API_KEY (the v3 "API Key") or TMDB_READ_ACCESS_TOKEN (the
v4 "API Read Access Token"). Both are on https://www.themoviedb.org/settings/api
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.themoviedb.org/3"
ATTRIBUTION = "Streaming data from JustWatch via TMDB"

# What users type -> the normalized start of TMDB's provider names. One service
# often has several tier entries ("Paramount Plus Premium", "Paramount Plus
# Essential", "Netflix Standard with Ads"), and they all count.
SERVICE_ALIASES = {
    "max": "hbomax", "hbo": "hbomax",
    "apple": "appletv", "appletvplus": "appletv",
    "disney": "disneyplus",
    "prime": "amazonprimevideo", "amazon": "amazonprimevideo",
    "amazonprime": "amazonprimevideo", "primevideo": "amazonprimevideo",
    "paramount": "paramountplus",
}

GENRE_ALIASES = {
    "scifi": "science fiction", "sf": "science fiction",
    "romcom": "romance", "kids": "family", "doc": "documentary", "docs": "documentary",
}
# TV genres on TMDB are merged pairs.
TV_GENRE_ALIASES = {
    "science fiction": "sci-fi & fantasy", "fantasy": "sci-fi & fantasy",
    "action": "action & adventure", "adventure": "action & adventure",
    "war": "war & politics", "politics": "war & politics",
}


class TMDBError(Exception):
    pass


def norm(name):
    name = name.lower().replace("+", "plus")
    return re.sub(r"[^a-z0-9]", "", name)


def api(path, **params):
    key = os.environ.get("TMDB_API_KEY", "").strip()
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    # A v4 token pasted into TMDB_API_KEY still works.
    if key.startswith("eyJ") and not token:
        token, key = key, ""
    if not key and not token:
        raise TMDBError(
            "No TMDB credentials. Set TMDB_API_KEY or TMDB_READ_ACCESS_TOKEN "
            "(free at https://www.themoviedb.org/settings/api).")

    params = {k: v for k, v in params.items() if v is not None}
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    else:
        params["api_key"] = key
    url = API + path + "?" + urllib.parse.urlencode(params)

    for attempt in range(3):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(int(e.headers.get("Retry-After", "2")))
                continue
            if e.code == 401:
                raise TMDBError("TMDB rejected the credentials (401). Check the key.")
            if e.code == 404:
                raise TMDBError("Not found on TMDB: " + path)
            raise TMDBError("TMDB HTTP %d for %s" % (e.code, path))
        except urllib.error.URLError as e:
            raise TMDBError("Could not reach TMDB: %s" % e.reason)
    raise TMDBError("TMDB rate limit; try again shortly.")


def year_of(item):
    date = item.get("release_date") or item.get("first_air_date") or ""
    return int(date[:4]) if date[:4].isdigit() else None


def summarize(item, media_type=None):
    return {
        "id": item["id"],
        "type": media_type or item.get("media_type"),
        "title": item.get("title") or item.get("name"),
        "year": year_of(item),
        "rating": item.get("vote_average"),
        "votes": item.get("vote_count"),
        "overview": (item.get("overview") or "")[:200],
    }


def search(title, year=None, media_type=None):
    if media_type == "movie":
        data = api("/search/movie", query=title, primary_release_year=year)
        results = [summarize(r, "movie") for r in data.get("results", [])]
    elif media_type == "tv":
        data = api("/search/tv", query=title, first_air_date_year=year)
        results = [summarize(r, "tv") for r in data.get("results", [])]
    else:
        data = api("/search/multi", query=title)
        results = [summarize(r) for r in data.get("results", [])
                   if r.get("media_type") in ("movie", "tv")]
        if year:
            results = [r for r in results if r["year"] == year]
    # Exact title matches first, so "Dune" beats a more popular "Dune: Part Three".
    q = norm(title)
    results.sort(key=lambda r: norm(r["title"] or "") != q)
    return results[:8]


def provider_names(entry, key):
    return [p["provider_name"] for p in
            sorted(entry.get(key, []), key=lambda p: p.get("display_priority", 999))]


def availability(entry):
    return {
        "subscription": provider_names(entry, "flatrate"),
        "free": provider_names(entry, "free"),
        "free_with_ads": provider_names(entry, "ads"),
        "rent": provider_names(entry, "rent"),
        "buy": provider_names(entry, "buy"),
    }


def providers(media_type, tmdb_id, region, seasons=False):
    details = api("/%s/%s" % (media_type, tmdb_id))
    regional = api("/%s/%s/watch/providers" % (media_type, tmdb_id)) \
        .get("results", {}).get(region, {})
    out = {
        "id": tmdb_id,
        "type": media_type,
        "title": details.get("title") or details.get("name"),
        "year": year_of(details),
        "region": region,
        "available": availability(regional),
        "streaming_anywhere": bool(regional),
        "justwatch_link": regional.get("link"),
    }
    if media_type == "tv":
        out.update({
            "status": details.get("status"),
            "number_of_seasons": details.get("number_of_seasons"),
            "last_air_date": details.get("last_air_date"),
            "next_episode": (details.get("next_episode_to_air") or {}).get("air_date"),
            "networks": [n["name"] for n in details.get("networks", [])],
        })
        if seasons:
            per_season = []
            for s in details.get("seasons", [])[:30]:
                if s.get("season_number", 0) == 0:  # specials
                    continue
                entry = api("/tv/%s/season/%s/watch/providers" % (tmdb_id, s["season_number"])) \
                    .get("results", {}).get(region, {})
                per_season.append({
                    "season": s["season_number"],
                    "subscription": provider_names(entry, "flatrate"),
                    "free_with_ads": provider_names(entry, "ads") + provider_names(entry, "free"),
                })
            out["seasons"] = per_season
    out["attribution"] = ATTRIBUTION
    return out


def where(title, year, media_type, region, seasons):
    matches = search(title, year, media_type)
    if not matches:
        return {"query": title, "error": "No TMDB match. Try another spelling, or drop --year."}
    best = matches[0]
    result = providers(best["type"], best["id"], region, seasons)
    result["other_matches"] = [
        {k: m[k] for k in ("id", "type", "title", "year")} for m in matches[1:5]]
    return result


def service_list(region):
    merged = {}
    for media_type in ("movie", "tv"):
        for p in api("/watch/providers/%s" % media_type, watch_region=region).get("results", []):
            prio = p.get("display_priorities", {}).get(region, p.get("display_priority", 999))
            merged[p["provider_id"]] = {"id": p["provider_id"], "name": p["provider_name"],
                                        "priority": prio}
    return sorted(merged.values(), key=lambda p: p["priority"])


def resolve_service(name, region, services=None):
    """Return every TMDB provider entry (all tiers) for a service name or id."""
    services = services if services is not None else service_list(region)
    if name.isdigit():
        return [s for s in services if s["id"] == int(name)] or \
            [{"id": int(name), "name": name, "priority": 999}]
    q = norm(name)
    base = SERVICE_ALIASES.get(q, q)
    # Add-on channels ("HBO Max Amazon Channel") and rent/buy stores ("Apple TV
    # Store") are different products; only include them when asked for.
    wanted = [w for w in ("channel", "store") if w in q]

    def plain(s):
        lower = s["name"].lower()
        return all(w in wanted or w not in lower for w in ("channel", "store"))

    group = [s for s in services if norm(s["name"]).startswith(base) and plain(s)]
    if group:
        return group
    partial = [s for s in services if q in norm(s["name"]) and plain(s)]
    if len(partial) == 1:
        return partial
    if partial:
        raise TMDBError("'%s' matches several services: %s. Use the exact name or id."
                        % (name, ", ".join("%s (%d)" % (s["name"], s["id"]) for s in partial[:8])))
    raise TMDBError("No service named '%s' in %s. Run `services` to see the list." % (name, region))


def resolve_genre(name, media_type):
    genres = api("/genre/%s/list" % media_type).get("genres", [])
    q = name.lower().strip()
    q = GENRE_ALIASES.get(norm(q), q)
    if media_type == "tv":
        q = TV_GENRE_ALIASES.get(q, q)
    for g in genres:
        if norm(g["name"]) == norm(q):
            return g
    for g in genres:
        if norm(q) in norm(g["name"]):
            return g
    raise TMDBError("Unknown %s genre '%s'. Options: %s"
                    % (media_type, name, ", ".join(g["name"] for g in genres)))


def discover(args):
    media_type = args.type or "movie"
    services = service_list(args.region)
    chosen = []
    for name in args.service.split(","):
        if name.strip():
            chosen += [s for s in resolve_service(name.strip(), args.region, services)
                       if s not in chosen]
    date_field = "primary_release_date" if media_type == "movie" else "first_air_date"
    sort = {"popular": "popularity.desc", "rating": "vote_average.desc",
            "newest": date_field + ".desc"}[args.sort]
    monetization = "flatrate|free|ads" if args.include_free else "flatrate"

    params = {
        "watch_region": args.region,
        "with_watch_providers": "|".join(str(s["id"]) for s in chosen),
        "with_watch_monetization_types": monetization,
        "sort_by": sort,
        "vote_count.gte": args.min_votes,
        "vote_average.gte": args.min_rating,
        "include_adult": "false",
    }
    genre = None
    if args.genre:
        genre = resolve_genre(args.genre, media_type)
        params["with_genres"] = genre["id"]
    if args.year_from:
        params[date_field + ".gte"] = "%d-01-01" % args.year_from
    if args.year_to:
        params[date_field + ".lte"] = "%d-12-31" % args.year_to
    elif args.sort == "newest":
        params[date_field + ".lte"] = datetime.date.today().isoformat()

    results, total, page = [], 0, 1
    while len(results) < args.limit and page <= 5:
        data = api("/discover/%s" % media_type, page=page, **params)
        total = data.get("total_results", 0)
        results += [summarize(r, media_type) for r in data.get("results", [])]
        if page >= data.get("total_pages", 0):
            break
        page += 1
    return {
        "services": [s["name"] for s in chosen],
        "type": media_type,
        "genre": genre["name"] if genre else None,
        "region": args.region,
        "total_matching": total,
        "results": results[:args.limit],
        "attribution": ATTRIBUTION,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default="US", help="ISO country code (default US)")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("where", "search"):
        sp = sub.add_parser(name)
        sp.add_argument("title")
        sp.add_argument("--year", type=int)
        sp.add_argument("--type", choices=["movie", "tv"])
        if name == "where":
            sp.add_argument("--seasons", action="store_true", help="check each TV season")

    sp = sub.add_parser("providers")
    sp.add_argument("type", choices=["movie", "tv"])
    sp.add_argument("id", type=int)
    sp.add_argument("--seasons", action="store_true")

    sp = sub.add_parser("discover")
    sp.add_argument("--service", required=True)
    sp.add_argument("--type", choices=["movie", "tv"])
    sp.add_argument("--genre")
    sp.add_argument("--year-from", type=int)
    sp.add_argument("--year-to", type=int)
    sp.add_argument("--min-rating", type=float)
    sp.add_argument("--min-votes", type=int, default=100,
                    help="filters out obscure titles (default 100)")
    sp.add_argument("--sort", choices=["popular", "rating", "newest"], default="popular")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--include-free", action="store_true", help="also include free/ad-supported")

    sub.add_parser("services")
    sp = sub.add_parser("genres")
    sp.add_argument("--type", choices=["movie", "tv"], default="movie")

    # Accept --region after the subcommand too.
    argv = sys.argv[1:]
    if "--region" in argv[1:]:
        i = argv.index("--region")
        argv = argv[i:i + 2] + argv[:i] + argv[i + 2:]
    args = p.parse_args(argv)
    args.region = args.region.upper()

    try:
        if args.cmd == "where":
            out = where(args.title, args.year, args.type, args.region, args.seasons)
        elif args.cmd == "search":
            out = search(args.title, args.year, args.type)
        elif args.cmd == "providers":
            out = providers(args.type, args.id, args.region, args.seasons)
        elif args.cmd == "discover":
            out = discover(args)
        elif args.cmd == "services":
            out = [{k: s[k] for k in ("id", "name")} for s in service_list(args.region)]
        else:
            out = api("/genre/%s/list" % args.type).get("genres", [])
    except TMDBError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
