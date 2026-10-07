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
    assert app["cpus"]
    # 4g covers one gacha reel render peak (~1.2-1.4 GB), the in-process
    # Chromium and the tmpfs pages charged to this same cgroup.
    assert app["mem_limit"] == "4g"
    # pids_limit counts threads; Chromium, ffmpeg and the asyncio thread pool
    # share it.
    assert app["pids_limit"] >= 512
    # PID 1 is `sh -c "alembic upgrade head && python -m selara.main"`, which
    # does not reap orphaned Chromium/crashpad children.
    assert app["init"] is True
    tmpfs = next(str(entry) for entry in app["tmpfs"] if str(entry).startswith("/tmp:"))
    # The daily backup stages a pg_dump plus all of its ~45 MB split parts and
    # never deletes the source dump.
    assert "size=1g" in tmpfs
    # $HOME is on the read-only root fs, so the config home must be on /tmp.
    assert app["environment"]["XDG_CONFIG_HOME"].startswith("/tmp")


def test_app_persistent_writes_are_limited_to_gacha_cache_volume() -> None:
    compose = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]

    assert app["volumes"] == ["selara_gacha_reel_cache:/data/gacha_reel_cache"]
    mount = next(str(entry) for entry in app["volumes"] if str(entry).startswith("selara_gacha_reel_cache:"))
    assert app["environment"]["GACHA_REEL_CACHE_DIR"] == mount.split(":", 1)[1]


def test_only_the_gacha_reel_cache_volume_pins_its_name() -> None:
    compose = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8"))
    volumes = compose["volumes"]

    # Without the pin, Compose prefixes the project name and INSTALLATION.md
    # 4.4 would target the wrong volume; the cache is regenerable, so replacing
    # it is safe.
    assert volumes["selara_gacha_reel_cache"]["name"] == "selara_gacha_reel_cache"
    # Pinning these would address a different volume than the project-prefixed
    # one a running deployment uses, orphaning the production data.
    for name in ("selara_pg_data", "selara_redis_data"):
        assert "name" not in (volumes.get(name) or {})


def test_dockerfile_defaults_to_non_root_runtime_user() -> None:
    dockerfile_text = (Path(__file__).parents[2] / "Dockerfile").read_text(encoding="utf-8")

    assert "USER 10001:10001" in dockerfile_text
    # Root is still needed for apt/pip/playwright during the build; the USER
    # switch must come after those steps.
    assert dockerfile_text.index("playwright install") < dockerfile_text.index("USER 10001:10001")
    # The gacha reel cache mount point must really be created with the runtime
    # owner so a fresh named volume inherits it (a comment naming the path is
    # not enough).
    assert "mkdir -p /data/gacha_reel_cache" in dockerfile_text
    assert "chown selara:selara /data/gacha_reel_cache" in dockerfile_text
    assert dockerfile_text.index("mkdir -p /data/gacha_reel_cache") < dockerfile_text.index(
        "chown selara:selara /data/gacha_reel_cache"
    )
    # Matplotlib and fontconfig caches must live on the /tmp tmpfs instead of
    # the unwritable $HOME on the read-only root fs.
    assert "MPLCONFIGDIR=/tmp/" in dockerfile_text
    assert "XDG_CACHE_HOME=/tmp/" in dockerfile_text
