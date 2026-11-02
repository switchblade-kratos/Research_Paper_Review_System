-- advanced.sql : function, procedures, triggers, views, roles and permissions

-- helper: id of the user the backend set for this transaction
CREATE OR REPLACE FUNCTION app_user_id() RETURNS INT
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')::INT
$$;

-- STORED FUNCTION: live average score of one paper
CREATE OR REPLACE FUNCTION paper_avg_score(p_paper_id INT) RETURNS NUMERIC
LANGUAGE sql STABLE AS $$
    SELECT ROUND(AVG(score)::NUMERIC, 2) FROM reviews WHERE paper_id = p_paper_id
$$;

-- TRIGGER 1: keep papers.updated_at current
CREATE OR REPLACE FUNCTION trg_touch_paper() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

CREATE TRIGGER papers_touch BEFORE UPDATE ON papers
FOR EACH ROW EXECUTE FUNCTION trg_touch_paper();

-- TRIGGER 2: validate a review before it is saved (data integrity at DB level)
CREATE OR REPLACE FUNCTION trg_check_review() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r_role   VARCHAR;
    r_domain INT;
    p_domain INT;
BEGIN
    SELECT role, domain_id INTO r_role, r_domain FROM users WHERE user_id = NEW.reviewer_id;
    SELECT domain_id INTO p_domain FROM papers WHERE paper_id = NEW.paper_id;
    IF r_role IS DISTINCT FROM 'reviewer' THEN
        RAISE EXCEPTION 'Only reviewers can review papers';
    END IF;
    IF r_domain IS DISTINCT FROM p_domain THEN
        RAISE EXCEPTION 'Reviewer can only review papers in their own domain';
    END IF;
    IF EXISTS (SELECT 1 FROM paper_authors WHERE paper_id = NEW.paper_id AND user_id = NEW.reviewer_id) THEN
        RAISE EXCEPTION 'Reviewer cannot review their own paper';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER reviews_check BEFORE INSERT OR UPDATE ON reviews
FOR EACH ROW EXECUTE FUNCTION trg_check_review();

-- TRIGGER 3: after a review changes, refresh the paper's score, count and status
CREATE OR REPLACE FUNCTION trg_refresh_score() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    pid INT := COALESCE(NEW.paper_id, OLD.paper_id);
BEGIN
    UPDATE papers
       SET avg_score    = paper_avg_score(pid),
           review_count = (SELECT COUNT(*) FROM reviews WHERE paper_id = pid),
           status       = CASE WHEN status = 'submitted' THEN 'under_review' ELSE status END
     WHERE paper_id = pid;
    RETURN NULL;
END $$;

CREATE TRIGGER reviews_refresh AFTER INSERT OR UPDATE OR DELETE ON reviews
FOR EACH ROW EXECUTE FUNCTION trg_refresh_score();

-- TRIGGER 4: audit log of changes to papers and reviews
-- TG_ARGV[0] is the name of the table's id column, passed in CREATE TRIGGER
CREATE OR REPLACE FUNCTION trg_audit() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    rec JSONB := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
BEGIN
    INSERT INTO audit_log(table_name, action, row_id)
    VALUES (TG_TABLE_NAME, TG_OP, (rec ->> TG_ARGV[0])::INT);
    RETURN NULL;
END $$;

CREATE TRIGGER papers_audit AFTER UPDATE OR DELETE ON papers
FOR EACH ROW EXECUTE FUNCTION trg_audit('paper_id');
CREATE TRIGGER reviews_audit AFTER INSERT OR UPDATE OR DELETE ON reviews
FOR EACH ROW EXECUTE FUNCTION trg_audit('review_id');

-- TRIGGER 5: reject a paper whose title already exists (case and extra spaces ignored)
CREATE OR REPLACE FUNCTION norm_title(t TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT lower(regexp_replace(trim(t), '\s+', ' ', 'g'))
$$;

-- lets the trigger's lookup use an index instead of scanning every paper
CREATE INDEX idx_papers_title_norm ON papers (norm_title(title));

CREATE OR REPLACE FUNCTION trg_check_duplicate_title() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM papers
               WHERE norm_title(title) = norm_title(NEW.title)
                 AND paper_id <> NEW.paper_id) THEN
        RAISE EXCEPTION 'A paper titled "%" already exists', NEW.title;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER papers_unique_title BEFORE INSERT OR UPDATE OF title ON papers
FOR EACH ROW EXECUTE FUNCTION trg_check_duplicate_title();

