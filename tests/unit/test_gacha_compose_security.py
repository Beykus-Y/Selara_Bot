from pathlib import Path

import yaml


def test_gacha_compose_keeps_database_private_and_api_loopback_by_default() -> None:
    compose_path = Path(__file__).parents[2] / "gacha" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    postgres = compose["services"]["postgres"]
    gacha = compose["services"]["gacha"]

    assert not postgres.get("ports")
    assert any(str(port).startswith("${GACHA_BIND_HOST:-127.0.0.1}:") for port in gacha["ports"])
    assert "GACHA_SERVICE_TOKEN" in gacha["environment"]


def test_gacha_compose_requires_database_and_service_secrets() -> None:
    compose_path = Path(__file__).parents[2] / "gacha" / "docker-compose.yml"
    compose_text = compose_path.read_text(encoding="utf-8")

    assert "GACHA_POSTGRES_PASSWORD:?" in compose_text
    assert "GACHA_DATABASE_URL:?" in compose_text
    assert "GACHA_SERVICE_TOKEN:?" in compose_text
    assert "GACHA_POSTGRES_PASSWORD:-gacha" not in compose_text
    assert "GACHA_SERVICE_TOKEN:-}" not in compose_text
