import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("release_manifest", ROOT / "scripts" / "release_manifest.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def manifest(run_id=123):
    return release.build_manifest(repository="Beykus-Y/Selara_Bot", run_id=run_id,
                                  commit="a" * 40, owner="Beykus-Y",
                                  app_digest="sha256:" + "b" * 64,
                                  web_digest="sha256:" + "c" * 64,
                                  gacha_digest="sha256:" + "d" * 64)


@pytest.mark.parametrize("change", [
    lambda m: m.update(commit_sha="abc1234"),
    lambda m: m.update(publisher_run_id=True),
    lambda m: m["images"].update(app="ghcr.io/beykus-y/selara:latest"),
    lambda m: m["images"].update(web="ghcr.io/other/selara-web@sha256:" + "c" * 64),
    lambda m: m["images"].pop("gacha"),
    lambda m: m["images"].update(app="ghcr.io/beykus-y/selara@sha256:" + "b" * 64 + "\n"),
])
def test_invalid_or_incomplete_release_is_rejected(change):
    data = manifest()
    change(data)
    with pytest.raises(ValueError):
        release.validate_manifest(data)


def test_manifest_is_bound_to_publisher_repository_and_namespace():
    data = manifest()
    assert release.validate_manifest(data, repository="Beykus-Y/Selara_Bot", run_id=123, owner="Beykus-Y") is data
    for kwargs in ({"repository": "other/repo"}, {"run_id": 456}, {"owner": "other"}):
        with pytest.raises(ValueError):
            release.validate_manifest(data, **kwargs)


class DockerFake:
    def __init__(self, data, *, mismatch=None, health_fail=False):
        self.data = data
        self.mismatch = mismatch
        self.health_fail = health_fail
        self.calls = []

    def __call__(self, *args, env):
        self.calls.append(args)
        assert env["SELARA_IMAGE"] == self.data["images"]["app"]
        assert env["SELARA_WEB_IMAGE"] == self.data["images"]["web"]
        if args[:2] == ("image", "inspect"):
            reference = args[2]
            name = "app" if reference == self.data["images"]["app"] else "web"
            return json.dumps([{
                "Id": "id-" + name,
                "RepoDigests": [] if self.mismatch == "digest" else [reference],
                "Config": {"Labels": {"org.opencontainers.image.revision":
                                       "e" * 40 if self.mismatch == "revision" else self.data["commit_sha"]}},
            }])
        if args[:3] == ("compose", "ps", "-q"):
            return args[3] + "-container\n"
        if args[0] == "inspect":
            service = args[1].removesuffix("-container")
            name = release.SERVICES[service]
            return json.dumps([{
                "Image": "wrong" if self.mismatch == "container-id" else "id-" + name,
                "Config": {"Image": "latest" if self.mismatch == "container-ref" else self.data["images"][name]},
                "State": {"Running": self.mismatch != "stopped"},
            }])
        if args[:3] == ("exec", "web-container", "sha256sum"):
            return hashlib.sha256(b'<html><div id="root"></div></html>\n').hexdigest() + "  index.html\n"
        if args[0] == "exec" and self.health_fail:
            raise subprocess.CalledProcessError(1, args)
        return ""


def test_repeated_deploy_uses_exact_digests_and_retains_previous_release(tmp_path, monkeypatch):
    monkeypatch.setenv("SELARA_IMAGE", "ghcr.io/other/selara:latest")
    old = manifest(100)
    release.atomic_json(tmp_path / "current.json", old)
    new = manifest(123)
    runner = DockerFake(new)
    for _ in range(2):
        record = release.deploy_release(new, tmp_path, runner=runner, sleeper=lambda _: None)
        assert json.loads((tmp_path / "previous.json").read_text()) == old
        assert json.loads((tmp_path / "current.json").read_text()) == new
        assert record["containers"]["artifact-renderer"]["image"] == new["images"]["app"]
    assert ("compose", "up", "-d", "--no-build", "--pull", "never", "app", "web", "artifact-renderer") in runner.calls
    assert all("build" not in command for command in runner.calls)
    # A previous manifest can be deployed as-is without resolving moving tags.
    release.deploy_release(old, tmp_path, runner=DockerFake(old), sleeper=lambda _: None)
    assert json.loads((tmp_path / "current.json").read_text()) == old
    assert json.loads((tmp_path / "previous.json").read_text()) == new


@pytest.mark.parametrize("mismatch", ["digest", "revision", "container-id", "container-ref", "stopped"])
def test_image_mismatch_never_promotes_release(tmp_path, mismatch):
    old = manifest(100)
    release.atomic_json(tmp_path / "current.json", old)
    new = manifest()
    runner = DockerFake(new, mismatch=mismatch)
    with pytest.raises(ValueError):
        release.deploy_release(new, tmp_path, runner=runner, sleeper=lambda _: None)
    assert json.loads((tmp_path / "current.json").read_text()) == old
    assert not (tmp_path / "previous.json").exists()
    assert not (tmp_path / "last-deployment.json").exists()
    if mismatch in {"digest", "revision"}:
        assert not any(command[:2] == ("compose", "up") for command in runner.calls)


def test_failed_health_check_preserves_known_release(tmp_path):
    old = manifest(100)
    release.atomic_json(tmp_path / "current.json", old)
    runner = DockerFake(manifest(), health_fail=True)
    with pytest.raises(subprocess.CalledProcessError):
        release.deploy_release(manifest(), tmp_path, runner=runner, sleeper=lambda _: None)
    assert json.loads((tmp_path / "current.json").read_text()) == old
    assert len([call for call in runner.calls if call[:2] == ("exec", "app-container")]) == 6


def test_known_release_id_cannot_be_rebound_to_new_digest(tmp_path):
    old = manifest()
    release.atomic_json(tmp_path / "release-123.json", old)
    changed = manifest()
    changed["images"]["app"] = "ghcr.io/beykus-y/selara@sha256:" + "f" * 64
    runner = DockerFake(changed)
    with pytest.raises(ValueError, match="known release ID"):
        release.deploy_release(changed, tmp_path, runner=runner)
    assert runner.calls == []


def test_renderer_exit_during_app_start_does_not_promote(tmp_path):
    old = manifest(100)
    release.atomic_json(tmp_path / "current.json", old)
    runner = DockerFake(manifest())

    def crash_after_app_health(*args, env):
        result = runner(*args, env=env)
        if args[:2] == ("exec", "app-container"):
            runner.mismatch = "stopped"
        return result

    with pytest.raises(ValueError, match="Final"):
        release.deploy_release(manifest(), tmp_path, runner=crash_after_app_health)
    assert json.loads((tmp_path / "current.json").read_text()) == old


@pytest.mark.parametrize("failure", [None, "public-not-ready", "wrong-frontend"])
def test_deploy_probes_local_and_public_readiness_and_exact_frontend(tmp_path, monkeypatch, failure):
    import urllib.request

    monkeypatch.setenv("WEB_BASE_URL", "https://bot.example.com/")
    monkeypatch.setenv("WEB_PORT", "8080")
    visited = []

    def public_http(url, timeout):
        visited.append(url)
        if url.endswith("/miniapp/"):
            body = b"old build" if failure == "wrong-frontend" else b'<html><div id="root"></div></html>\n'
        else:
            body = json.dumps({"status": "ok", "checks": {
                "database": True, "redis": True,
                "polling": not (failure == "public-not-ready" and "example.com" in url),
            }}).encode()
        return type("Response", (), {"read": lambda self: body})()

    monkeypatch.setattr(urllib.request, "urlopen", public_http)
    runner = DockerFake(manifest())

    def probe_runner(*args, env):
        result = runner(*args, env=env)
        if args[:2] == ("exec", "app-container"):
            try:
                exec(args[-1], {})
            except AssertionError as error:
                raise subprocess.CalledProcessError(1, args) from error
        return result

    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            release.deploy_release(manifest(), tmp_path, runner=probe_runner, sleeper=lambda _: None)
        assert not (tmp_path / "current.json").exists()
    else:
        release.deploy_release(manifest(), tmp_path, runner=probe_runner)
        assert {"http://127.0.0.1:8080/readyz", "https://bot.example.com/miniapp/readyz", "https://bot.example.com/miniapp/"} <= set(visited)


def test_release_workflows_use_manifest_and_fail_before_ssh():
    publish = yaml.load((ROOT / ".github/workflows/docker-publish.yml").read_text(), Loader=yaml.BaseLoader)
    deploy = yaml.load((ROOT / ".github/workflows/deploy-vps.yml").read_text(), Loader=yaml.BaseLoader)
    steps = publish["jobs"]["publish"]["steps"]
    builds = [step for step in steps if step.get("uses") == "docker/build-push-action@v6"]
    assert {step["id"] for step in builds} == {"build_app", "build_web", "build_gacha"}
    artifact = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")
    assert artifact["with"]["overwrite"] == "false"
    assert artifact["with"]["if-no-files-found"] == "error"
    assert deploy["on"]["workflow_dispatch"]["inputs"]["release_run_id"]["required"] == "true"
    deploy_steps = deploy["jobs"]["deploy"]["steps"]
    download = next(step for step in deploy_steps if step.get("uses") == "actions/download-artifact@v4")
    assert download["with"]["name"] == artifact["with"]["name"]
    assert "inputs.release_run_id" in download["with"]["run-id"]
    names = [step["name"] for step in deploy_steps]
    assert names.index("Validate release before connecting to VPS") < names.index("Copy manifest and deployment driver")
    assert ":latest" not in json.dumps(deploy)
    assert "deploy-vps" == deploy["concurrency"]["group"]
