import subprocess
import sys
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def make_repo(tmp_path):
    """Build a two-commit repo: base commit, then head commit. Caller supplies
    the file contents for each side. Returns (repo_path, base_sha, head_sha)."""
    def _make(base_files: dict[str, str], head_files: dict[str, str]):
        repo = tmp_path / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        for rel, content in base_files.items():
            f = repo / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        base_sha = git(repo, "rev-parse", "HEAD")
        for rel, content in head_files.items():
            f = repo / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--allow-empty", "-m", "head")
        head_sha = git(repo, "rev-parse", "HEAD")
        return repo, base_sha, head_sha
    return _make


BUGGY_CALC = "def add(a, b):\n    return a - b  # bug\n"
FIXED_CALC = "def add(a, b):\n    return a + b\n"
FIX_TEST = (
    "from calc import add\n\n\n"
    "def test_add():\n"
    "    assert add(2, 3) == 5\n"
)
