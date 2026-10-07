from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_gacha_image_declares_non_root_after_dependency_installation():
    dockerfile = (ROOT / "gacha/Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert dockerfile.index("USER 10001:10001") > dockerfile.index("pip install .")
    assert "chown gacha:gacha /data" in dockerfile


def test_gacha_compose_enforces_security_and_writable_paths():
    compose = yaml.safe_load((ROOT / "gacha/docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["gacha"]
    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert any(path.startswith("/tmp:rw,") and "size=" in path and "mode=1777" in path for path in service["tmpfs"])
    assert "gacha_runtime_data:/data" in service["volumes"]
    assert "gacha_runtime_data" in compose["volumes"]
    assert service["mem_limit"] == "1g"
    assert float(service["cpus"]) == 1.0
    assert service["pids_limit"] == 256
