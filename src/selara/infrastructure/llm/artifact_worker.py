"""Private renderer entrypoint: run only in the isolated artifact-renderer container."""
import asyncio

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from selara.infrastructure.llm.artifact_rendering import render_static_pages

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_gate = asyncio.Semaphore(1)


class RenderInput(BaseModel):
    pages: list[str] = Field(min_length=1, max_length=3)
    css: str = Field(default="", max_length=12000)


@app.get("/healthz")
async def health():
    return {"ok": True}


@app.post("/render")
async def render(request: Request):
    # Stream with a byte bound; Content-Length alone is not trustworthy.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 256000:
            raise HTTPException(413, "Слишком большой запрос.")
    try:
        payload = RenderInput.model_validate_json(body)
        async with asyncio.timeout(20):
            async with _gate:
                return await render_static_pages(payload.pages, payload.css)
    except (ValueError, TimeoutError) as exc:
        raise HTTPException(422, "Рендер не прошёл проверку: " + str(exc)[:600]) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090, limit_concurrency=4)
