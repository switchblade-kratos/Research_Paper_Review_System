# Paper Review System

DBMS Experiential Learning, Level 3 project.

A conference paper reviewing system:

- **Authors** submit papers and see only their own papers.
- **Reviewers** see and review only the papers in their domain.
- **Admins** see everything, decide paper status and view reports.

Search ranks papers by meaning, using Qwen3-Embedding vectors stored in PostgreSQL with pgvector.

Stack: Python 3.12 (uv), FastAPI, Jinja2 templates, PostgreSQL 16 + pgvector.

## Project layout

| File                     | What it does                                                               |
| ------------------------ | -------------------------------------------------------------------------- |
| `sql/schema.sql`         | DDL: DROP, CREATE, ALTER, primary/foreign keys, CHECK/UNIQUE constraints   |
| `sql/advanced.sql`       | Functions, 2 stored procedures, 5 triggers, 3 views, DB roles and GRANTs   |
| `sql/indexes.sql`        | B-tree, GIN full-text and HNSW vector indexes (run after loading data)     |
| `app/config.py`          | Reads settings from `.env` (no credentials in code)                        |
| `app/db.py`              | Connection pool, per-role transactions, dashboard / detail / write queries |
| `app/auth.py`            | bcrypt passwords, session login, role checks                               |
| `app/search.py`          | Query embedding + cosine similarity search                                 |
| `app/reports.py`         | Admin reports: GROUP BY/HAVING, nested subquery, CTE                       |
| `app/api.py`             | JSON API (try it at `/docs`)                                               |
| `app/frontend.py`        | HTML pages                                                                 |
| `app/main.py`            | Creates the FastAPI app                                                    |
| `scripts/setup_db.py`    | Runs the SQL files                                                         |
| `scripts/load_papers.py` | Bulk-loads the JSON dataset with embeddings                                |
| `scripts/seed_demo.py`   | Creates demo admin/reviewers and sample reviews                            |
| `templates/`, `static/`  | Page templates and stylesheet                                              |

## Setup

Works on Linux and Windows. Python 3.12 is pinned in `.python-version`; uv downloads it automatically.

On Windows, `uv sync` installs the CUDA build of PyTorch, so search uses an NVIDIA GPU if you have one. On Linux it uses the normal PyPI build, which also uses an NVIDIA GPU if one is present and falls back to CPU otherwise.

### 1. PostgreSQL 16 with pgvector 0.8 or newer

Search relies on the `hnsw.iterative_scan` setting, which only exists from pgvector 0.8.

**Linux (Ubuntu):**

```
sudo apt install postgresql-16 postgresql-16-pgvector
```

If your distro's pgvector is older than 0.8, add the official PostgreSQL apt repo first (`/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh`).

**Linux (Fedora):**

```
sudo dnf install postgresql-server postgresql-contrib pgvector
sudo postgresql-setup --initdb
sudo sed -i 's/\bident\b/scram-sha-256/g' /var/lib/pgsql/data/pg_hba.conf
sudo systemctl enable --now postgresql
```

The `sed` line allows password logins over localhost, which the app needs.

**Windows:** install Docker Desktop (it needs WSL 2: `wsl --install --no-distribution` in an admin PowerShell), then:

```
docker run -d --name paperdb -e POSTGRES_PASSWORD=postgres -p 5432:5432 -v paperdb_data:/var/lib/postgresql/data pgvector/pgvector:pg16
```

After a restart, start it again with `docker start paperdb`.

### 2. Create the database, app user and extension

Run this once. `CREATEROLE` is needed because `advanced.sql` creates the `author_r`, `reviewer_r` and `admin_r` roles.

**Linux:**

```
sudo -u postgres psql -c "CREATE USER paper_app WITH PASSWORD 'paperpass' CREATEROLE;"
sudo -u postgres psql -c "CREATE DATABASE paperdb OWNER paper_app;"
sudo -u postgres psql -d paperdb -c "CREATE EXTENSION vector; GRANT ALL ON SCHEMA public TO paper_app;"
```

**Windows (Docker):**

```
docker exec -it paperdb psql -U postgres -c "CREATE USER paper_app WITH PASSWORD 'paperpass' CREATEROLE;"
docker exec -it paperdb psql -U postgres -c "CREATE DATABASE paperdb OWNER paper_app;"
docker exec -it paperdb psql -U postgres -d paperdb -c "CREATE EXTENSION vector; GRANT ALL ON SCHEMA public TO paper_app;"
```

### 3. Install Python dependencies and configure

```
uv sync
cp .env.example .env        # Windows: copy .env.example .env
```

Then edit `.env` and set `SESSION_SECRET` to any long random string. `DATABASE_URL` already matches step 2.

### 4. Create the tables, load the data, build the indexes

Put the dataset file in `data/` (the folder is git-ignored), then:

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
- Papers whose title repeats an earlier one are skipped (the database also rejects duplicate titles with a trigger).

**About the embeddings:**

- They must be the raw **1024-dim** Qwen3-Embedding-0.6B vectors, not the PCA-reduced ones.
- The search box embeds the user's query with the same model, so the two have to match.
- Papers without a 1024-dim vector are still loaded, but they won't show up in search.

### 5. Run

```
uv run uvicorn app.main:app
```

Open http://127.0.0.1:8000. The API docs are at http://127.0.0.1:8000/docs. Add `--reload` while editing code.

The first search downloads the embedding model (about 1.2 GB) from Hugging Face once; after that it's cached. After each server start, the first search takes a few seconds while the model loads.

### Opening the database for queries

- **Linux:** `psql -h localhost -U paper_app -d paperdb`
- **Windows:** `docker exec -it paperdb psql -U paper_app -d paperdb`

## Demo logins

| Role     | Email                                     | Password    |
| -------- | ----------------------------------------- | ----------- |
| Admin    | admin@demo.com                            | admin123    |
| Reviewer | reviewer1@demo.com ... reviewer5@demo.com | reviewer123 |
| Author   | author@demo.com                           | author123   |

Each demo reviewer is assigned one of the five largest domains. `author@demo.com` is the dataset author with the most papers.

## How the permissions work

Each app role maps to a PostgreSQL role, and each PostgreSQL role can read **only its own view**:

| App role | DB role      | Can read                         | Can write                  |
| -------- | ------------ | -------------------------------- | -------------------------- |
| author   | `author_r`   | `v_author_papers` (own papers)   | `CALL submit_paper`        |
| reviewer | `reviewer_r` | `v_reviewer_papers` (own domain) | `CALL submit_review`       |
| admin    | `admin_r`    | everything                       | `papers.status`, new users |

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
  - 5 triggers: validate reviews, refresh score/count/status, keep `updated_at` current, audit log, reject duplicate titles
  - indexes, including GIN full-text
- **Vector**:
  - pgvector `vector(1024)` column
  - HNSW index with cosine distance (`<=>`)
  - similarity = 1 − distance
- **Validation at DB level**:
  - the CHECK constraints
  - the `reviews_check` trigger, which rejects reviewing outside your domain, reviewing your own paper, and non-reviewers reviewing
  - the `papers_unique_title` trigger, which rejects a title that already exists (case and extra spaces ignored)
- **Filtering**: done in SQL `WHERE` clauses with bound parameters, never in the UI.
