from pathlib import Path
import json

import yaml


def _commands(job: dict) -> str:
    return "\n".join(
        f"{step.get('working-directory', '')} {step.get('run', '')}"
        for step in job["steps"]
    )


def test_ci_workflow_checks_backend_gacha_and_frontend() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert {"pull_request", "push"} <= set(workflow["on"])
    jobs = workflow["jobs"]

    # Branch protection requires checks named exactly `backend` and `frontend`;
    # they are aggregators that fail unless every parallel job succeeded.
    for aggregator, parts in {
        "backend": {"backend-checks", "backend-unit", "backend-integration"},
        "frontend": {"frontend-static", "frontend-browser"},
    }.items():
        assert jobs[aggregator]["name"] == aggregator
        assert set(jobs[aggregator]["needs"]) == parts
        assert jobs[aggregator]["if"] == "${{ always() }}"
        assert "success" in _commands(jobs[aggregator])

    checks = _commands(jobs["backend-checks"])
    unit = _commands(jobs["backend-unit"])
    integration = _commands(jobs["backend-integration"])
    frontend_commands = _commands(jobs["frontend-static"]) + _commands(jobs["frontend-browser"])

    assert "pip-audit" in checks
    assert "python -m compileall" in checks
    assert any(
        step.get("working-directory") == "gacha" and "pytest" in str(step.get("run", ""))
        for step in jobs["backend-checks"]["steps"]
    )
    # Every test file belongs to exactly one shard, so no test is skipped.
    assert "ci_test_shard.py tests/unit" in unit
    assert "ci_test_shard.py tests/integration" in integration
    for command in (unit, integration):
        assert "pytest" in command
        assert "playwright install --with-deps chromium" in command
        assert "alembic upgrade head" in command
    assert "npm ci" in frontend_commands
    assert "uv sync --locked --only-group browser" in frontend_commands
    assert "npm run lint" in frontend_commands
    assert "npm run build" in frontend_commands
    assert "test_miniapp_admin_models_browser.py" in frontend_commands


def test_ci_test_shards_cover_every_test_file_exactly_once() -> None:
    import importlib.util

    root = Path(__file__).parents[2]
    spec = importlib.util.spec_from_file_location("ci_test_shard", root / "scripts" / "ci_test_shard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for tests_dir, count in ((root / "tests" / "unit", 4), (root / "tests" / "integration", 4)):
        files = module.collect(tests_dir)
        shards = module.split(files, count)
        flat = [path for shard in shards for path in shard]
        assert sorted(flat) == files
        assert all(shards)


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
