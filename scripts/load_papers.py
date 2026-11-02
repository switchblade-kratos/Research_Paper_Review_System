"""Bulk-load the papers dataset (JSON array or JSON Lines) with its 1024-dim embeddings.

    uv run python -m scripts.load_papers data/papers.json --limit 50000

Field names default to the arXiv metadata format and can be changed with flags
(see --help). Every author in the dataset becomes an `author` user whose password
is AUTHOR_PASSWORD below, so you can log in as any of them for the demo.
"""
import argparse
import datetime as dt
import json
import re

import ijson
import psycopg
from psycopg.errors import RaiseException

from app.auth import hash_password
from app.config import DATABASE_URL, EMBED_DIM

AUTHOR_PASSWORD = "author123"
BATCH = 1000
MAX_AUTHORS = 10


def read_records(path):
    """Streams records so huge files never sit fully in memory."""
    with open(path, "rb") as f:
        first = f.read(1)
        while first.isspace():
            first = f.read(1)
        f.seek(0)
        if first == b"[":
            yield from ijson.items(f, "item", use_float=True)
        else:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def parse_authors(value):
    if isinstance(value, list):     # ["A B", ...] or arXiv authors_parsed [["Last","First",""], ...]
        names = [" ".join(reversed([p for p in a[:2] if p])) if isinstance(a, list) else str(a) for a in value]
    else:
        names = re.split(r",|\band\b", str(value or ""))
    names = [re.sub(r"\s+", " ", n).strip() for n in names]
    return [n[:200] for n in names if len(n) > 1][:MAX_AUTHORS]


def parse_domain(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    value = str(value or "").split()
    return value[0][:100] if value else "general"


def parse_date(value):
    text = str(value or "")
    for fmt, size in (("%Y-%m-%d", 10), ("%Y", 4)):
        try:
            return dt.datetime.strptime(text[:size], fmt).date()
        except ValueError:
            pass
    return dt.date.today()


def to_vector(value):
    if not isinstance(value, list) or len(value) != EMBED_DIM:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in value) + "]"


class Loader:
    def __init__(self, conn, args):
        self.conn, self.args = conn, args
        self.domains, self.authors, self.seen = {}, {}, set()
        self.pw_hash = hash_password(AUTHOR_PASSWORD)   # one hash reused: bcrypt per row is too slow
        self.next_email = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] + 1
        # titles already in the DB, normalised the same way as the duplicate-title trigger
        self.titles = {t for (t,) in conn.execute("SELECT norm_title(title) FROM papers")}
        self.stats = {"papers": 0, "skipped": 0, "no_embedding": 0}

    def domain_id(self, name):
        if name not in self.domains:
            self.domains[name] = self.conn.execute(
                "INSERT INTO domains(name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
                "RETURNING domain_id", (name,)).fetchone()[0]
        return self.domains[name]

    def add_authors(self, names):
        new = [n for n in dict.fromkeys(names) if n not in self.authors]
        if not new:
            return
        rows = []
        for n in new:
            rows.append((n, f"author{self.next_email}@papers.local", self.pw_hash))
            self.next_email += 1
        with self.conn.cursor() as cur:
            cur.executemany("INSERT INTO users(full_name, email, password_hash, role) "
                            "VALUES (%s, %s, %s, 'author') RETURNING user_id", rows, returning=True)
            for n in new:
                self.authors[n] = cur.fetchone()[0]
                cur.nextset()

    def flush(self, batch):
        if not batch:
            return
        self.add_authors([n for rec in batch for n in rec["authors"]])
        self.conn.commit()          # keep authors even if some papers below are rejected
        try:
            self.insert_papers(batch)
        except RaiseException:
            # the duplicate-title trigger rejected a paper: redo this batch one paper at a time
            self.conn.rollback()
            for rec in batch:
                try:
                    self.insert_papers([rec])
                except RaiseException:
                    self.conn.rollback()
                    self.stats["skipped"] += 1
        print(f"  loaded {self.stats['papers']} papers", flush=True)

    def insert_papers(self, batch):
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO papers(source_id, title, abstract, domain_id, submitted_at, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s::vector) RETURNING paper_id",
                [(r["sid"], r["title"], r["abstract"], r["domain_id"], r["date"], r["vec"]) for r in batch],
                returning=True)
            links = []
            for rec in batch:
                pid = cur.fetchone()[0]
                cur.nextset()
                links += [(pid, self.authors[n], i + 1) for i, n in enumerate(dict.fromkeys(rec["authors"]))]
            cur.executemany("INSERT INTO paper_authors(paper_id, user_id, author_order) VALUES (%s, %s, %s)", links)
        self.conn.commit()
        self.stats["papers"] += len(batch)

    def run(self):
        a, batch = self.args, []
        for raw in read_records(a.file):
            if a.limit and self.stats["papers"] + len(batch) >= a.limit:
                break
            title = " ".join(str(raw.get(a.title_field) or "").split())
            abstract = " ".join(str(raw.get(a.abstract_field) or "").split())
            sid = str(raw.get(a.id_field) or "") or None
            authors = parse_authors(raw.get(a.authors_field))
            if len(title) < 5 or len(abstract) < 20 or not authors or (sid and sid in self.seen):
                self.stats["skipped"] += 1
                continue
            title = title[:500]
            if title.lower() in self.titles:      # same title as an earlier paper: skip it
                self.stats["skipped"] += 1
                continue
            self.titles.add(title.lower())
            if sid:
                self.seen.add(sid)
            vec = to_vector(raw.get(a.embedding_field))
            self.stats["no_embedding"] += vec is None
            batch.append(dict(sid=sid, title=title[:500], abstract=abstract, authors=authors, vec=vec,
                              domain_id=self.domain_id(parse_domain(raw.get(a.domain_field))),
                              date=parse_date(raw.get(a.date_field))))
            if len(batch) >= BATCH:
                self.flush(batch)
                batch = []
        self.flush(batch)
        print("finished:", self.stats)
        if self.stats["no_embedding"]:
            print(f"warning: {self.stats['no_embedding']} papers had no {EMBED_DIM}-dim embedding "
                  f"(field '{a.embedding_field}'); they won't appear in vector search.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--limit", type=int, default=0, help="max papers to load (0 = all)")
    p.add_argument("--id-field", default="id")
    p.add_argument("--title-field", default="title")
    p.add_argument("--abstract-field", default="abstract")
    p.add_argument("--authors-field", default="authors")
    p.add_argument("--domain-field", default="categories")
    p.add_argument("--date-field", default="update_date")
    p.add_argument("--embedding-field", default="embedding")
    args = p.parse_args()
    with psycopg.connect(DATABASE_URL) as conn:
        Loader(conn, args).run()


if __name__ == "__main__":
    main()