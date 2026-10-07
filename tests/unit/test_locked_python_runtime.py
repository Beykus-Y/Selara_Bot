import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("check_locked_python_runtime", ROOT / "scripts/check_locked_python_runtime.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


@pytest.mark.parametrize("version,expected", [("1.2", True), ("1.3", False)])
def test_installed_version_must_come_from_lock(tmp_path, monkeypatch, version, expected):
    path = tmp_path / "uv.lock"
    path.write_text('[[package]]\nname="selara"\nversion="0.1.0"\n'
                    '[[package]]\nname="example-package"\nversion="1.2"\n')
    monkeypatch.setattr(check.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "selara"}, version="0.1.0"),
        SimpleNamespace(metadata={"Name": "Example_Package"}, version=version),
    ])
    if expected:
        assert check.check_runtime(path, "selara")["example-package"] == "1.2"
    else:
        with pytest.raises(RuntimeError, match="Unmatched installed distribution"):
            check.check_runtime(path, "selara")


def test_missing_workspace_package_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "uv.lock"
    path.write_text('[[package]]\nname="selara-gacha"\nversion="0.1.0"\n')
    monkeypatch.setattr(check.metadata, "distributions", lambda: [])
    with pytest.raises(RuntimeError, match="Missing installed project"):
        check.check_runtime(path, "selara-gacha")
