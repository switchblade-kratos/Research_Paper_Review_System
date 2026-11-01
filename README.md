# Paper Review System

DBMS Experiential Learning, Level 3 project.

A conference paper reviewing system:
- **Authors** submit papers and see only their own papers.
- **Reviewers** see and review only the papers in their domain.
- **Admins** see everything, decide paper status and view reports.

Search ranks papers by meaning, using Qwen3-Embedding vectors stored in PostgreSQL with pgvector.

Stack: Python (uv), FastAPI, Jinja2 templates, PostgreSQL 16 + pgvector.

## Project layout

| File | What it does |
|---|---|
| `sql/schema.sql` | DDL: DROP, CREATE, ALTER, primary/foreign keys, CHECK/UNIQUE constraints |
| `sql/advanced.sql` | Function, 2 stored procedures, 4 triggers, 3 views, DB roles and GRANTs |
| `sql/indexes.sql` | B-tree, GIN full-text and HNSW vector indexes (run after loading data) |
| `app/config.py` | Reads settings from `.env` (no credentials in code) |
| `app/db.py` | Connection pool, per-role transactions, dashboard / detail / write queries |
| `app/auth.py` | bcrypt passwords, session login, role checks |
| `app/search.py` | Query embedding + cosine similarity search |
| `app/reports.py` | Admin reports: GROUP BY/HAVING, nested subquery, CTE |
| `app/api.py` | JSON API (try it at `/docs`) |
| `app/frontend.py` | HTML pages |
| `app/main.py` | Creates the FastAPI app |
| `scripts/setup_db.py` | Runs the SQL files |
| `scripts/load_papers.py` | Bulk-loads the JSON dataset with embeddings |
| `scripts/seed_demo.py` | Creates demo admin/reviewers and sample reviews |
| `templates/`, `static/` | Page templates and stylesheet |

## Setup

### 1. Install PostgreSQL 16 and pgvector 0.8 or newer

On Ubuntu:
```
sudo apt install postgresql postgresql-16-pgvector
```
If your distro's pgvector is older than 0.8, use the official `pgvector/pgvector:pg16` Docker image instead. Search relies on the `hnsw.iterative_scan` setting, which only exists from 0.8.

### 2. Create the database, app user and extension

Run this once as the postgres superuser:
```
sudo -u postgres psql <<'SQL'
CREATE USER paper_app WITH PASSWORD 'paperpass' CREATEROLE;
CREATE DATABASE paperdb OWNER paper_app;
\c paperdb
CREATE EXTENSION vector;
GRANT ALL ON SCHEMA public TO paper_app;
SQL
```
`CREATEROLE` is needed because `advanced.sql` creates the `author_r`, `reviewer_r` and `admin_r` roles.

### 3. Install Python dependencies and configure
```
uv sync
cp .env.example .env      # then edit DATABASE_URL and SESSION_SECRET
```

### 4. Create the tables, load the data, build the indexes
```
uv run python -m scripts.setup_db
uv run python -m scripts.load_papers data/papers.json --limit 50000
uv run python -m scripts.seed_demo
uv run python -m scripts.setup_db --indexes
```

Warning: `setup_db` without flags **drops and recreates all tables**.

**About the dataset file:**
- It can be a JSON array or JSON Lines (one paper per line). Big files are streamed.
- Default field names follow the arXiv metadata format: `id`, `title`, `abstract`, `authors`, `categories`, `update_date`, `embedding`.
- If your names differ, pass flags, e.g. `--embedding-field vector --domain-field category`. See `--help` for the full list.
- The first category becomes the paper's domain.
- Every author becomes a user, with password `author123`.

**About the embeddings:**
- They must be the raw **1024-dim** Qwen3-Embedding-0.6B vectors, not the PCA-reduced ones.
- The search box embeds the user's query with the same model, so the two have to match.
- Papers without a 1024-dim vector are still loaded, but they won't show up in search.

### 5. Run
```
uv run uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000. The API docs are at http://127.0.0.1:8000/docs.

The first search downloads the embedding model (about 1.2 GB) from Hugging Face.

## Demo logins

| Role | Email | Password |
|---|---|---|
| Admin | admin@demo.com | admin123 |
| Reviewer | reviewer1@demo.com ... reviewer5@demo.com | reviewer123 |
| Author | author@demo.com | author123 |

Each demo reviewer is assigned one of the five largest domains. `author@demo.com` is the dataset author with the most papers.

## How the permissions work

Each app role maps to a PostgreSQL role, and each PostgreSQL role can read **only its own view**:

| App role | DB role | Can read | Can write |
|---|---|---|---|
| author | `author_r` | `v_author_papers` (own papers) | `CALL submit_paper` |
| reviewer | `reviewer_r` | `v_reviewer_papers` (own domain) | `CALL submit_review` |
| admin | `admin_r` | everything | `papers.status`, new users |

For every request, the backend opens a transaction and runs two statements:
- `SET LOCAL ROLE <role>` switches to the user's DB role.
- `set_config('app.user_id', ...)` tells the views who the user is.

So even a bug in the Python code can't show an author someone else's paper, because PostgreSQL refuses the query (`permission denied for table papers`).

## Where each requirement is covered

- **DDL**:
  - `DROP`/`CREATE` at the top of `schema.sql`
  - `ALTER` at its end (adds `updated_at`, the email format check, the comments check)
- **Constraints**:
  - primary and foreign keys
  - `UNIQUE` (email, one review per reviewer per paper)
  - `CHECK` (role, status, score 1–10, email format, reviewers must have a domain)
- **DML**:
  - INSERT: register, submit paper, review
  - UPDATE: status, edit review
  - DELETE: cascades and the audit trigger
  - SELECT: everywhere
- **Queries**:
  - JOINs in `db.get_paper`
  - GROUP BY + HAVING + aggregates, nested (correlated) subquery and CTE in `reports.py`
  - ORDER BY and full-text search in `db.list_papers`
- **Advanced features**:
  - views and role permissions
  - function `paper_avg_score`
  - procedures `submit_paper` (one transaction: a bad co-author rolls back the whole submission) and `submit_review`
  - 4 triggers: validate reviews, refresh score/count/status, keep `updated_at` current, audit log
  - indexes, including GIN full-text
- **Vector**:
  - pgvector `vector(1024)` column
  - HNSW index with cosine distance (`<=>`)
  - similarity = 1 − distance
- **Validation at DB level**: the CHECK constraints plus the `reviews_check` trigger. It rejects reviewing outside your domain, reviewing your own paper, and non-reviewers reviewing.
- **Filtering**: done in SQL `WHERE` clauses with bound parameters, never in the UI.
