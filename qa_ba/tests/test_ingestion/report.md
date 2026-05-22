# Ingestion Test Report

## 1. Mục tiêu

Phần test ingestion hiện tại dùng mô hình mới:

- dữ liệu test được tổ chức theo **domain**
- kỳ vọng test được định nghĩa theo **manifest per-file**
- bộ quality gate chỉ còn 3 gate chính

Đường dẫn chính:

- data input: [qa_ba/data_test](/d:/Project/Datamining/project/ai-paper-system-root/qa_ba/data_test)
- manifest: [test_manifest.json](/d:/Project/Datamining/project/ai-paper-system-root/qa_ba/data_test/test_manifest.json)
- test suite: [test_pdf_quality_gates.py](/d:/Project/Datamining/project/ai-paper-system-root/qa_ba/tests/test_ingestion/test_pdf_quality_gates.py)
- output folder: [qa_ba/evaluation/ingestion](/d:/Project/Datamining/project/ai-paper-system-root/qa_ba/evaluation/ingestion)

## 2. Cấu trúc dữ liệu

Dataset không còn chia theo `normal / formula / table / ...` nữa.

Hiện tại dữ liệu được chia theo 5 domain:

- `KHKT&CN`
- `KHNN`
- `KHTN`
- `KHXH&NV`
- `KHYD`

Mỗi file PDF cần có một entry tương ứng trong `test_manifest.json`.

## 3. Manifest dùng để làm gì

Manifest là nguồn sự thật cho quality gate.

Mỗi entry trong `files` mô tả:

- `id`
- `domain`
- `filename`
- `pipeline_gate`
- `content_quality`
- `metadata_quality`

Ví dụ tối thiểu:

```json
{
  "id": "KHYD-001",
  "domain": "KHYD",
  "filename": "paper_a.pdf",
  "pipeline_gate": {
    "use_lm": true,
    "require_lm_step": true,
    "min_pages": 1,
    "min_text_chars": 200
  },
  "content_quality": {
    "sections": { "mode": "strict", "min_count": 1 },
    "figures": { "mode": "observe", "min_count": 0 }
  },
  "metadata_quality": {
    "mode": "strict",
    "min_title_chars": 10,
    "min_authors": 1
  }
}
```

## 4. 3 Gate hiện tại

### 4.1 Pipeline Gate

Kiểm:

- pipeline có chạy thành công không
- step `load` có chạy không
- OCR/load có ra text không
- `page_count` có đạt ngưỡng không
- nếu file yêu cầu LM thì `extract_metadata_lm` có chạy không

Nếu không đạt:

- `FAIL`

### 4.2 Content Quality Gate

Kiểm những phần ingestion hiện đáng tin hơn:

- `sections`
- `figures`

Không dùng `formula` và `table` làm tiêu chí pass/fail chính nữa.

Hai metric đó chỉ còn được ghi nhận như số liệu quan sát:

- `observed_formulas`
- `observed_tables`

Mode đánh giá:

- `strict`: không đạt thì `FAIL`
- `observe`: không đạt thì `WARN`

### 4.3 Metadata Quality Gate

Kiểm metadata cuối:

- `title`
- `authors`

Mode đánh giá:

- `strict`: không đạt thì `FAIL`
- `observe`: không đạt thì `WARN`

## 5. Trạng thái test

Bộ test mới không dùng `SKIP` cho quality evaluation của file.

Chỉ còn:

- `PASS`
- `WARN`
- `FAIL`

## 6. Formula và table

Vì ingestion hiện trích xuất `formula` và `table` còn yếu, chúng đã bị loại khỏi quality criteria chính.

Điều này có nghĩa là:

- test không fail chỉ vì `formulas=0`
- test không fail chỉ vì `tables=0`

Nhưng log vẫn có thể ghi:

- `observed_formulas=<n>`
- `observed_tables=<n>`

## 7. Chạy test

Kích hoạt môi trường:

```powershell
.\.venv\Scripts\Activate.ps1
```

Thêm `tesseract` vào `PATH` cho terminal hiện tại:

```powershell
$env:PATH = "C:\Program Files\Tesseract-OCR;" + $env:PATH
```

Chạy bộ quality gate:

```powershell
pytest qa_ba\tests\ingestion\test_pdf_quality_gates.py -v -s -rA
```

## 8. Lưu ý

- Nếu `files` trong manifest đang rỗng, test sẽ fail sớm với thông báo rõ ràng.
- Nếu file trong manifest không tồn tại trong đúng domain folder, test sẽ fail sớm.
- Nếu LM được yêu cầu mà Ollama/model không sẵn, `Pipeline Gate` sẽ fail.

## 9. Kết luận

Bộ ingestion test hiện tại đã chuyển sang mô hình phù hợp hơn với dataset domain-based:

- data theo domain
- kỳ vọng theo file
- 3 gate rõ ràng
- không còn phụ thuộc vào các group test cũ
