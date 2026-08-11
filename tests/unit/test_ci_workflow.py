from pathlib import Path

import yaml


def test_ci_workflow_checks_backend_gacha_and_frontend() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert {"pull_request", "push"} <= set(workflow["on"])
    jobs = workflow["jobs"]
    assert "backend" in jobs
    assert "frontend" in jobs

    backend_commands = "\n".join(
        f"{step.get('working-directory', '')} {step.get('run', '')}"
        for step in jobs["backend"]["steps"]
    )
    frontend_commands = "\n".join(
        str(step.get("run", "")) for step in jobs["frontend"]["steps"]
    )

    assert "pytest" in backend_commands
    assert "tests/unit" in backend_commands
    assert "tests/integration" in backend_commands
    assert "playwright install --with-deps chromium" in backend_commands
    assert "pip-audit" in backend_commands
    assert any(
        step.get("working-directory") == "gacha" and "pytest" in str(step.get("run", ""))
        for step in jobs["backend"]["steps"]
    )
    assert "npm ci" in frontend_commands
    assert "npm run lint" in frontend_commands
    assert "npm run build" in frontend_commands


def test_cryptography_dependency_includes_security_fixed_release() -> None:
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"
    pyproject = pyproject_path.read_text(encoding="utf-8")

    assert '"cryptography>=50.0.0,<51"' in pyproject
