from pathlib import Path

import yaml


def test_main_compose_keeps_database_private_and_requires_credentials() -> None:
    compose_path = Path(__file__).parents[2] / "docker-compose.yml"
    compose_text = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)

    postgres = compose["services"]["postgres"]
    app = compose["services"]["app"]

    assert not postgres.get("ports")
    assert "SELARA_POSTGRES_PASSWORD:?" in compose_text
    assert "SELARA_DATABASE_URL:?" in compose_text
    assert "POSTGRES_PASSWORD: selara" not in compose_text
    assert any(str(port).startswith("127.0.0.1:") for port in app["ports"])


def test_artifact_worker_is_isolated_from_credentials_database_and_internet():
    compose = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.yml").read_text())
    worker = compose["services"]["artifact-renderer"]
    assert worker["user"] == "10001:10001"
    assert worker["read_only"] and worker["cap_drop"] == ["ALL"]
    assert not worker.get("env_file") and not worker.get("volumes") and not worker.get("ports")
    assert worker["networks"] == ["artifact_rendering"]
    assert compose["networks"]["artifact_rendering"]["internal"]
    assert "artifact_rendering" in compose["services"]["app"]["networks"]
