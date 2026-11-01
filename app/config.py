"""Configuration: everything comes from environment variables (.env), nothing is hard-coded."""
import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable {name}. Copy .env.example to .env and fill it in.")
    return value


DATABASE_URL = _required("DATABASE_URL")
SESSION_SECRET = _required("SESSION_SECRET")
EMBED_MODEL = os.getenv("EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")
EMBED_DIM = 1024
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "20"))
