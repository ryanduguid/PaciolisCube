from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_declares_source_only_installation() -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())

    assert "**Package lifecycle:** source-only." in readme
    assert "not published to PyPI" in readme
    assert "git clone https://github.com/ryanduguid/PaciolisCube.git" in readme
    assert "python -m pip install ." in readme
    assert "pip install pacioliscube" not in readme
