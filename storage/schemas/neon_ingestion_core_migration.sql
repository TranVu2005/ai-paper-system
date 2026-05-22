-- neon_ingestion_core_migration.sql
-- Purpose: Move to metadata-first ingestion storage on Neon.
-- Safe strategy: additive migration (no destructive drop).

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =========================================================
-- 1) Documents: add core ingestion identity + uri pointers
-- =========================================================
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS doc_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS source_type VARCHAR(32),
    ADD COLUMN IF NOT EXISTS source_file_uri TEXT,
    ADD COLUMN IF NOT EXISTS ingestion_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Backfill from legacy fields where possible
UPDATE documents
SET source_type = COALESCE(source_type, NULLIF(lower(file_type), ''))
WHERE source_type IS NULL;

UPDATE documents
SET doc_id = COALESCE(doc_id, NULLIF(regexp_replace(filename, '\\.[^.]+$', ''), ''))
WHERE doc_id IS NULL;

-- Normalize status vocabulary: processed -> parsed
UPDATE documents
SET status = 'parsed'
WHERE status = 'processed';

-- Replace old status check with expanded lifecycle
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_documents_status_valid'
          AND conrelid = 'documents'::regclass
    ) THEN
        ALTER TABLE documents DROP CONSTRAINT ck_documents_status_valid;
    END IF;
END
$$;

ALTER TABLE documents
    ADD CONSTRAINT ck_documents_status_valid
    CHECK (status IN ('uploaded', 'processing', 'parsed', 'indexed', 'failed'));

CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_doc_id ON documents(doc_id) WHERE doc_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_documents_source_type ON documents(source_type);
CREATE INDEX IF NOT EXISTS ix_documents_updated_at ON documents(updated_at DESC);

-- =========================================================
-- 2) Document metadata: enrich academic fields from ingestion
-- =========================================================
ALTER TABLE document_metadata
    ADD COLUMN IF NOT EXISTS page_count INTEGER,
    ADD COLUMN IF NOT EXISTS publisher TEXT,
    ADD COLUMN IF NOT EXISTS volume VARCHAR(64),
    ADD COLUMN IF NOT EXISTS issue VARCHAR(64),
    ADD COLUMN IF NOT EXISTS pages VARCHAR(64),
    ADD COLUMN IF NOT EXISTS citation_count INTEGER,
    ADD COLUMN IF NOT EXISTS metadata_confidence DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS ingestion_version VARCHAR(64);

CREATE INDEX IF NOT EXISTS ix_document_metadata_doi ON document_metadata(doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_document_metadata_publication_year ON document_metadata(publication_year);
CREATE INDEX IF NOT EXISTS ix_document_metadata_title_trgm_hint ON document_metadata((left(title, 128)));

-- =========================================================
-- 3) Artifact registry: keep heavy data outside relational tables
-- =========================================================
CREATE TABLE IF NOT EXISTS document_artifacts (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    artifact_type VARCHAR(64) NOT NULL,
    uri TEXT NOT NULL,
    checksum_sha256 VARCHAR(64),
    size_bytes BIGINT,
    mime_type VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_document_artifacts_artifact_type_valid CHECK (
        artifact_type IN (
            'unified_json',
            'sections_json',
            'references_json',
            'tables_json',
            'formulas_json',
            'figures_dir',
            'figures_manifest',
            'raw_source_copy'
        )
    ),
    CONSTRAINT ck_document_artifacts_size_non_negative CHECK (size_bytes IS NULL OR size_bytes >= 0),
    CONSTRAINT uq_document_artifact_unique_per_type UNIQUE (document_id, artifact_type)
);

CREATE INDEX IF NOT EXISTS ix_document_artifacts_document_id ON document_artifacts(document_id);
CREATE INDEX IF NOT EXISTS ix_document_artifacts_type_created_at ON document_artifacts(artifact_type, created_at DESC);

-- =========================================================
-- 4) Indexing status: track vector/graph indexing outside heavy payloads
-- =========================================================
CREATE TABLE IF NOT EXISTS document_index_status (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    vector_collection VARCHAR(128),
    chunk_count INTEGER,
    vector_indexed_at TIMESTAMPTZ,
    graph_indexed BOOLEAN NOT NULL DEFAULT FALSE,
    graph_indexed_at TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_document_index_status_chunk_count_non_negative CHECK (chunk_count IS NULL OR chunk_count >= 0)
);

CREATE INDEX IF NOT EXISTS ix_document_index_status_vector_indexed_at ON document_index_status(vector_indexed_at DESC);
CREATE INDEX IF NOT EXISTS ix_document_index_status_graph_indexed_at ON document_index_status(graph_indexed_at DESC);

-- =========================================================
-- 5) Optional guidance: mark heavy legacy tables as deprecated
-- =========================================================
COMMENT ON TABLE document_chunks IS 'DEPRECATED for heavy storage: keep chunk text/embedding in vector store (e.g., Qdrant).';
COMMENT ON TABLE document_graphs IS 'DEPRECATED for heavy storage: keep graph payload in Neo4j or artifact files.';

COMMIT;
