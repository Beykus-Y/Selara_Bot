from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from gacha_service.config import settings
from gacha_service.infrastructure.db import create_engine, create_session_factory
from gacha_service.web.api import build_router


engine = create_engine(settings.database_url)
session_factory = create_session_factory(engine)


app = FastAPI(title="Selara Gacha", version="0.1.0")
images_dir = settings.images_dir
app.mount("/images", StaticFiles(directory=images_dir), name="images")
app.include_router(build_router(session_factory))
