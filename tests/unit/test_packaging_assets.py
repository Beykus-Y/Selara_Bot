from __future__ import annotations

from pathlib import Path
import tomllib


def test_pyproject_includes_quote_background_as_package_data() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    package_data = data["tool"]["setuptools"]["package-data"]

    assert "selara" in package_data
    assert "images/*.png" in package_data["selara"]
