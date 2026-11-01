-- indexes.sql : run AFTER loading the dataset (building indexes on a full table is much faster)

DROP INDEX IF EXISTS idx_papers_domain_status, idx_papers_submitted, idx_papers_fts,
                     idx_papers_embedding, idx_paper_authors_user, idx_reviews_reviewer;

-- B-tree: dashboard filters by domain + status and sorts by date
CREATE INDEX idx_papers_domain_status ON papers (domain_id, status);
CREATE INDEX idx_papers_submitted     ON papers (submitted_at DESC);

-- B-tree on foreign keys used by the views and joins
CREATE INDEX idx_paper_authors_user ON paper_authors (user_id);
CREATE INDEX idx_reviews_reviewer   ON reviews (reviewer_id);

-- GIN: full-text keyword search over title + abstract
CREATE INDEX idx_papers_fts ON papers
    USING GIN (to_tsvector('english', title || ' ' || abstract));

-- HNSW: approximate nearest-neighbour search on embeddings with cosine distance
CREATE INDEX idx_papers_embedding ON papers
    USING hnsw (embedding vector_cosine_ops);

ANALYZE papers;
