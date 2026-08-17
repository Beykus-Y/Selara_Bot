from __future__ import annotations

import hashlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


def static_assets_version(static_dir: Path) -> str:
    """Content hash of every file under static_dir, used to cache-bust
    static_url() so a long-lived Cache-Control header is safe: the query
    string changes whenever any static file's bytes change, computed once
    per process (picked up automatically on every deploy restart)."""
    hasher = hashlib.sha256()
    for path in sorted(static_dir.rglob("*")):
        if path.is_file():
            hasher.update(path.read_bytes())
    return hasher.hexdigest()[:10]


def create_template_environment(*, template_dir: Path, static_dir: Path | None = None) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    version = static_assets_version(static_dir) if static_dir is not None else None
    suffix = f"?v={version}" if version else ""
    environment.globals["static_url"] = lambda path: f"/static/{str(path).lstrip('/')}{suffix}"
    return environment
