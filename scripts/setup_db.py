"""Create the schema (or the indexes) by running the SQL files.

    uv run python -m scripts.setup_db            # schema.sql + advanced.sql (wipes tables!)
    uv run python -m scripts.setup_db --indexes  # indexes.sql (run after loading data)
"""
import sys
from pathlib import Path

import psycopg

from app.config import DATABASE_URL

SQL_DIR = Path(__file__).parent.parent / "sql"


def run_file(conn, name: str):
    print(f"running {name} ...")
    conn.execute((SQL_DIR / name).read_text())


def main():
    files = ["indexes.sql"] if "--indexes" in sys.argv else ["schema.sql", "advanced.sql"]
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.add_notice_handler(lambda n: None)   # hide "does not exist, skipping" notices
        for name in files:
            run_file(conn, name)
    print("done")


if __name__ == "__main__":
    main()
