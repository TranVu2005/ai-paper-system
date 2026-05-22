-- Drop metadata columns that are not reliably extracted from ingestion JSON.
-- Run on Neon/Postgres only after confirming no downstream code depends on them.

ALTER TABLE document_metadata
    DROP COLUMN IF EXISTS page_count,
    DROP COLUMN IF EXISTS publisher,
    DROP COLUMN IF EXISTS volume,
    DROP COLUMN IF EXISTS issue,
    DROP COLUMN IF EXISTS pages,
    DROP COLUMN IF EXISTS citation_count,
    DROP COLUMN IF EXISTS metadata_confidence,
    DROP COLUMN IF EXISTS ingestion_version;
