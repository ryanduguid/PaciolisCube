"""The declared version and the shipped version must not drift apart."""

from pathlib import Path

import pytest

import pacioliscube

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 predates tomllib
    tomllib = None


@pytest.mark.skipif(tomllib is None, reason="tomllib arrived in Python 3.11")
def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert pacioliscube.__version__ == declared
