"""Create demo accounts and sample reviews after the dataset is loaded.

    uv run python -m scripts.seed_demo --domains 5 --reviews 15

Creates:
  admin@demo.com / admin123
  reviewer1@demo.com ... (one per busiest domain) / reviewer123
  author@demo.com / author123  (the most prolific loaded author is renamed to this)
Reviews go through the same triggers as the app, so scores, counts,
statuses and the audit log all get filled in.
"""
import argparse
import random

import psycopg

from app.auth import hash_password
from app.config import DATABASE_URL

COMMENTS = [
    "Clear motivation and solid experiments, but related work could be broader.",
    "The method is interesting; the evaluation needs stronger baselines.",
    "Well written. Some claims in the results section are not fully supported.",
    "Novel idea with limited empirical validation. Needs an ablation study.",
    "Good contribution to the area. Minor issues with notation and clarity.",
    "The problem is relevant but the approach is incremental over prior work.",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--domains", type=int, default=5, help="how many of the busiest domains get a reviewer")
    p.add_argument("--reviews", type=int, default=15, help="reviews written per reviewer")
    args = p.parse_args()
    random.seed(42)

    with psycopg.connect(DATABASE_URL) as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO users(full_name, email, password_hash, role)
                       VALUES ('Demo Admin', 'admin@demo.com', %s, 'admin')
                       ON CONFLICT (email) DO NOTHING""", (hash_password("admin123"),))

        # the author with the most papers becomes author@demo.com
        cur.execute("""UPDATE users SET email = 'author@demo.com', password_hash = %s
                       WHERE user_id = (SELECT user_id FROM paper_authors
                                        GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1)
                         AND NOT EXISTS (SELECT 1 FROM users WHERE email = 'author@demo.com')""",
                    (hash_password("author123"),))

        cur.execute("""SELECT domain_id, COUNT(*) FROM papers GROUP BY domain_id
                       ORDER BY COUNT(*) DESC LIMIT %s""", (args.domains,))
        domains = [r[0] for r in cur.fetchall()]
        rev_hash = hash_password("reviewer123")

        for i, domain_id in enumerate(domains, start=1):
            cur.execute("""INSERT INTO users(full_name, email, password_hash, role, domain_id)
                           VALUES (%s, %s, %s, 'reviewer', %s)
                           ON CONFLICT (email) DO UPDATE SET domain_id = EXCLUDED.domain_id
                           RETURNING user_id""",
                        (f"Reviewer {i}", f"reviewer{i}@demo.com", rev_hash, domain_id))
            reviewer_id = cur.fetchone()[0]
            # review the newest papers in the domain, plus the demo author's papers in it
            cur.execute("""(SELECT paper_id FROM papers WHERE domain_id = %s
                            ORDER BY submitted_at DESC LIMIT %s)
                           UNION
                           (SELECT pa.paper_id FROM paper_authors pa
                            JOIN users u ON u.user_id = pa.user_id
                            JOIN papers p ON p.paper_id = pa.paper_id
                            WHERE u.email = 'author@demo.com' AND p.domain_id = %s LIMIT 3)""",
                        (domain_id, args.reviews, domain_id))
            for (paper_id,) in cur.fetchall():
                cur.execute("""INSERT INTO reviews(paper_id, reviewer_id, score, comments)
                               VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                            (paper_id, reviewer_id, random.randint(2, 10), random.choice(COMMENTS)))

        # decide some papers so the stats pages have accepted/rejected data
        cur.execute("UPDATE papers SET status = 'accepted' WHERE review_count > 0 AND avg_score >= 7")
        cur.execute("UPDATE papers SET status = 'rejected' WHERE review_count > 0 AND avg_score <= 4")
        conn.commit()
        print(f"seeded admin, {len(domains)} reviewers and their reviews")


if __name__ == "__main__":
    main()
