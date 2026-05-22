# AI Worker Contract

Backend owns users, documents, jobs, history, and API contracts.
AI workers own chunking, embedding, retrieval, model calls, and generated results.

## Public CMS Flow

Frontend creates jobs through authenticated CMS endpoints:

- `POST /api/v1/cms/documents/{document_id}/process/request`
- `POST /api/v1/cms/documents/{document_id}/summary/request`
- `POST /api/v1/cms/documents/{document_id}/qa/request`
- `GET /api/v1/cms/jobs/{job_id}`

The request endpoints return a `job_id`. The frontend can poll job status until it is `done` or `failed`.

## Worker Flow

Workers use `x-internal-token`.

1. Claim the next job:

```http
POST /api/v1/internal/ai/jobs/next
```

Optional filter:

```http
POST /api/v1/internal/ai/jobs/next?job_type=qa
```

Response:

```json
{
  "job_id": 1,
  "document_id": 10,
  "job_type": "qa",
  "payload": {
    "question": "..."
  }
}
```

2. Read document content:

```http
GET /api/v1/internal/ai/documents/{document_id}
```

3. Save generated data:

```http
POST /api/v1/internal/chunks/?document_id={document_id}
POST /api/v1/internal/ai/documents/{document_id}/summary
POST /api/v1/internal/ai/documents/{document_id}/qa
POST /api/v1/internal/ai/documents/{document_id}/graph
POST /api/v1/internal/ai/documents/{document_id}/recommendations
```

Include `job_id` in the body when the result belongs to a specific job.

4. Mark generic job completion/failure:

```http
POST /api/v1/internal/ai/jobs/{job_id}/complete
POST /api/v1/internal/ai/jobs/{job_id}/fail
```

## Job Types

- `process_document`: chunking, embedding, metadata extraction, summary bootstrap.
- `summary`: summary generation for an existing document.
- `qa`: retrieval and answer generation.
- Future types can be added without changing the job table, for example `graph`, `recommendation`, or `metadata`.

## Status Values

- `pending`
- `processing`
- `done`
- `failed`

