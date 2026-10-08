"""CI/image smoke check: all installed distributions must come from uv.lock."""

from importlib import metadata
from pathlib import Path
import re
import sys
import tomllib


def check_runtime(lock_path: Path, project: str) -> dict[str, str]:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    allowed: dict[str, set[str]] = {}
    for package in lock["package"]:
        name = re.sub(r"[-_.]+", "-", package["name"]).lower()
        allowed.setdefault(name, set()).add(package["version"])
    installed = {}
    for distribution in metadata.distributions():
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        if distribution.version not in allowed.get(name, set()):
            raise RuntimeError(f"Unmatched installed distribution: {name}=={distribution.version}")
        installed[name] = distribution.version
    if project not in installed:
        raise RuntimeError(f"Missing installed project: {project}")
    return installed


if __name__ == "__main__":
    installed = check_runtime(Path(sys.argv[1]), sys.argv[2])
    print("\n".join(f"{name}=={version}" for name, version in sorted(installed.items())))
