# Database Cleanup Report (Neon/PostgreSQL)

Generated on: 2026-05-15  
Source: `DATABASE_URL` from `backend/.env`  
Raw audit JSON: `docs/database_audit.json`

## 1) Tổng quan database

- Tổng số bảng: **17**
- Bảng đề xuất giữ: **14**
- Bảng candidate_drop: **0** (theo rule tự động)
- Bảng candidate_review: **3**
- Tổng số cột 100% NULL: **0** (theo rule *candidate drop*), nhưng có nhiều cột 100% NULL vẫn được code/schema tham chiếu
- Tổng số cột >95% NULL: **0** (theo rule *candidate drop*), nhưng có cột sparse cần review

## 2) Danh sách bảng và khuyến nghị

| Table | Rows | Used in Code | FK Related | Recommendation |
|---|---:|---|---|---|
| alembic_version | 1 | yes | no | keep |
| document_artifacts | 113 | yes | yes | keep |
| document_chunks | 1 | yes | yes | keep |
| document_graphs | 68 | yes | yes | keep |
| document_jobs | 365 | yes | yes | keep |
| document_metadata | 113 | yes | yes | keep |
| document_recommendations | 235 | yes | yes | keep |
| document_summaries | 62 | yes | yes | keep |
| documents | 137 | yes | yes | keep |
| login_events | 12 | yes | yes | keep |
| password_reset_codes | 0 | yes | no | candidate_review |
| playing_with_neon | 10 | no | no | candidate_review |
| qa_history | 37 | yes | yes | keep |
| refresh_tokens | 177 | yes | yes | keep |
| users | 13 | yes | yes | keep |
| worker_status | 0 | yes | no | candidate_review |
| workspaces | 118 | yes | yes | keep |

## 3) Danh sách bảng nên giữ

- `users`, `workspaces`, `documents`, `document_metadata`, `document_chunks`, `document_summaries`, `qa_history`
- `document_jobs`, `document_graphs`, `document_artifacts`, `document_recommendations`
- `refresh_tokens`, `login_events`, `alembic_version`

Lý do: có dữ liệu thật, liên kết FK, và/hoặc đang được backend/frontend sử dụng.

## 4) Danh sách bảng có thể bỏ (ứng viên)

Hiện chưa có bảng nào đạt rule tự động `candidate_drop` tuyệt đối (0 row + không dùng code + không FK).

## 5) Danh sách bảng cần xem xét thêm

1. `playing_with_neon` (10 rows): không thấy code runtime dùng, không FK -> **candidate_drop thủ công sau backup**.
2. `worker_status` (0 rows): có trong code nhưng chưa có dữ liệu -> **candidate_disable/review**.
3. `password_reset_codes` (0 rows): bảng bảo mật, có trong flow quên mật khẩu -> **giữ**, chỉ review vận hành.

## 6) Danh sách cột 100% NULL (cần review nghiệp vụ, chưa drop)

| Table | Column | Null % | Distinct | Used in Code | Recommendation |
|---|---|---:|---:|---|---|
| document_artifacts | checksum_sha256 | 100% | 0 | yes | keep (review pipeline ghi checksum) |
| document_metadata | source | 100% | 0 | yes | keep |
| document_metadata | external_url | 100% | 0 | yes | keep |
| document_metadata | page_count | 100% | 0 | yes | keep |
| document_metadata | publisher | 100% | 0 | yes | keep |
| document_metadata | volume | 100% | 0 | yes | keep |
| document_metadata | issue | 100% | 0 | yes | keep |
| document_metadata | pages | 100% | 0 | yes | keep |
| document_metadata | citation_count | 100% | 0 | yes | keep |
| document_metadata | metadata_confidence | 100% | 0 | yes | keep |
| document_metadata | ingestion_version | 100% | 0 | yes | keep |
| document_recommendations | recommended_document_id | 100% | 0 | yes | keep (xem lại mapping recommendation) |
| documents | source_file_uri | 100% | 0 | yes | keep |
| documents | ingestion_version | 100% | 0 | yes | keep |
| refresh_tokens | user_id | 100% | 0 | yes | keep (cần kiểm tra migration/flow auth) |
| users | google_sub | 100% | 0 | yes | keep (chưa dùng login Google) |
| users | last_login_at | 100% | 0 | yes | keep |

## 7) Danh sách cột >95% NULL

| Table | Column | Null % | Distinct | Used in Code | Recommendation |
|---|---|---:|---:|---|---|
| documents | doc_id | 97.81% | 3 | yes | keep (review writer) |
| documents | source_type | 97.81% | 2 | yes | keep (review ingestion parser) |

## 8) Các bảng/cột extraction đang không tối ưu

- `document_chunks`: chỉ **1 row** trong khi `documents` có 137 row -> khả năng pipeline chunking chưa chạy đủ.
- `document_metadata.authors`: phát hiện dữ liệu nhiễu (ví dụ chứa cụm câu văn kiểu *"lấy chủ"*, *"trong đó thường gặp nhất là"*).
  - Khuyến nghị: **CLEANING_NEEDED**, không drop cột.
  - Nên áp dụng filter author ở bước extraction và/hoặc post-processing.

## 9) Rủi ro nếu xóa

- Xóa nhầm bảng auth (`refresh_tokens`, `password_reset_codes`, `users`) có thể khóa login/reset.
- Xóa bảng ingestion (`document_*`, `qa_history`) có thể làm hỏng dashboard/admin/QA.
- Xóa cột NULL nhưng có trong schema/API có thể làm crash response parser hoặc migration rollback khó.

## 10) Đề xuất migration

- Tạo file candidate migration: `storage/schemas/cleanup_candidate.sql`
- Mọi lệnh DROP đều để dạng comment / transaction với `ROLLBACK`.
- Ưu tiên xử lý theo thứ tự:
  1. Backup đầy đủ
  2. Drop candidate rõ ràng (`playing_with_neon`) nếu được duyệt
  3. Giữ các bảng/cột auth + core document
  4. Sửa pipeline extraction để giảm NULL/nhiễu trước khi drop cột

