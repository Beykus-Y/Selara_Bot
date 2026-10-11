"""The live provider diagnostic must never make paid calls in CI by accident."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_llm_sdk_provider.py"


@pytest.mark.parametrize(
    ("argv", "exit_code", "expected"),
    [
        ([], 2, "Refusing to call provider without --allow-billing"),
        (["--allow-billing"], 1, "FAIL: RuntimeError (details redacted)"),
    ],
)
def test_paid_provider_smoke_is_opt_in_and_sanitizes_missing_secrets(
    argv: list[str], exit_code: int, expected: str,
) -> None:
    env = os.environ.copy()
    env.pop("LLM_API_KEY", None)
    env.pop("LLM_MODEL", None)
    env.pop("LLM_BASE_URL", None)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=SCRIPT.parents[1], env=env,
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert completed.returncode == exit_code
    assert expected in completed.stderr
    assert completed.stdout == ""
