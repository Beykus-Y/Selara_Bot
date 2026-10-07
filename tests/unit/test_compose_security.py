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


def test_app_container_runs_non_root_and_hardened_like_renderer() -> None:
    compose = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]

    assert app["user"] == "10001:10001"
    assert app["read_only"] and app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["mem_limit"] and app["cpus"] and app["pids_limit"]
    assert any(str(entry).startswith("/tmp:") for entry in app["tmpfs"])


def test_app_persistent_writes_are_limited_to_gacha_cache_volume() -> None:
    compose = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]

    assert app["volumes"] == ["selara_gacha_reel_cache:/data/gacha_reel_cache"]


def test_dockerfile_defaults_to_non_root_runtime_user() -> None:
    dockerfile_text = (Path(__file__).parents[2] / "Dockerfile").read_text(encoding="utf-8")

    assert "USER 10001:10001" in dockerfile_text
    # Root is still needed for apt/pip/playwright during the build; the USER
    # switch must come after those steps.
    assert dockerfile_text.index("playwright install") < dockerfile_text.index("USER 10001:10001")
    # The gacha reel cache mount point must exist with the runtime owner so a
    # fresh named volume inherits it.
    assert "/data/gacha_reel_cache" in dockerfile_text
