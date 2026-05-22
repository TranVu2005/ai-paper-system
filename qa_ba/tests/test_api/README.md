# API Manual Test (Terminal Only)

Muc tieu:
- Test API truc tiep bang terminal (PowerShell), khong can viet test code.
- Kiem tra nhanh endpoint public va endpoint internal.
- Co script batch doc OpenAPI va goi lan luot route.

## 1) Dieu kien truoc khi test

Chay backend truoc:

```powershell
cd D:\Project\Datamining\project\ai-paper-system-root
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Khi thay dong sau la backend da san sang:

`Uvicorn running on http://127.0.0.1:8000`

## 2) Test endpoint public

```powershell
Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/api/v1/auth/google/config"
```

Expected:
- HTTP `200`
- Co cac field nhu `provider`, `enabled`, `redirect_enabled`

## 3) Test endpoint internal (khong token)

```powershell
Invoke-WebRequest -Method GET -Uri "http://127.0.0.1:8000/api/v1/internal/ai/system/dashboard"
```

Expected:
- HTTP `401`
- Thong diep loi ve token khong hop le

## 4) Test endpoint internal (co token)

Dat token dung (lay tu file `backend/.env`, key `INTERNAL_API_TOKEN`):

```powershell
$headers = @{ "x-internal-token" = "YOUR_INTERNAL_TOKEN" }
Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/api/v1/internal/ai/system/dashboard" -Headers $headers
```

Expected:
- HTTP `200`
- Co cac field:
  - `documents_total`
  - `documents_processing`
  - `documents_processed`
  - `queue_pending`
  - `workers_online`

## 5) Test RAG demo API

Khong token (expected `401`):

```powershell
Invoke-WebRequest -Method POST -Uri "http://127.0.0.1:8000/api/v1/internal/demo/rag-qa" -ContentType "application/json" -Body '{"question":"Tom tat noi dung tai lieu"}'
```

Co token (expected `200`):

```powershell
$headers = @{ "x-internal-token" = "YOUR_INTERNAL_TOKEN" }
$body = @{
  question = "Tom tat noi dung tai lieu"
  doc_limit = 1
  top_k = 4
} | ConvertTo-Json

Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8000/api/v1/internal/demo/rag-qa" -Headers $headers -ContentType "application/json" -Body $body
```

## 6) Cach danh gia PASS/FAIL

- PASS: status code va response dung nhu expected.
- FAIL: khong ket noi duoc backend, status code sai, hoac response sai contract.

## 7) Loi thuong gap

- `Unable to connect to the remote server`:
  - Backend chua chay
  - Chay sai port (khong phai `8000`)
- `401 Invalid internal token`:
  - Token sai hoac chua truyen header `x-internal-token`

## 8) Cach 1: Liet ke route tu OpenAPI (semi-auto)

Lay danh sach route tu OpenAPI:

```powershell
Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8000/openapi.json" |
  Select-Object -ExpandProperty paths |
  Get-Member -MemberType NoteProperty |
  Select-Object -ExpandProperty Name
```

Sau do goi tung route bang `Invoke-RestMethod` hoac `Invoke-WebRequest`.

## 9) Cach 2: Batch test toan bo route tu OpenAPI

Script da co san:

`qa_ba/tests/test_api/run_openapi_batch.py`

### 9.1 Chi test method an toan (GET/HEAD/OPTIONS)

```powershell
python .\qa_ba\tests\test_api\run_openapi_batch.py --base-url "http://127.0.0.1:8000" --internal-token "YOUR_INTERNAL_TOKEN"
```

### 9.2 Test ca write methods (POST/PUT/PATCH/DELETE)

```powershell
python .\qa_ba\tests\test_api\run_openapi_batch.py --base-url "http://127.0.0.1:8000" --internal-token "YOUR_INTERNAL_TOKEN" --include-write-methods
```

### 9.3 Test full theo backend (OpenAPI + hidden routes + internal compat alias)

```powershell
python .\qa_ba\tests\test_api\run_openapi_batch.py --base-url "http://127.0.0.1:8000" --internal-token "YOUR_INTERNAL_TOKEN" --include-write-methods --include-hidden-routes --include-internal-compat-alias
```

Co the them bearer token neu can:

```powershell
python .\qa_ba\tests\test_api\run_openapi_batch.py --base-url "http://127.0.0.1:8000" --internal-token "YOUR_INTERNAL_TOKEN" --bearer-token "YOUR_ACCESS_TOKEN" --include-write-methods
```

### 9.4 Test write co chon loc (khong xoa, khong job/worker/recover)

Dung profile an toan:

```powershell
python .\qa_ba\tests\test_api\run_openapi_batch.py `
  --base-url "https://YOUR-BACKEND" `
  --internal-token "YOUR_INTERNAL_TOKEN" `
  --bearer-token "YOUR_ACCESS_TOKEN" `
  --include-write-methods `
  --include-hidden-routes `
  --include-internal-compat-alias `
  --safe-write-profile
```

`--safe-write-profile` se tu dong bo qua:
- Method `DELETE`
- Path match `/jobs`, `/workers`, `/recover`

Neu can tuy chinh them:

```powershell
python .\qa_ba\tests\test_api\run_openapi_batch.py `
  --base-url "https://YOUR-BACKEND" `
  --include-write-methods `
  --exclude-method DELETE `
  --exclude-path-regex "/documents/.*/status" `
  --exclude-path-regex "/internal/.*/claim"
```

Ket qua duoc ghi vao:
- `qa_ba/tests/test_api/output/openapi_batch_result_latest.csv`
- `qa_ba/tests/test_api/output/openapi_batch_result_latest.json`

Quy uoc ket qua:
- `PASS_OK`: Tra ve 2xx
- `PASS_REACHABLE`: Route reachable nhung tra ve 4xx hop ly (401/403/404/405/409/415/422)
- `FAIL_SERVER`: Tra ve 5xx
- `FAIL_CONNECT`: Khong ket noi duoc backend
- `SKIPPED_WRITE_METHOD`: Bo qua write methods khi chua bat `-IncludeWriteMethods`
- `SKIPPED_POLICY`: Bo qua theo rule an toan (`--safe-write-profile` hoac `--exclude-*`)
