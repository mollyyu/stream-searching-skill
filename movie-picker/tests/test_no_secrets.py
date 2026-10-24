"""Guard against publishing a TMDB key: no file git would commit may contain it.

The key is read from your environment, so it never appears in this file. The
test is skipped when no key is set (e.g. in CI without secrets)."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SECRETS = [v.strip() for v in (os.environ.get("TMDB_API_KEY"), os.environ.get("TMDB_READ_ACCESS_TOKEN"))
           if v and len(v.strip()) >= 16]


def publishable_files() -> list[Path]:
    """Tracked files plus new files that .gitignore doesn't exclude."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                         cwd=REPO, capture_output=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [REPO / p for p in out.stdout.decode().split("\0") if p]


@pytest.mark.skipif(not SECRETS, reason="no TMDB key in the environment to look for")
def test_tmdb_key_is_not_in_any_publishable_file():
    leaks = []
    for path in publishable_files():
        try:
            text = path.read_text(errors="ignore")
        except (IsADirectoryError, FileNotFoundError):
            continue
        if any(secret in text for secret in SECRETS):
            leaks.append(str(path.relative_to(REPO)))
    assert not leaks, f"Your TMDB key is in files git would publish: {leaks}"


def test_local_data_and_secrets_are_ignored():
    for path in [".env", "movie-picker/data/moviepicker.db", "movie-picker/data/server.log",
                 "stream-search-workspace/x", "movie-picker/.venv/x"]:
        res = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO)
        assert res.returncode == 0, f"{path} is not ignored by .gitignore"
