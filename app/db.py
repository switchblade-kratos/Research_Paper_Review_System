"""Database layer: connection pool, per-role transactions and all core SQL.

Every request that reads papers runs inside `as_role()`, which does
SET LOCAL ROLE + SET LOCAL app.user_id. The role can only read its own view,
so permissions are enforced by PostgreSQL itself, not just by Python.
"""
from contextlib import contextmanager

import psycopg
from psycopg import errors, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import DATABASE_URL, PAGE_SIZE

pool = ConnectionPool(DATABASE_URL, open=False, kwargs={"row_factory": dict_row})

DB_ROLE = {"author": "author_r", "reviewer": "reviewer_r", "admin": "admin_r"}
VIEW = {"author": "v_author_papers", "reviewer": "v_reviewer_papers", "admin": "v_admin_papers"}

# whitelisted ORDER BY options (never put user text straight into SQL)
SORTS = {
    "newest": "submitted_at DESC, paper_id DESC",
    "oldest": "submitted_at ASC, paper_id ASC",
    "score": "avg_score DESC NULLS LAST, paper_id",
    "title": "title ASC",
}
STATUSES = ["submitted", "under_review", "accepted", "rejected"]


@contextmanager
def as_app():
    """Plain transaction as the app's own login user (used for login/registration)."""
    with pool.connection() as conn, conn.transaction():
        yield conn.cursor()


@contextmanager
def as_role(role: str, user_id: int):
    """Transaction running as the user's database role, with app.user_id set."""
    with pool.connection() as conn, conn.transaction():
        cur = conn.cursor()
        cur.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(DB_ROLE[role])))
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
        yield cur


# ---------- users ----------
def get_user_by_email(email: str):
    with as_app() as cur:
        cur.execute("SELECT * FROM users WHERE email = %s", (email.lower().strip(),))
        return cur.fetchone()


def create_user(full_name, email, password_hash, role="author", domain_id=None, acting=None):
    """Registration runs as the app; admins creating reviewers run as admin_r."""
    query = """INSERT INTO users(full_name, email, password_hash, role, domain_id)
               VALUES (%s, %s, %s, %s, %s) RETURNING user_id"""
    params = (full_name.strip(), email.lower().strip(), password_hash, role, domain_id)
    ctx = as_role(acting["role"], acting["user_id"]) if acting else as_app()
    with ctx as cur:
        cur.execute(query, params)
        return cur.fetchone()["user_id"]


def list_domains():
    with as_app() as cur:
        cur.execute("SELECT domain_id, name FROM domains ORDER BY name")
        return cur.fetchall()


# ---------- dashboard (all filtering happens here, in SQL) ----------
def list_papers(user, f: dict):
    """Default dashboard list from the role's view, filtered and sorted in the database."""
    where, params = [], []
    if f.get("status") in STATUSES:
        where.append("status = %s"); params.append(f["status"])
    if f.get("domain_id"):
        where.append("domain_id = %s"); params.append(int(f["domain_id"]))
    if f.get("year_from"):
        where.append("submitted_at >= make_date(%s, 1, 1)"); params.append(int(f["year_from"]))
    if f.get("year_to"):
        where.append("submitted_at <= make_date(%s, 12, 31)"); params.append(int(f["year_to"]))
    if f.get("keyword"):
        where.append("to_tsvector('english', title || ' ' || abstract) @@ plainto_tsquery('english', %s)")
        params.append(f["keyword"])
    if f.get("min_score"):
        where.append("avg_score >= %s"); params.append(float(f["min_score"]))

    order = SORTS.get(f.get("sort"), SORTS["newest"])
    page = max(int(f.get("page") or 1), 1)
    query = sql.SQL(
        "SELECT paper_id, title, domain_name, status, submitted_at, avg_score, review_count "
        "FROM {view} {where} ORDER BY {order} LIMIT %s OFFSET %s"
    ).format(
        view=sql.Identifier(VIEW[user["role"]]),
        where=sql.SQL("WHERE " + " AND ".join(where) if where else ""),
        order=sql.SQL(order),
    )
    params += [PAGE_SIZE + 1, (page - 1) * PAGE_SIZE]   # fetch one extra to know if there is a next page
    with as_role(user["role"], user["user_id"]) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


# ---------- paper detail ----------
def get_paper(user, paper_id: int):
    """Visibility is checked through the role's view, then details are fetched with JOINs."""
    with as_role(user["role"], user["user_id"]) as cur:
        cur.execute(sql.SQL("SELECT paper_id FROM {} WHERE paper_id = %s")
                    .format(sql.Identifier(VIEW[user["role"]])), (paper_id,))
        if cur.fetchone() is None:
            return None
        cur.execute("RESET ROLE")
        # JOIN query: paper + domain
        cur.execute("""SELECT p.paper_id, p.title, p.abstract, p.status, p.submitted_at,
                              p.avg_score, p.review_count, d.name AS domain_name
                       FROM papers p JOIN domains d ON d.domain_id = p.domain_id
                       WHERE p.paper_id = %s""", (paper_id,))
        paper = cur.fetchone()
        # JOIN query: authors in order
        cur.execute("""SELECT u.full_name, u.email FROM paper_authors pa
                       JOIN users u ON u.user_id = pa.user_id
                       WHERE pa.paper_id = %s ORDER BY pa.author_order""", (paper_id,))
        paper["authors"] = cur.fetchall()
        # JOIN query: reviews with reviewer names
        cur.execute("""SELECT r.reviewer_id, u.full_name AS reviewer, r.score, r.comments, r.created_at
                       FROM reviews r JOIN users u ON u.user_id = r.reviewer_id
                       WHERE r.paper_id = %s ORDER BY r.created_at DESC""", (paper_id,))
        paper["reviews"] = cur.fetchall()
    return paper


# ---------- writes (go through stored procedures / role permissions) ----------
def submit_paper(user, title, abstract, domain_id, coauthors: list, embedding: str | None):
    with as_role(user["role"], user["user_id"]) as cur:
        cur.execute("CALL submit_paper(%s, %s, %s, %s, %s, NULL)",
                    (title, abstract, domain_id, coauthors, embedding))
        return cur.fetchone()["p_paper_id"]


def submit_review(user, paper_id: int, score: int, comments: str):
    with as_role(user["role"], user["user_id"]) as cur:
        cur.execute("CALL submit_review(%s, %s, %s)", (paper_id, score, comments))


def set_status(user, paper_id: int, status: str):
    with as_role(user["role"], user["user_id"]) as cur:
        cur.execute("UPDATE papers SET status = %s WHERE paper_id = %s", (status, paper_id))
        return cur.rowcount


def friendly_error(err: psycopg.Error) -> str:
    """Turn a database error (constraint, trigger, permission) into a readable message."""
    diag = getattr(err, "diag", None)
    if isinstance(err, errors.UniqueViolation):
        return f"Already exists (rule: {diag.constraint_name})"
    if isinstance(err, errors.CheckViolation):
        return f"Invalid value (rule: {diag.constraint_name})"
    if isinstance(err, errors.InsufficientPrivilege):
        return "Permission denied by the database for your role"
    if diag is not None and diag.message_primary:
        return diag.message_primary
    return str(err)
