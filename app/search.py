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


def warm_up():
    """Load the model and run one tiny query so the first real search is fast.
    Called in a background thread at startup; a failure here only means the
    first search loads the model itself, as before."""
    try:
        embed_query("warm up")
    except Exception as err:
        print(f"Embedding model warm-up failed: {err}")


def embed_document(text: str) -> str:
    vec = _model().encode([text], normalize_embeddings=True)[0]
    return _to_pgvector(vec)


SIMILARITY = "ROUND((1 - (embedding <=> %s::vector))::numeric, 4) AS similarity"


def search(user, query_text: str, limit: int = 10):
    """Exact title matches first, then the closest papers by meaning.

    Both steps read only the user's role view, so permissions still apply.
    1. Exact title: norm_title() ignores case and extra spaces and uses the
       idx_papers_title_norm index, so an exact title is never missed.
    2. Meaning: `<=>` is pgvector's cosine distance, so similarity = 1 - distance.
       - reviewer/admin: HNSW index with iterative scan, so the view's filter
         still returns enough rows on a huge table (needs pgvector >= 0.8).
       - author: only a handful of own papers, so an exact scan over a
         MATERIALIZED CTE is used (the index would mostly return other people's papers).
    """
    qvec = embed_query(query_text)
    view = sql.Identifier(db.VIEW[user["role"]])
    exact_query = sql.SQL(
        "SELECT paper_id, title, domain_name, status, " + SIMILARITY + ", true AS exact "
        "FROM {view} WHERE norm_title(title) = norm_title(%s) LIMIT %s").format(view=view)
    if user["role"] == "author":
        near_query = sql.SQL(
            "WITH mine AS MATERIALIZED (SELECT * FROM {view} WHERE embedding IS NOT NULL) "
            "SELECT paper_id, title, domain_name, status, " + SIMILARITY + ", false AS exact "
            "FROM mine ORDER BY embedding <=> %s::vector LIMIT %s").format(view=view)
    else:
        near_query = sql.SQL(
            "SELECT paper_id, title, domain_name, status, " + SIMILARITY + ", false AS exact "
            "FROM {view} WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector LIMIT %s").format(view=view)
    with db.as_role(user["role"], user["user_id"]) as cur:
        cur.execute(exact_query, (qvec, query_text, limit))
        results = cur.fetchall()
        cur.execute("SET LOCAL hnsw.ef_search = 100")
        cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        cur.execute(near_query, (qvec, qvec, limit))
        seen = {r["paper_id"] for r in results}
        results += [r for r in cur.fetchall() if r["paper_id"] not in seen]
    return results[:limit]