"""Vector search: embed text with Qwen3-Embedding and rank papers by cosine similarity (pgvector)."""
from functools import lru_cache

from psycopg import sql

from app import db
from app.config import EMBED_DIM, EMBED_MODEL


@lru_cache(maxsize=1)
def _model():
    # imported lazily so the app starts fast; the model loads on the first search
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)


def _to_pgvector(values) -> str:
    if len(values) != EMBED_DIM:
        raise ValueError(f"Expected a {EMBED_DIM}-dim embedding, got {len(values)}")
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


def embed_query(text: str) -> str:
    """Qwen3-Embedding uses an instruction prompt for queries (not for documents)."""
    vec = _model().encode([text], prompt_name="query", normalize_embeddings=True)[0]
    return _to_pgvector(vec)


def embed_document(text: str) -> str:
    vec = _model().encode([text], normalize_embeddings=True)[0]
    return _to_pgvector(vec)


def search(user, query_text: str, limit: int = 10):
    """Top papers by cosine similarity, restricted to what the user's role can see.

    `<=>` is pgvector's cosine distance, so similarity = 1 - distance.
    - reviewer/admin: HNSW index with iterative scan, so the view's filter
      still returns enough rows on a huge table (needs pgvector >= 0.8).
    - author: only a handful of own papers, so an exact scan over a
      MATERIALIZED CTE is used (the index would mostly return other people's papers).
    """
    qvec = embed_query(query_text)
    view = sql.Identifier(db.VIEW[user["role"]])
    if user["role"] == "author":
        query = sql.SQL(
            "WITH mine AS MATERIALIZED (SELECT * FROM {view} WHERE embedding IS NOT NULL) "
            "SELECT paper_id, title, domain_name, status, "
            "       ROUND((1 - (embedding <=> %s::vector))::numeric, 4) AS similarity "
            "FROM mine ORDER BY embedding <=> %s::vector LIMIT %s").format(view=view)
    else:
        query = sql.SQL(
            "SELECT paper_id, title, domain_name, status, "
            "       ROUND((1 - (embedding <=> %s::vector))::numeric, 4) AS similarity "
            "FROM {view} WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector LIMIT %s").format(view=view)
    with db.as_role(user["role"], user["user_id"]) as cur:
        cur.execute("SET LOCAL hnsw.ef_search = 100")
        cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        cur.execute(query, (qvec, qvec, limit))
        return cur.fetchall()
