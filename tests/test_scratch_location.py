"""Repo convention: scratch files stay under the repo (tests/.scratch_tmp), never /tmp - pytest's tmp_path
and tempfile both point there (full review 2026-09-22)."""
import tempfile
from pathlib import Path

REPO_SCRATCH = Path(__file__).resolve().parent / ".scratch_tmp"


def test_tmp_path_is_under_the_repo_scratch_dir(tmp_path):
    assert REPO_SCRATCH in tmp_path.resolve().parents, tmp_path


def test_tempfile_default_is_under_the_repo_scratch_dir():
    assert REPO_SCRATCH in Path(tempfile.gettempdir()).resolve().parents or Path(tempfile.gettempdir()).resolve() == REPO_SCRATCH, tempfile.gettempdir()