-- STORED PROCEDURE 1 (TRANSACTION): submit a paper and its authors together.
-- If any co-author email is unknown, the whole submission is rolled back.
CREATE OR REPLACE PROCEDURE submit_paper(
    p_title TEXT, p_abstract TEXT, p_domain_id INT, p_coauthor_emails TEXT[],
    p_embedding TEXT, INOUT p_paper_id INT DEFAULT NULL)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    uid INT := app_user_id();
    em  TEXT;
    co  INT;
    n   INT := 2;
BEGIN
    IF (SELECT role FROM users WHERE user_id = uid) IS DISTINCT FROM 'author' THEN
        RAISE EXCEPTION 'Only authors can submit papers';
    END IF;
    INSERT INTO papers(title, abstract, domain_id, embedding)
    VALUES (p_title, p_abstract, p_domain_id, p_embedding::vector)
    RETURNING paper_id INTO p_paper_id;

    INSERT INTO paper_authors(paper_id, user_id, author_order) VALUES (p_paper_id, uid, 1);

    FOREACH em IN ARRAY COALESCE(p_coauthor_emails, ARRAY[]::TEXT[]) LOOP
        SELECT user_id INTO co FROM users WHERE email = lower(trim(em)) AND role = 'author';
        IF co IS NULL THEN
            RAISE EXCEPTION 'Co-author % is not a registered author', em;
        END IF;
        INSERT INTO paper_authors(paper_id, user_id, author_order)
        VALUES (p_paper_id, co, n) ON CONFLICT DO NOTHING;
        n := n + 1;
    END LOOP;
END $$;

-- STORED PROCEDURE 2: reviewer adds or edits their review of a paper
CREATE OR REPLACE PROCEDURE submit_review(p_paper_id INT, p_score INT, p_comments TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    INSERT INTO reviews(paper_id, reviewer_id, score, comments)
    VALUES (p_paper_id, app_user_id(), p_score, p_comments)
    ON CONFLICT (paper_id, reviewer_id)
    DO UPDATE SET score = EXCLUDED.score, comments = EXCLUDED.comments, created_at = now();
END $$;

-- VIEWS: one per role. Each role can only read its own view.
CREATE VIEW v_author_papers AS
SELECT p.paper_id, p.title, p.abstract, p.domain_id, d.name AS domain_name, p.status,
       p.submitted_at, p.avg_score, p.review_count, p.embedding
FROM papers p JOIN domains d ON d.domain_id = p.domain_id
WHERE EXISTS (SELECT 1 FROM paper_authors pa
              WHERE pa.paper_id = p.paper_id AND pa.user_id = app_user_id());

CREATE VIEW v_reviewer_papers AS
SELECT p.paper_id, p.title, p.abstract, p.domain_id, d.name AS domain_name, p.status,
       p.submitted_at, p.avg_score, p.review_count, p.embedding
FROM papers p JOIN domains d ON d.domain_id = p.domain_id
WHERE p.domain_id = (SELECT u.domain_id FROM users u WHERE u.user_id = app_user_id());

CREATE VIEW v_admin_papers AS
SELECT p.paper_id, p.title, p.abstract, p.domain_id, d.name AS domain_name, p.status,
       p.submitted_at, p.avg_score, p.review_count, p.embedding
FROM papers p JOIN domains d ON d.domain_id = p.domain_id;

-- ROLES (cluster-wide, so only create them if missing)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'author_r')   THEN CREATE ROLE author_r NOLOGIN;   END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reviewer_r') THEN CREATE ROLE reviewer_r NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_r')    THEN CREATE ROLE admin_r NOLOGIN;    END IF;
    -- the app's login user must be able to SET ROLE to these
    EXECUTE format('GRANT author_r, reviewer_r, admin_r TO %I', current_user);
END $$;

-- PERMISSIONS
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM author_r, reviewer_r, admin_r;
-- procedures are executable by everyone by default, so lock them down first
REVOKE EXECUTE ON PROCEDURE submit_paper(TEXT, TEXT, INT, TEXT[], TEXT, INT) FROM PUBLIC;
REVOKE EXECUTE ON PROCEDURE submit_review(INT, INT, TEXT) FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO author_r, reviewer_r, admin_r;

GRANT SELECT  ON v_author_papers TO author_r;
GRANT SELECT  ON domains TO author_r;
GRANT EXECUTE ON PROCEDURE submit_paper(TEXT, TEXT, INT, TEXT[], TEXT, INT) TO author_r;

GRANT SELECT  ON v_reviewer_papers TO reviewer_r;
GRANT EXECUTE ON PROCEDURE submit_review(INT, INT, TEXT) TO reviewer_r;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO admin_r;
GRANT UPDATE (status) ON papers TO admin_r;
GRANT INSERT ON users TO admin_r;
GRANT USAGE ON SEQUENCE users_user_id_seq TO admin_r;