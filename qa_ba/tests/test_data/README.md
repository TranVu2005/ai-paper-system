# Data Quality Test Suite (QA BA)

## 1) Muc tieu cua bo test nay

Bo test nay la "quality gate" o muc du lieu dau vao cho recommend/RAG.
No tra loi cau hoi:

- JSON co doc/parse duoc khong?
- Cac truong cot loi co day du khong?
- `sections` co dung format de dung tiep trong pipeline khong?
- Ty le thieu metadata quan trong (abstract/keywords/year/authors) co nam trong nguong chap nhan khong?

No **khong** kiem tra chat luong semantic cua ket qua recommend.

---

## 2) File chinh

- `test_processed_data_quality.py`

---

## 3) Dang test chinh xac cai gi? (6 test)

Khi chay `pytest`, ban se thay 6 test:

1. `test_data_root_exists`
- Kiem tra duong dan dataset co ton tai hay khong.
- Mac dinh la `data/processed` (co the doi bang env `QA_DATA_ROOT`).

2. `test_dataset_has_json_files`
- Kiem tra co it nhat N file JSON (`QA_DATA_MIN_FILES`, mac dinh = 1).
- De chan truong hop tro nham folder rong.

3. `test_all_files_are_valid_json`
- Doc tung file `*.json` va parse bang `json.loads`.
- Fail neu co file hong cu phap JSON.

4. `test_required_core_fields`
- Moi file phai dat cac truong cot loi:
  - `doc_id` (khong rong)
  - `title` (khong rong)
  - `full_text` (khong rong)
  - `sections` (la list va co phan tu)
- Neu file nao thieu, test fail va in danh sach (toi da 20 file dau).

5. `test_sections_shape`
- Kiem tra `sections` dung schema co ban:
  - la list
  - moi phan tu la object
  - co key `name` va `content`
  - co it nhat 1 section co `content` khong rong
- Muc dich: dam bao text splitter/chunker/retriever co du lieu de dung.

6. `test_quality_ratios`
- Kiem tra ty le phu cua cac truong quan trong tren toan bo tap:
  - `abstract` >= `QA_MIN_ABSTRACT_RATIO` (mac dinh 0.98)
  - `keywords` >= `QA_MIN_KEYWORDS_RATIO` (mac dinh 0.95)
  - `year` >= `QA_MIN_YEAR_RATIO` (mac dinh 0.80)
  - `authors` >= `QA_MIN_AUTHORS_RATIO` (mac dinh 0.95)
- Day la gate theo "ty le", khong bat buoc 100%.

---



## 4) PASS/FAIL hieu nhu the nao?

- PASS toan bo:
  - Du lieu dat cac gate cau truc/co ban va nguong coverage dang set.
  - Nghia la data "san sang o muc input quality gate".

- FAIL:
  - Se chi ro nhom loi (json hong, thieu core fields, sections sai format, hoac coverage duoi nguong).
  - Message da in danh sach file loi dau tien de debug nhanh.

---

## 5) Bo test nay CHUA cover gi?

De tranh hieu nham: PASS khong co nghia la recommendation da "chat luong cao".
Bo test nay chua cover:

- dung/sai ve nghia cua title/abstract/full_text
- do lien quan ket qua recommend theo query
- latency build index/recommend
- duplicate/noise/language quality chi tiet

Muon cover cac diem tren can bo test muc cao hon (integration + relevance metrics).

---

## 6) Cach chay

Tai root project:

```powershell
cd D:\Project\Datamining\project\ai-paper-system-root
.\.venv\Scripts\Activate.ps1
pytest qa_ba/tests/test_data/test_processed_data_quality.py -v
```

Chay full `data/processed`:

```powershell
$env:QA_DATA_ROOT="D:\Project\Datamining\project\ai-paper-system-root\data\processed"
$env:QA_DATA_MAX_FILES="0"   # 0 = tat ca file
pytest qa_ba/tests/test_data/test_processed_data_quality.py -v
```

Scan nhanh mau 100 file:

```powershell
$env:QA_DATA_MAX_FILES="100"
pytest qa_ba/tests/test_data/test_processed_data_quality.py -v
```

---

## 7) Bien moi truong tuy chinh

- `QA_DATA_ROOT`: duong dan dataset (mac dinh: `data/processed`)
- `QA_DATA_MAX_FILES`: gioi han so file scan (0 = tat ca)
- `QA_DATA_MIN_FILES`: so file toi thieu (mac dinh: 1)
- `QA_MIN_ABSTRACT_RATIO`: nguong abstract (mac dinh: 0.98)
- `QA_MIN_KEYWORDS_RATIO`: nguong keywords (mac dinh: 0.95)
- `QA_MIN_YEAR_RATIO`: nguong year (mac dinh: 0.80)
- `QA_MIN_AUTHORS_RATIO`: nguong authors (mac dinh: 0.95)
