from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from gacha_service.config import settings
from gacha_service.infrastructure.db import create_engine, create_session_factory, init_db
from gacha_service.web.api import build_router


engine = create_engine(settings.database_url)
session_factory = create_session_factory(engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db(engine)
    yield


app = FastAPI(title="Selara Gacha", version="0.1.0", lifespan=lifespan)
images_dir = settings.images_dir
app.mount("/images", StaticFiles(directory=images_dir), name="images")
app.include_router(build_router(session_factory))
