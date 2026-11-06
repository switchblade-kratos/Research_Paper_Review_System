"""App entry point: uv run uvicorn app.main:app --reload"""
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import api, db, frontend, search
from app.config import SESSION_SECRET


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.pool.open()
    # load the embedding model in the background so the server starts at once
    # and the first search doesn't have to wait for it
    threading.Thread(target=search.warm_up, daemon=True).start()
    yield
    db.pool.close()


app = FastAPI(title="Paper Review System", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=8 * 3600)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "static")), name="static")
app.include_router(api.router)
app.include_router(frontend.router)