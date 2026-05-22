-- ai-paper-system relational schema (shared)
-- Source of truth moved from backend to storage/schemas

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =========================
-- USERS
-- =========================
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    full_name VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    role VARCHAR(32) NOT NULL DEFAULT 'user',
    auth_provider VARCHAR(32) NOT NULL DEFAULT 'local',
    google_sub VARCHAR(255) UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    CONSTRAINT ck_users_role_valid CHECK (role IN ('user', 'admin')),
    CONSTRAINT ck_users_auth_provider_valid CHECK (auth_provider IN ('local', 'google'))
);

CREATE INDEX IF NOT EXISTS ix_users_created_at ON users(created_at DESC);

-- =========================
-- WORKSPACES
-- =========================
CREATE TABLE IF NOT EXISTS workspaces (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL DEFAULT 'Untitled notebook',
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_workspaces_user_is_deleted ON workspaces(user_id, is_deleted);
CREATE INDEX IF NOT EXISTS ix_workspaces_updated_at ON workspaces(updated_at DESC);

-- =========================
-- DOCUMENTS
-- =========================
CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type VARCHAR(32) NOT NULL,
    raw_text TEXT,
    doc_id VARCHAR(255) UNIQUE,
    source_type VARCHAR(32),
    source_file_uri TEXT,
    ingestion_version VARCHAR(64),
    status VARCHAR(32) NOT NULL DEFAULT 'uploaded',
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id BIGINT REFERENCES workspaces(id) ON DELETE SET NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_documents_status_valid CHECK (status IN ('uploaded', 'processing', 'parsed', 'indexed', 'failed'))
);

CREATE INDEX IF NOT EXISTS ix_documents_user_workspace_created_at ON documents(user_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_documents_status_created_at ON documents(status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_documents_workspace_id ON documents(workspace_id);
CREATE INDEX IF NOT EXISTS ix_documents_source_type ON documents(source_type);
CREATE INDEX IF NOT EXISTS ix_documents_updated_at ON documents(updated_at DESC);

-- =========================
-- DOCUMENT METADATA (1:1)
-- =========================
CREATE TABLE IF NOT EXISTS document_metadata (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT,
    abstract TEXT,
    publication_year INTEGER,
    source TEXT,
    language VARCHAR(16) DEFAULT 'vi',
    authors JSONB NOT NULL DEFAULT '[]'::jsonb,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    topics JSONB NOT NULL DEFAULT '[]'::jsonb,
    methods JSONB NOT NULL DEFAULT '[]'::jsonb,
    doi VARCHAR(255),
    page_count INTEGER,
    publisher TEXT,
    volume VARCHAR(64),
    issue VARCHAR(64),
    pages VARCHAR(64),
    citation_count INTEGER,
    metadata_confidence DOUBLE PRECISION,
    ingestion_version VARCHAR(64),
    external_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_document_metadata_doi ON document_metadata(doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_document_metadata_publication_year ON document_metadata(publication_year);

-- =========================
-- DOCUMENT CHUNKS (DEPRECATED: keep heavy chunk payload in Qdrant/cache)
-- =========================
CREATE TABLE IF NOT EXISTS document_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding JSONB,
    CONSTRAINT uq_document_chunks_document_id_chunk_index UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS ix_document_chunks_document_id ON document_chunks(document_id);

-- =========================
-- SUMMARIES
-- =========================
CREATE TABLE IF NOT EXISTS document_summaries (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_document_summaries_document_id_created_at ON document_summaries(document_id, created_at DESC);

-- =========================
-- GRAPH SNAPSHOT (1:1) (DEPRECATED: prefer Neo4j/artifact payload)
-- =========================
CREATE TABLE IF NOT EXISTS document_graphs (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    nodes JSONB NOT NULL DEFAULT '[]'::jsonb,
    edges JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================
-- DOCUMENT ARTIFACTS (JSON/files pointer registry)
-- =========================
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

-- =========================
-- DOCUMENT INDEX STATUS (vector/graph runtime index state)
-- =========================
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

-- =========================
-- RECOMMENDATIONS
-- =========================
CREATE TABLE IF NOT EXISTS document_recommendations (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    recommended_document_id BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    reason TEXT,
    score DOUBLE PRECISION,
    source VARCHAR(64),
    external_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_document_recommendations_document_id ON document_recommendations(document_id);
CREATE INDEX IF NOT EXISTS ix_document_recommendations_recommended_document_id ON document_recommendations(recommended_document_id);

-- =========================
-- QA HISTORY
-- =========================
CREATE TABLE IF NOT EXISTS qa_history (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_qa_history_user_document_created_at ON qa_history(user_id, document_id, created_at DESC);

-- =========================
-- AI JOBS
-- =========================
CREATE TABLE IF NOT EXISTS document_jobs (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    job_type VARCHAR(64) NOT NULL DEFAULT 'process_document',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    requested_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    payload JSONB,
    result JSONB,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_document_jobs_status_valid CHECK (status IN ('pending', 'running', 'done', 'failed')),
    CONSTRAINT ck_document_jobs_retry_count_non_negative CHECK (retry_count >= 0)
);

CREATE INDEX IF NOT EXISTS ix_document_jobs_status_created_at ON document_jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_document_jobs_requested_by_user_id ON document_jobs(requested_by_user_id);

-- =========================
-- AUTH: REFRESH TOKENS
-- =========================
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id BIGSERIAL PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    user_email VARCHAR(255) NOT NULL,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    device_id VARCHAR(255) NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_email ON refresh_tokens(user_email);
CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS ix_refresh_tokens_device_id ON refresh_tokens(device_id);

-- =========================
-- AUTH: PASSWORD RESET OTP
-- =========================
CREATE TABLE IF NOT EXISTS password_reset_codes (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_password_reset_codes_email ON password_reset_codes(email);
CREATE INDEX IF NOT EXISTS ix_password_reset_codes_code_hash ON password_reset_codes(code_hash);

-- =========================
-- WORKER MONITORING
-- =========================
CREATE TABLE IF NOT EXISTS worker_status (
    id BIGSERIAL PRIMARY KEY,
    worker_name VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL DEFAULT 'online',
    current_job_id BIGINT,
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
