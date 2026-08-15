from pathlib import Path
import json

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
    assert "Jinja2" in frontend_commands
    assert "npm run lint" in frontend_commands
    assert "npm run build" in frontend_commands


def test_cryptography_dependency_includes_security_fixed_release() -> None:
    pyproject_path = Path(__file__).parents[2] / "pyproject.toml"
    pyproject = pyproject_path.read_text(encoding="utf-8")

    assert '"cryptography>=50.0.0,<51"' in pyproject


def test_frontend_lint_command_includes_server_ui_javascript() -> None:
    root = Path(__file__).parents[2]
    package = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    server_config = root / "frontend" / "eslint.server-ui.config.js"

    assert "lint:server-ui" in package["scripts"]
    assert "lint:server-ui" in package["scripts"]["lint"]
    assert "lint:server-ui:js" in package["scripts"]
    assert "lint:server-ui:css" in package["scripts"]
    assert "lint:server-ui:html" in package["scripts"]
    assert "src/selara/web/static" in package["scripts"]["lint:server-ui:js"]
    assert "src/selara/web/static" in package["scripts"]["lint:server-ui:css"]
    assert server_config.is_file()
    assert "globals.browser" in server_config.read_text(encoding="utf-8")
    stylelint_config = root / "frontend" / "stylelint.server-ui.config.mjs"
    assert stylelint_config.is_file()
    assert "stylelint-config-standard" in stylelint_config.read_text(encoding="utf-8")
    assert (root / "frontend" / ".htmlhintrc").is_file()
    assert (root / "scripts" / "render_server_ui_fixture.py").is_file()
