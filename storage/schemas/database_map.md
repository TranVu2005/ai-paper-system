# AI Paper System - Shared DB Map

This document is the single relational DB contract for backend, frontend, and workers.

## 1) Core Entities

- `users`: account identity, role, auth provider.
- `workspaces`: user notebooks.
- `documents`: uploaded files owned by user and optionally grouped by workspace.

## 2) Document Intelligence Entities (Metadata-First)

- `document_metadata` (1:1 with `documents`): title/abstract/authors/keywords + publication metadata (doi, year, journal, page_count, citation_count, ...).
- `document_artifacts` (1:N): pointer registry (`uri`) to heavy ingestion artifacts (unified JSON, sections/tables/formulas/references files, figures dir).
- `document_index_status` (1:1): vector/graph indexing runtime state (collection name, chunk_count, timestamps, last_error).
- `document_chunks` (1:N, deprecated for heavy payload): keep only if a local fallback is needed.
- `document_summaries` (1:N snapshots): summary content in `summary` (scoped by `summary_style`).
- `document_graphs` (1:1 snapshot, deprecated for heavy payload): prefer Neo4j or artifact files.
- `document_recommendations` (1:N): internal/external recommendations.
- `qa_history` (N): Q&A logs per user/document.
- `document_jobs` (N): async AI pipeline jobs.

## 3) Authentication / Session Entities

- `refresh_tokens`: refresh token rotation and revocation per device.
- `password_reset_codes`: OTP lifecycle for forgot-password flow.

## 4) Ops Entity

- `worker_status`: heartbeat/status for AI workers.

## 5) Relationships

- `users (1) -> (N) workspaces`
- `users (1) -> (N) documents`
- `workspaces (1) -> (N) documents`
- `documents (1) -> (1) document_metadata`
- `documents (1) -> (N) document_artifacts`
- `documents (1) -> (1) document_index_status`
- `documents (1) -> (N) document_chunks`
- `documents (1) -> (N) document_summaries`
- `documents (1) -> (1) document_graphs`
- `documents (1) -> (N) document_recommendations`
- `documents (1) -> (N) qa_history`
- `documents (1) -> (N) document_jobs`

## 6) Frontend-to-DB Path (through backend)

- Login/Register/Profile pages -> `users`, `refresh_tokens`, `password_reset_codes`
- Home/Workspace pages -> `workspaces`, `documents`
- Library/Upload/Detail pages -> `documents`, `document_metadata`, `document_artifacts`
- Summary/QA/Graph/Recommendation UI -> `document_jobs` + result tables (`document_summaries`, `qa_history`, `document_graphs`, `document_recommendations`)
- Analytics/Search pages -> aggregate/read from `documents`, `document_metadata`, `document_index_status`, `qa_history`

## 7) Notes

- Relational schema source of truth: `storage/schemas/postgres_schema.sql`.
- Preferred storage split:
  - Postgres: business metadata + pipeline/indexing status
  - Artifact storage: JSON/files from ingestion
  - Qdrant/Neo4j: vector and graph heavy runtime payloads
- Graph/vector stores remain in:
  - `storage/schemas/neo4j_schema.cypher`
  - `storage/schemas/qdrant_schema.json`
