-- schema.sql : tables, keys and constraints (DDL)
-- Run order: schema.sql -> advanced.sql -> (load data) -> indexes.sql

-- DROP: start clean every time this file is run
DROP VIEW IF EXISTS v_author_papers, v_reviewer_papers, v_admin_papers CASCADE;
DROP TABLE IF EXISTS audit_log, reviews, paper_authors, papers, users, domains CASCADE;

-- CREATE
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE domains (
    domain_id   SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    CONSTRAINT domain_name_not_blank CHECK (length(trim(name)) > 0)
);

CREATE TABLE users (
    user_id       SERIAL PRIMARY KEY,
    full_name     VARCHAR(200) NOT NULL,
    email         VARCHAR(200) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          VARCHAR(10) NOT NULL DEFAULT 'author',
    domain_id     INT REFERENCES domains(domain_id) ON DELETE SET NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT role_valid CHECK (role IN ('author', 'reviewer', 'admin')),
    -- a reviewer must belong to a domain
    CONSTRAINT reviewer_has_domain CHECK (role <> 'reviewer' OR domain_id IS NOT NULL)
);

CREATE TABLE papers (
    paper_id      SERIAL PRIMARY KEY,
    source_id     VARCHAR(100) UNIQUE,          -- id from the original dataset
    title         TEXT NOT NULL,
    abstract      TEXT NOT NULL,
    domain_id     INT NOT NULL REFERENCES domains(domain_id),
    status        VARCHAR(20) NOT NULL DEFAULT 'submitted',
    submitted_at  DATE NOT NULL DEFAULT CURRENT_DATE,
    avg_score     NUMERIC(4,2),                 -- kept up to date by a trigger
    review_count  INT NOT NULL DEFAULT 0,       -- kept up to date by a trigger
    embedding     vector(1024),                 -- Qwen3-Embedding-0.6B, 1024 dims
    CONSTRAINT status_valid CHECK (status IN ('submitted', 'under_review', 'accepted', 'rejected')),
    CONSTRAINT title_length CHECK (length(trim(title)) BETWEEN 5 AND 500),
    CONSTRAINT abstract_length CHECK (length(trim(abstract)) >= 20)
);

-- many-to-many: a paper has many authors, an author has many papers
CREATE TABLE paper_authors (
    paper_id     INT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    user_id      INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    author_order INT NOT NULL DEFAULT 1 CHECK (author_order >= 1),
    PRIMARY KEY (paper_id, user_id)
);

CREATE TABLE reviews (
    review_id    SERIAL PRIMARY KEY,
    paper_id     INT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    reviewer_id  INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    score        INT NOT NULL CHECK (score BETWEEN 1 AND 10),
    comments     TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT one_review_per_reviewer UNIQUE (paper_id, reviewer_id)
);

CREATE TABLE audit_log (
    log_id      SERIAL PRIMARY KEY,
    table_name  VARCHAR(50) NOT NULL,
    action      VARCHAR(10) NOT NULL,
    row_id      INT,
    changed_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- ALTER: changes made after the first version of the schema
ALTER TABLE papers ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT now();
ALTER TABLE users ADD CONSTRAINT email_format
    CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$');
ALTER TABLE reviews ADD CONSTRAINT comments_not_blank CHECK (length(trim(comments)) >= 10);
