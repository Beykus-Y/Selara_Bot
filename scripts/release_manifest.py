"""Build immutable release manifests and deploy their exact image digests."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time


DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
OWNER = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SERVICES = {"app": "app", "web": "web", "artifact-renderer": "app"}
IMAGE_NAMES = {"app": "selara", "web": "selara-web", "gacha": "selara-gacha"}


def validate_manifest(manifest: dict, *, repository: str | None = None,
                      run_id: int | None = None, owner: str | None = None) -> dict:
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Unsupported release manifest")
    source = manifest.get("repository", "")
    if not isinstance(source, str) or not REPOSITORY.fullmatch(source):
        raise ValueError("Invalid release repository")
    if repository is not None and source != repository:
        raise ValueError("Release belongs to another repository")
    commit = manifest.get("commit_sha", "")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError("A full commit SHA is required")
    release_id = manifest.get("publisher_run_id")
    if type(release_id) is not int or release_id <= 0 or (run_id is not None and release_id != run_id):
        raise ValueError("Invalid publisher run ID")
    namespace = manifest.get("image_owner", "")
    if not isinstance(namespace, str) or not OWNER.fullmatch(namespace):
        raise ValueError("Invalid GHCR namespace")
    if owner is not None and namespace != owner.lower():
        raise ValueError("Unexpected GHCR namespace")
    images = manifest.get("images")
    if not isinstance(images, dict) or set(images) != set(IMAGE_NAMES):
        raise ValueError("A complete app/web/gacha release is required")
    for name, image_name in IMAGE_NAMES.items():
        reference = images[name]
        prefix = f"ghcr.io/{namespace}/{image_name}@"
        if (not isinstance(reference, str) or not reference.startswith(prefix)
                or not DIGEST.fullmatch(reference[len(prefix):])):
            raise ValueError(f"Invalid immutable {name} image")
    return manifest


def build_manifest(*, repository: str, run_id: int, commit: str, owner: str,
                   app_digest: str, web_digest: str, gacha_digest: str) -> dict:
    owner = owner.lower()
    digests = {"app": app_digest, "web": web_digest, "gacha": gacha_digest}
    return validate_manifest({
        "schema_version": 1, "repository": repository, "publisher_run_id": run_id,
        "commit_sha": commit, "image_owner": owner,
        "images": {name: f"ghcr.io/{owner}/{image_name}@{digests[name]}"
                   for name, image_name in IMAGE_NAMES.items()},
    })


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def docker(*args: str, env: dict) -> str:
    return subprocess.run(["docker", *args], env=env, check=True, text=True,
                          stdout=subprocess.PIPE, timeout=600).stdout


def deploy_release(manifest: dict, state_dir: Path, *, runner=docker, sleeper=time.sleep) -> dict:
    """Call with an exclusive deployment lock held; promote only verified releases."""
    validate_manifest(manifest)
    current = state_dir / "current.json"
    previous = validate_manifest(json.loads(current.read_text(encoding="utf-8"))) if current.exists() else None
    known_release = state_dir / f"release-{manifest['publisher_run_id']}.json"
    if known_release.exists() and json.loads(known_release.read_text(encoding="utf-8")) != manifest:
        raise ValueError("Cannot change the digests of a known release ID")
    env = dict(os.environ, SELARA_IMAGE=manifest["images"]["app"],
               SELARA_WEB_IMAGE=manifest["images"]["web"])
    # Refuse builds and tag resolution even if the server's .env names :latest.
    runner("compose", "pull", *SERVICES, env=env)
    expected_ids = {}
    for name in ("app", "web"):
        reference = manifest["images"][name]
        image = json.loads(runner("image", "inspect", reference, env=env))[0]
        if reference not in image.get("RepoDigests", []):
            raise ValueError(f"Pulled {name} image digest does not match release")
        if image.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision") != manifest["commit_sha"]:
            raise ValueError(f"Pulled {name} image revision does not match release")
        expected_ids[name] = image["Id"]
    runner("compose", "up", "-d", "--no-build", "--pull", "never", *SERVICES, env=env)
    containers = {}
    for service, image_name in SERVICES.items():
        container_id = runner("compose", "ps", "-q", service, env=env).strip()
        if not container_id or "\n" in container_id:
            raise ValueError(f"Expected one running {service} container")
        container = json.loads(runner("inspect", container_id, env=env))[0]
        reference = manifest["images"][image_name]
        if (container.get("Image") != expected_ids[image_name]
                or container.get("Config", {}).get("Image") != reference
                or not container.get("State", {}).get("Running")):
            raise ValueError(f"Running {service} does not match release")
        containers[service] = {"container_id": container_id,
                               "image_id": expected_ids[image_name], "image": reference}
    app_id = containers["app"]["container_id"]
    # Verify the public frontend is this exact deployed build, not a stale
    # reverse-proxy upstream or an unrelated HTTP 200 page.
    index_hash = runner("exec", containers["web"]["container_id"], "sha256sum",
                        "/usr/share/nginx/html/index.html", env=env).split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", index_hash):
        raise ValueError("Invalid deployed frontend checksum")
    health_command = (
        "import hashlib, json, os, urllib.request; "
        "port = int(os.environ.get('WEB_PORT', '8080')); "
        "urllib.request.urlopen(f'http://127.0.0.1:{port}/readyz', timeout=5).read(); "
        "base = os.environ['WEB_BASE_URL'].rstrip('/'); "
        "ready = json.loads(urllib.request.urlopen(base + '/miniapp/readyz', timeout=5).read()); "
        "assert ready['status'] == 'ok' and all(ready['checks'][key] for key in ('database', 'redis', 'polling')); "
        "page = urllib.request.urlopen(base + '/miniapp/', timeout=5).read(); "
        f"assert hashlib.sha256(page).hexdigest() == '{index_hash}', 'Public frontend build mismatch'"
    )
    for attempt in range(6):
        try:
            runner("exec", app_id, "python", "-c", health_command, env=env)
            break
        except subprocess.CalledProcessError:
            if attempt == 5:
                raise
            sleeper(5)
    # A web/renderer process can exit while the app is starting.
    for service, expected in containers.items():
        container = json.loads(runner("inspect", expected["container_id"], env=env))[0]
        if (container.get("Image") != expected["image_id"]
                or container.get("Config", {}).get("Image") != expected["image"]
                or not container.get("State", {}).get("Running")
                or container.get("State", {}).get("Health", {}).get("Status", "healthy") != "healthy"):
            raise ValueError(f"Final {service} verification failed")
    record = {"release": manifest, "containers": containers, "verified_at": int(time.time())}
    state_dir.mkdir(parents=True, exist_ok=True)
    if previous is not None and previous != manifest:
        atomic_json(state_dir / "previous.json", previous)
    atomic_json(known_release, manifest)
    atomic_json(state_dir / "last-deployment.json", record)
    atomic_json(current, manifest)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("output", type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--run-id", type=int, required=True)
    deploy = commands.add_parser("deploy")
    deploy.add_argument("manifest", help="Manifest path or 'previous' for rollback")
    args = parser.parse_args()
    if args.command == "build":
        manifest = build_manifest(repository=os.environ["GITHUB_REPOSITORY"],
                                  run_id=int(os.environ["GITHUB_RUN_ID"]),
                                  commit=os.environ["RELEASE_COMMIT"], owner=os.environ["IMAGE_OWNER"],
                                  app_digest=os.environ["APP_DIGEST"], web_digest=os.environ["WEB_DIGEST"],
                                  gacha_digest=os.environ["GACHA_DIGEST"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.output, manifest)
    elif args.command == "validate":
        validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")),
                          repository=os.environ["GITHUB_REPOSITORY"], run_id=args.run_id,
                          owner=os.environ["IMAGE_OWNER"])
    else:
        # All workflow and manual deployments share a server-side lock.
        import fcntl

        state_dir = Path.cwd() / ".selara-releases"
        state_dir.mkdir(parents=True, exist_ok=True)
        with (state_dir / "deploy.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest_path = state_dir / "previous.json" if args.manifest == "previous" else Path(args.manifest)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            record = deploy_release(manifest, state_dir)
            print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
