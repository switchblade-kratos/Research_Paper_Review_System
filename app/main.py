"""App entry point: uv run uvicorn app.main:app --reload"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import api, db, frontend
from app.config import SESSION_SECRET


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.pool.open()
    yield
    db.pool.close()


app = FastAPI(title="Paper Review System", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=8 * 3600)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "static")), name="static")
app.include_router(api.router)
app.include_router(frontend.router)
