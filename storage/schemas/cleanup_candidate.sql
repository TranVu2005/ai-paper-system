BEGIN;

-- =========================================================
-- Candidate cleanup script (REVIEW ONLY - DO NOT EXECUTE)
-- Generated from audit on 2026-05-15
-- All destructive statements are intentionally commented.
-- =========================================================

-- [TABLE] Candidate: demo/testing table, not FK-related, not referenced in runtime code
-- Reason: `playing_with_neon` has data but does not serve current core features.
-- DROP TABLE IF EXISTS public.playing_with_neon;

-- [TABLE] Candidate review: currently empty but referenced by code paths.
-- Keep until feature confirmation:
--  - public.worker_status
--  - public.password_reset_codes
-- DROP TABLE IF EXISTS public.worker_status;
-- DROP TABLE IF EXISTS public.password_reset_codes;

-- [COLUMN] Candidate review only (high NULL rate but appears in schema/code).
-- Do NOT drop now; verify backend/frontend contracts first.
-- ALTER TABLE public.documents DROP COLUMN IF EXISTS doc_id;
-- ALTER TABLE public.documents DROP COLUMN IF EXISTS source_type;
-- ALTER TABLE public.documents DROP COLUMN IF EXISTS source_file_uri;
-- ALTER TABLE public.documents DROP COLUMN IF EXISTS ingestion_version;

-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS source;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS external_url;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS page_count;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS publisher;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS volume;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS issue;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS pages;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS citation_count;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS metadata_confidence;
-- ALTER TABLE public.document_metadata DROP COLUMN IF EXISTS ingestion_version;

-- ALTER TABLE public.document_artifacts DROP COLUMN IF EXISTS checksum_sha256;
-- ALTER TABLE public.document_recommendations DROP COLUMN IF EXISTS recommended_document_id;
-- ALTER TABLE public.users DROP COLUMN IF EXISTS google_sub;
-- ALTER TABLE public.users DROP COLUMN IF EXISTS last_login_at;
-- ALTER TABLE public.refresh_tokens DROP COLUMN IF EXISTS user_id;

-- Safety stop: never commit in candidate script
ROLLBACK;

