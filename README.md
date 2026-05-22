# AI Paper System

AI Paper System is a full-stack platform for scientific paper management with AI features (ingestion, summarization, RAG QA, and graph-based recommendation).

Hệ thống AI Paper System là nền tảng quản lý bài báo khoa học tích hợp AI (ingestion, tóm tắt, RAG QA, gợi ý theo đồ thị tri thức).

## Security Update (May 22, 2026)
- Historical credentials were removed from repository files.
- Existing secrets must be rotated before production use.
- Never commit real credentials into `.env` or documentation.

## Overview
- Frontend: React + Vite
- Backend: FastAPI
- Data layer:
  - PostgreSQL for relational app data
  - Neo4j Aura for user-document graph and recommendation graph
- AI pipeline:
  - Ingestion pipeline
  - Summary + RAG + recommendation modules

## Features
- User authentication (email + Google OAuth)
- Upload and manage documents
- AI summaries (multiple styles)
- Document Q&A with RAG
- Knowledge graph extraction and graph retrieval
- Recommendation from graph signals
- Admin analytics/dashboard endpoints

## Architecture
```text
frontend (React/Vite)
   |
   v
backend/app (FastAPI API v1)
   |
   +-- PostgreSQL
   +-- Neo4j user graph
   +-- Neo4j recommendation graph
   +-- ai_module + ingestion pipelines
```

## Project Structure
```text
ai-paper-system/
├─ backend/          # FastAPI app, models, routes, services
├─ frontend/         # React/Vite UI
├─ ai_module/        # AI inference/retrieval/recommendation logic
├─ ingestion/        # Document ingestion pipeline
├─ scripts/          # Local helper scripts
├─ qa_ba/            # Tests and QA assets
├─ docs/             # Design and technical docs
├─ storage/          # DB clients, schemas
└─ data/samples/     # Small example data for repository
```

## Prerequisites
- Python 3.11+
- Node.js 20+
- PostgreSQL instance
- 2 Neo4j Aura databases (or equivalent separation)

## Environment Variables

### Backend
1. Copy template:
```bash
cp backend/.env.example backend/.env
```
2. Fill required values:
- `DATABASE_URL`
- `SECRET_KEY`
- `INTERNAL_API_TOKEN`
- `NEO4J_URI`, `NEO4J_USER`/`NEO4J_USERNAME`, `NEO4J_PASSWORD`
- `NEO4J_REC_URI`, `NEO4J_REC_USER`/`NEO4J_REC_USERNAME`, `NEO4J_REC_PASSWORD`

### Frontend
1. Copy template:
```bash
cp frontend/.env.example frontend/.env.local
```
2. Default local API:
- `VITE_API_BASE_URL=http://localhost:8000/api/v1`

## Quick Start

### Backend
```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### API Docs
- Swagger UI: `http://localhost:8000/docs`

## Testing

### Backend tests
```bash
cd backend
pytest
```

### Frontend checks
```bash
cd frontend
npm run lint
npm run build
```

## Deployment Notes
- See [DEPLOY_GPU.md](DEPLOY_GPU.md) for non-Docker deployment with remote AI services.
- `deploy.env.example` provides production-oriented variable placeholders.

## Security Notes
- `.env` files are local-only and ignored by git.
- Large or sensitive runtime data is excluded from source control.
- Uploaded files and DB backup dumps are not tracked.

## Contributing
1. Create a feature branch.
2. Keep commits focused and small.
3. Run tests/lint before opening a PR.
4. Do not commit credentials or private data.

## License
No license file is currently defined in this repository.
Add a `LICENSE` file before public distribution if needed.
