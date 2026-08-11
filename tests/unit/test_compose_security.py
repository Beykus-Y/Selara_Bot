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
