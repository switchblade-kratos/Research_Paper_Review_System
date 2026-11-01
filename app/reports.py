"""Admin reports: one query of each kind (GROUP BY/HAVING, nested subquery, CTE, aggregates)."""
from app import db

# GROUP BY + HAVING + aggregate functions + JOIN + ORDER BY
REVIEWER_WORKLOAD = """
SELECT u.full_name AS reviewer, d.name AS domain,
       COUNT(r.review_id)          AS reviews_done,
       ROUND(AVG(r.score), 2)      AS avg_score_given,
       MIN(r.score)                AS lowest,
       MAX(r.score)                AS highest
FROM reviews r
JOIN users u   ON u.user_id = r.reviewer_id
JOIN domains d ON d.domain_id = u.domain_id
GROUP BY u.user_id, u.full_name, d.name
HAVING COUNT(r.review_id) >= %s
ORDER BY reviews_done DESC, avg_score_given DESC
"""

# NESTED (correlated) SUBQUERY: papers scoring above the average of their own domain
ABOVE_DOMAIN_AVERAGE = """
SELECT p.paper_id, p.title, d.name AS domain, p.avg_score
FROM papers p
JOIN domains d ON d.domain_id = p.domain_id
WHERE p.avg_score > (SELECT AVG(p2.avg_score)
                     FROM papers p2
                     WHERE p2.domain_id = p.domain_id)
ORDER BY p.avg_score DESC
LIMIT 20
"""

# CTE (WITH): per-domain statistics, then joined to domain names
DOMAIN_STATS = """
WITH domain_stats AS (
    SELECT domain_id,
           COUNT(*)                                      AS total,
           COUNT(*) FILTER (WHERE status = 'accepted')   AS accepted,
           COUNT(*) FILTER (WHERE status = 'rejected')   AS rejected,
           COUNT(*) FILTER (WHERE review_count = 0)      AS unreviewed
    FROM papers
    GROUP BY domain_id
)
SELECT d.name AS domain, s.total, s.accepted, s.rejected, s.unreviewed,
       ROUND(100.0 * s.accepted / NULLIF(s.accepted + s.rejected, 0), 1) AS acceptance_pct
FROM domain_stats s
JOIN domains d ON d.domain_id = s.domain_id
ORDER BY s.total DESC
LIMIT 25
"""

# simple GROUP BY: paper counts per status
STATUS_COUNTS = "SELECT status, COUNT(*) AS n FROM papers GROUP BY status ORDER BY status"

RECENT_AUDIT = """SELECT table_name, action, row_id, changed_at
                  FROM audit_log ORDER BY changed_at DESC LIMIT 15"""


def admin_reports(user, min_reviews: int = 1) -> dict:
    with db.as_role(user["role"], user["user_id"]) as cur:
        out = {}
        cur.execute(STATUS_COUNTS);              out["status_counts"] = cur.fetchall()
        cur.execute(REVIEWER_WORKLOAD, (min_reviews,)); out["workload"] = cur.fetchall()
        cur.execute(ABOVE_DOMAIN_AVERAGE);       out["above_avg"] = cur.fetchall()
        cur.execute(DOMAIN_STATS);               out["domain_stats"] = cur.fetchall()
        cur.execute(RECENT_AUDIT);               out["audit"] = cur.fetchall()
    return out
