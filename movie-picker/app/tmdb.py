"""Small async TMDB client with an in-memory cache.

Streaming availability ("watch providers") on TMDB comes from JustWatch.
"""

import asyncio
import os
import time

import httpx

API = "https://api.themoviedb.org/3"
IMAGE = "https://image.tmdb.org/t/p/"


class TMDBError(Exception):
    pass


class TMDB:
    def __init__(self, ttl_seconds: int = 6 * 3600, concurrency: int = 16):
        self.ttl = ttl_seconds
        self._cache: dict = {}
        self._client = httpx.AsyncClient(base_url=API, timeout=20)
        self._sem = asyncio.Semaphore(concurrency)

    def _auth(self) -> tuple[dict, dict]:
        key = os.environ.get("TMDB_API_KEY", "").strip()
        token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
        if key.startswith("eyJ") and not token:  # a v4 token in the v3 variable
            token, key = key, ""
        if token:
            return {"Authorization": f"Bearer {token}"}, {}
        if key:
            return {}, {"api_key": key}
        raise TMDBError("TMDB_API_KEY is not set. Get a free key at "
                        "https://www.themoviedb.org/settings/api")

    async def get(self, path: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        cache_key = (path, tuple(sorted(params.items())))
        hit = self._cache.get(cache_key)
        if hit and hit[0] > time.time():
            return hit[1]

        headers, auth_params = self._auth()
        async with self._sem:
            for attempt in range(3):
                try:
                    resp = await self._client.get(path, params={**params, **auth_params},
                                                  headers=headers)
                except httpx.HTTPError as e:
                    raise TMDBError(f"Could not reach TMDB: {e}") from e
                if resp.status_code == 429 and attempt < 2:
                    await asyncio.sleep(float(resp.headers.get("Retry-After", "1")))
                    continue
                break
        if resp.status_code == 401:
            raise TMDBError("TMDB rejected the API key (401).")
        if resp.status_code >= 400:
            raise TMDBError(f"TMDB HTTP {resp.status_code} for {path}")

        data = resp.json()
        self._cache[cache_key] = (time.time() + self.ttl, data)
        return data

    async def close(self):
        await self._client.aclose()


def image_url(path: str | None, size: str = "w342") -> str | None:
    return f"{IMAGE}{size}{path}" if path else None
