// =============================================================================
// NEO4J SCHEMA — Paper RAG + Knowledge Graph System
// Target:  Neo4j 5.x
// Version: v2 (sync với neo4j_client.py v2, entities.py v2, graph_builder.py v3)
//
// Changelog v2:
//   [v2-1]  Thêm constraint Concept, Evidence               — thay Method/Dataset/Task
//   [v2-2]  Thêm index concept_category, concept_domain
//   [v2-3]  Thêm index evidence_type, evidence_language
//   [v2-4]  Thêm index finding_type
//   [v2-5]  Thêm fulltext index concept_ft, evidence_ft
//   [v2-6]  Thêm fulltext index institution_ft              — thiếu trong client nhưng có trong schema
//   [v2-7]  SECTION 4: thêm node spec Concept/Evidence/Metric/Finding
//   [v2-8]  SECTION 5: xóa EVALUATES_ON→Dataset, ADDRESSES_TASK
//           thêm USES_CONCEPT, EVALUATES_ON→Evidence, ACHIEVES_METRIC,
//           EXTENDS, SUPPORTS, CONTRADICTS, HAS_FINDING
//   [v2-9]  SECTION 6: thêm MERGE template Concept/Evidence/Metric/Finding
//           thêm BASED_ON(Concept), EXTENDS, SUPPORTS, CONTRADICTS, HAS_FINDING
//   [v2-10] Giữ legacy Method/Dataset/Task constraint để backward compat
//           (sẽ xóa trong v3 sau khi migration xong)
//
// Thứ tự chạy bắt buộc:
//   1. Constraints  → phải có trước bất kỳ MERGE nào
//   2. Indexes      → tối ưu query
//   3. Full-text    → fuzzy search author/title/concept/evidence
// Chạy từng block, kiểm tra lỗi trước khi chạy block tiếp theo.
// =============================================================================


// =============================================================================
// SECTION 1 — CONSTRAINTS
// Chia theo 3 giai đoạn build để dễ rollout dần.
// =============================================================================

// -----------------------------------------------------------------------------
// Giai đoạn 1 — MVP: Paper + Author + Institution
// Chạy ngay từ đầu, trước khi ingest bất kỳ paper nào.
// -----------------------------------------------------------------------------

// Paper — id là UUID sinh trong pipeline, luôn có
CREATE CONSTRAINT paper_id IF NOT EXISTS
  FOR (p:Paper) REQUIRE p.id IS UNIQUE;

// Paper — doi là preferred unique key khi có
// Neo4j 5.x: UNIQUE constraint cho phép nhiều node doi = null — behavior đúng
// Neo4j 4.x: KHÔNG cho phép nhiều null — cần test kỹ nếu dùng 4.x
// Constraint này tự động tạo index trên doi — KHÔNG tạo thêm index thủ công
CREATE CONSTRAINT paper_doi IF NOT EXISTS
  FOR (p:Paper) REQUIRE p.doi IS UNIQUE;

// Paper — fallback key khi không có doi
// Bắt buộc normalize title trước khi MERGE:
//   lowercase → strip → collapse whitespace → remove special chars
// Nếu không normalize, cùng một paper sẽ tạo 2 node khác nhau.
//
// KHÔNG dùng NODE KEY vì year có thể null (paper không rõ năm).
// NODE KEY bắt buộc cả 2 field NOT NULL → insert fail nếu year = null.
// Dùng composite index thường thay thế; uniqueness được enforce ở tầng Python
// trong graph_builder.py trước khi gọi MERGE.
CREATE INDEX paper_title_year IF NOT EXISTS
  FOR (p:Paper) ON (p.title, p.year);
//
// Quy tắc xử lý year = null trong graph_builder.py:
//   1. Thử parse year từ nội dung file (regex trên header/footer).
//   2. Thử lấy từ reference section của paper khác cite đến paper này.
//   3. Nếu vẫn null → set year = 0 (sentinel) để tránh collision, ghi log warning.
//   Không dùng -1 vì Neo4j sort int, 0 ít gây nhầm lẫn hơn.
//   Sentinel 0 này áp dụng nhất quán cho cả merge_paper_by_title_year()
//   lẫn merge_citation() khi cited_year = null (xem [fix-7] và [fix-9]).

// Author — unique theo id (UUID), KHÔNG unique theo name vì trùng tên phổ biến.
// MERGE logic trong graph_builder.py:
//   1. Lookup bằng (name, affiliation) qua full-text index
//   2. Nếu score >= threshold → MATCH node đó
//   3. Nếu không tìm thấy    → CREATE với id mới
CREATE CONSTRAINT author_id IF NOT EXISTS
  FOR (a:Author) REQUIRE a.id IS UNIQUE;

// Institution — tên tổ chức là unique key (sau normalize)
// Normalize: lowercase, bỏ dấu, collapse whitespace
// VD: "Đại học Quốc gia Hà Nội" → "dai hoc quoc gia ha noi"
CREATE CONSTRAINT institution_name IF NOT EXISTS
  FOR (i:Institution) REQUIRE i.name IS UNIQUE;

// Venue — tên journal/conference sau normalize
CREATE CONSTRAINT venue_name IF NOT EXISTS
  FOR (v:Venue) REQUIRE v.name IS UNIQUE;

// Topic — đến từ UnifiedDocument.keywords (đã lowercase khi MERGE)
CREATE CONSTRAINT topic_name IF NOT EXISTS
  FOR (t:Topic) REQUIRE t.name IS UNIQUE;

// -----------------------------------------------------------------------------
// Giai đoạn 2 — Schema v2: Concept, Evidence, Metric, Finding
// Chạy sau khi giai đoạn 1 ổn định và LLM extraction đã sẵn sàng.
// [v2-1] Thay thế Method/Dataset/Task — entity_extractor.py v4 trở đi dùng các node này.
// -----------------------------------------------------------------------------

// Concept — thay thế Method + Task, phân biệt bằng field category
// category: "method" | "model" | "algorithm" | "task" | "framework" | "architecture"
//         | "gene" | "protein" | "disease" | "drug" | "pathway"
//         | "theory" | "policy" | "event" | "organization"
//         | "phenomenon" | "law" | "particle"
//         | "indicator" | "market" | "concept"
CREATE CONSTRAINT concept_name IF NOT EXISTS
  FOR (c:Concept) REQUIRE c.name IS UNIQUE;

// Evidence — thay thế Dataset, phân biệt bằng field evidence_type
// evidence_type: "dataset" | "benchmark" | "corpus"
//              | "experiment" | "clinical_trial"
//              | "survey" | "census" | "case_study"
//              | "observation" | "simulation"
CREATE CONSTRAINT evidence_name IF NOT EXISTS
  FOR (e:Evidence) REQUIRE e.name IS UNIQUE;

// Metric — KHÔNG có global unique constraint vì cùng tên metric (VD: "f1 score")
// có thể xuất hiện với context khác nhau (measured_on/measured_by khác nhau).
// MERGE key = name (node canonical) + (measured_on, measured_by) trên EDGE.
// Uniqueness enforce ở tầng Python trong neo4j_client.merge_metric().

// Finding — KHÔNG có global unique constraint vì description gần như unique.
// MERGE key = description[:200] — enforce ở tầng Python.

// -----------------------------------------------------------------------------
// Giai đoạn 3 — Chunk + Citation graph
// Chunk node dùng qdrant_id làm MERGE key — phải có constraint trước khi
// build_phase3() chạy.
// Citation chỉ tạo thêm edge [:CITES] giữa các Paper đã có, hoặc tạo stub
// Paper dùng paper_id constraint.
// -----------------------------------------------------------------------------

// Chunk — qdrant_id là unique key, sinh bởi Qdrant sau khi upsert
// merge_chunk() dùng MERGE (c:Chunk {qdrant_id: $qdrant_id}) — phải có constraint
// để tránh duplicate khi pipeline chạy lại.
CREATE CONSTRAINT chunk_qdrant_id IF NOT EXISTS
  FOR (c:Chunk) REQUIRE c.qdrant_id IS UNIQUE;

// -----------------------------------------------------------------------------
// Legacy — giữ để backward compat, sẽ xóa trong v3  [v2-10]
// Code cũ gọi merge_method/merge_dataset/merge_task vẫn hoạt động.
// DEPRECATED: dùng Concept/Evidence thay thế.
// -----------------------------------------------------------------------------

CREATE CONSTRAINT method_name IF NOT EXISTS
  FOR (m:Method) REQUIRE m.name IS UNIQUE;

CREATE CONSTRAINT dataset_name IF NOT EXISTS
  FOR (d:Dataset) REQUIRE d.name IS UNIQUE;

CREATE CONSTRAINT task_name IF NOT EXISTS
  FOR (tk:Task) REQUIRE tk.name IS UNIQUE;


// =============================================================================
// SECTION 2 — PROPERTY INDEXES
// Chỉ tạo cho field thường xuyên filter/sort.
// Không tạo index cho list property (chunk_ids) — Neo4j không query list hiệu quả.
// =============================================================================

// Paper — filter theo năm và ngôn ngữ (dùng nhiều trong recommendation query)
CREATE INDEX paper_year     IF NOT EXISTS FOR (p:Paper) ON (p.year);
CREATE INDEX paper_language IF NOT EXISTS FOR (p:Paper) ON (p.language);

// Paper — track trạng thái pipeline (tìm stub paper cần ingest, paper chưa embed)
CREATE INDEX paper_status IF NOT EXISTS FOR (p:Paper) ON (p.processing_status);

// Author — group theo affiliation raw (trước khi link sang Institution)
CREATE INDEX author_affiliation IF NOT EXISTS FOR (a:Author) ON (a.affiliation);

// Institution — filter theo quốc gia
CREATE INDEX institution_country IF NOT EXISTS FOR (i:Institution) ON (i.country);

// Concept — filter theo category và domain  [v2-2]
// VD: tìm tất cả method thuộc domain cs
CREATE INDEX concept_category IF NOT EXISTS FOR (c:Concept) ON (c.category);
CREATE INDEX concept_domain   IF NOT EXISTS FOR (c:Concept) ON (c.domain);

// Evidence — filter theo loại và ngôn ngữ  [v2-3]
// VD: tìm tất cả benchmark tiếng Việt
CREATE INDEX evidence_type     IF NOT EXISTS FOR (e:Evidence) ON (e.evidence_type);
CREATE INDEX evidence_language IF NOT EXISTS FOR (e:Evidence) ON (e.language);

// Finding — filter theo loại finding  [v2-4]
// VD: lấy tất cả limitation của 1 paper
CREATE INDEX finding_type IF NOT EXISTS FOR (f:Finding) ON (f.finding_type);

// SIMILAR_TO — filter theo score để lấy top-k similar paper
// Tạo index trên relationship property — Neo4j 5.x hỗ trợ
CREATE INDEX similar_to_score IF NOT EXISTS FOR ()-[r:SIMILAR_TO]-() ON (r.score);

// Legacy indexes — giữ để backward compat  [v2-10]
CREATE INDEX method_category  IF NOT EXISTS FOR (m:Method)  ON (m.category);
CREATE INDEX dataset_language IF NOT EXISTS FOR (d:Dataset) ON (d.language);


// =============================================================================
// SECTION 3 — FULL-TEXT INDEXES
// Dùng cho fuzzy lookup trong graph_builder.py và entity_extractor.py.
// Quan trọng: chỉ hoạt động trên STRING property, không phải list.
// aliases được lưu dạng "alias1|alias2|alias3" thay vì list[str].
// =============================================================================

// Author — tìm gần đúng theo name và name_ascii (bỏ dấu tiếng Việt)
CREATE FULLTEXT INDEX author_ft IF NOT EXISTS
  FOR (a:Author) ON EACH [a.name, a.name_ascii];

// Paper — tìm gần đúng theo title (khi Reference chỉ có title, không có doi)
CREATE FULLTEXT INDEX paper_title_ft IF NOT EXISTS
  FOR (p:Paper) ON EACH [p.title];

// Institution — tìm gần đúng tên trường Việt Nam (nhiều cách viết khác nhau)
CREATE FULLTEXT INDEX institution_ft IF NOT EXISTS
  FOR (i:Institution) ON EACH [i.name, i.name_ascii];

// Concept — tìm theo name và aliases_text  [v2-5]
// Dùng bởi EntityMerger.lookup_concept_by_fulltext() trong graph_updater.py
// VD: "phobert" tìm thấy node có aliases_text = "pho-bert|pho bert"
CREATE FULLTEXT INDEX concept_ft IF NOT EXISTS
  FOR (c:Concept) ON EACH [c.name, c.aliases_text];

// Evidence — tìm theo name và aliases_text  [v2-5]
// Dùng bởi EntityMerger.lookup_evidence_by_fulltext() trong graph_updater.py
CREATE FULLTEXT INDEX evidence_ft IF NOT EXISTS
  FOR (e:Evidence) ON EACH [e.name, e.aliases_text];

// Legacy fulltext indexes — giữ để backward compat  [v2-10]
CREATE FULLTEXT INDEX method_ft IF NOT EXISTS
  FOR (m:Method) ON EACH [m.name, m.aliases_text];

CREATE FULLTEXT INDEX dataset_ft IF NOT EXISTS
  FOR (d:Dataset) ON EACH [d.name, d.aliases_text];


// =============================================================================
// SECTION 4 — NODE PROPERTY REFERENCE
// graph_builder.py phải tuân theo spec này khi SET property.
// Field có dấu ? là optional, có thể null.
// =============================================================================

// --- :Paper ---
// {
//   id:                string   — UUID, sinh bởi pipeline, luôn có
//   title:             string   — required, đã normalize
//   year:              int?     — required nếu không có doi; dùng 0 làm sentinel
//   doi:               string?  — preferred unique key, null nếu không có
//   abstract:          string?  — lưu để embed sang Qdrant
//   language:          string   — "vi" | "en" | "bilingual"
//   domain:            string?  — "cs" | "bio" | "social" | "physics" | "economics"
//                                  infer từ PaperEntity.domain sau khi entity_extractor chạy
//   source_file:       string   — đường dẫn file gốc
//   source_type:       string   — "pdf" | "docx" | "html"
//   page_count:        int?
//   citation_count:    int?     — từ Semantic Scholar / CrossRef nếu có
//   chunk_ids:         list     — ID chunks trong Qdrant (không index, chỉ lưu)
//   processing_status: string   — "stub" | "parsed" | "kg_built" | "embedded"
//   created_at:        datetime
// }

// --- :Author ---
// {
//   id:          string   — UUID (primary key)
//   name:        string   — tên gốc, giữ nguyên dấu
//   name_ascii:  string?  — bỏ dấu tiếng Việt, dùng cho full-text index
//   email:       string?  — từ Regex extraction (UnifiedDocument)
//   affiliation: string?  — tên tổ chức raw, trước khi link sang Institution
// }
// Lưu ý: KHÔNG có name UNIQUE.
// "Nguyễn Văn A" ở ĐHQGHN và "Nguyễn Văn A" ở ĐHBK là 2 Author node khác nhau.

// --- :Institution ---
// {
//   id:           string   — UUID
//   name:         string   — normalized unique key (lowercase + bỏ dấu + collapse ws)
//                            đây là field dùng để MERGE, không phải để hiển thị
//   display_name: string?  — tên gốc giữ nguyên dấu, dùng để hiển thị trên UI
//   name_ascii:   string?  — bỏ dấu, dùng cho full-text index
//   country:      string?  — "VN" | "US" | ...
//   type:         string?  — "university" | "institute" | "company" | "other"
// }

// --- :Venue ---
// {
//   id:        string
//   name:      string   — tên journal/conference (normalize, unique key)
//   type:      string   — "journal" | "conference" | "workshop" | "preprint"
//   publisher: string?
// }

// --- :Topic ---
// {
//   id:   string
//   name: string   — lowercase, đã strip dấu câu (unique key)
// }

// --- :Concept ---   [v2]
// Thay thế :Method + :Task. category phân biệt loại.
// {
//   id:           string
//   name:         string   — tên canonical lowercase (unique key)
//   category:     string   — xem CONCEPT_CATEGORIES trong entities.py:
//                            "method" | "model" | "algorithm" | "task" |
//                            "framework" | "architecture" |
//                            "gene" | "protein" | "disease" | "drug" | "pathway" |
//                            "theory" | "policy" | "event" | "organization" |
//                            "phenomenon" | "law" | "particle" |
//                            "indicator" | "market" | "concept"
//   domain:       string?  — "cs" | "bio" | "social" | "physics" | "economics"
//   aliases_text: string?  — "alias1|alias2" — STRING pipe-separated, không phải list
//                            ON MATCH SET trong merge_concept() append alias mới,
//                            không overwrite alias cũ
// }

// --- :Evidence ---   [v2]
// Thay thế :Dataset. evidence_type phân biệt loại.
// {
//   id:            string
//   name:          string   — tên canonical lowercase (unique key)
//   evidence_type: string   — xem EVIDENCE_TYPES trong entities.py:
//                             "dataset" | "benchmark" | "corpus" |
//                             "experiment" | "clinical_trial" |
//                             "survey" | "census" | "case_study" |
//                             "observation" | "simulation"
//   language:      string?  — "vi" | "en" | "multilingual"
//   aliases_text:  string?  — "alias1|alias2" — STRING pipe-separated
// }

// --- :Metric ---   [v2]
// Node đại diện cho tên metric canonical (VD: "f1 score", "accuracy").
// Context đo lường (value, measured_on, measured_by) nằm trên EDGE ACHIEVES_METRIC.
// {
//   id:            string
//   name:          string   — tên canonical lowercase (MERGE key)
//   higher_better: bool?    — true = cao hơn tốt hơn (F1, Accuracy, BLEU)
//                             false = thấp hơn tốt hơn (Perplexity, FID, MAE)
//                             null = không xác định
// }
// Không có UNIQUE constraint vì cùng tên metric có thể đo trên dataset khác nhau.
// MERGE key = name trên node, (measured_on, measured_by) trên EDGE.

// --- :Finding ---   [v2]
// {
//   id:           string
//   description:  string   — mô tả finding, tối đa 200 chars (MERGE key)
//   finding_type: string   — "result" | "hypothesis" | "conclusion" |
//                             "limitation" | "contribution"
// }

// --- :Chunk ---
// {
//   id:        string   — UUID
//   qdrant_id: string   — ID trong Qdrant (MERGE key, unique)
//                         build_phase3() dùng sau khi Qdrant upsert xong
//   section:   string?  — tên section gốc (VD: "method", "abstract")
//   page:      int?     — số trang trong file gốc
//   text:      string?  — preview tối đa 1000 chars, full text nằm ở Qdrant
// }


// =============================================================================
// SECTION 5 — RELATIONSHIP SCHEMA
// Thứ tự = thứ tự build trong graph_builder.py.
// Property trên edge là provenance + confidence — không enforce bởi Neo4j,
// graph_builder.py phải tự validate trước khi SET.
// =============================================================================

// --- Giai đoạn 1 — rule-only (graph_builder.build_phase1) ---

// (Author)-[:WROTE {order: int}]->(Paper)
//   order: 0 = first author, 1 = second author, ...

// (Author)-[:AFFILIATED_WITH]->(Institution)
//   Institution != Venue.
//   Institution = nơi tác giả làm việc (trường, viện, công ty).
//   Venue       = nơi paper được đăng (journal, conference).

// (Paper)-[:PUBLISHED_AT {volume?, issue?, pages?}]->(Venue)
//   Properties đặt qua ON CREATE/MATCH SET — không nằm trong MERGE key.

// (Paper)-[:HAS_TOPIC {source: "keyword" | "llm_extracted"}]->(Topic)

// (Paper)-[:CITES {confidence, raw_ref_text}]->(Paper)
//   confidence:   1.0 = doi match (path 1: cited_doc_id)
//                 1.0 = doi match (path 2: cited_doi)
//                 0.8 = (title + year) exact match
//                 0.6 = title only (year = null → sentinel 0 — xem [fix-9])
//   raw_ref_text: text citation gốc, dùng để debug khi cần

// --- Giai đoạn 2 — hybrid (graph_builder.build_phase2) ---

// (Paper)-[:USES_CONCEPT {category, source_section, confidence, evidence}]->(Concept)   [v2]
//   Thay thế USES_METHOD + ADDRESSES_TASK.
//   category:       ConceptEntity.category — phân biệt loại concept trên edge
//   source_section: "abstract" | "method" | "experiment" | "conclusion" | ...
//   confidence:     float 0.0 → 1.0
//   evidence:       string <= 200 chars, câu văn gốc làm bằng chứng
//   ON MATCH SET giữ confidence cao nhất

// (Paper)-[:EVALUATES_ON {source_section, confidence, evidence}]->(Evidence)   [v2]
//   Thay thế EVALUATES_ON→Dataset.
//   Target đổi từ :Dataset → :Evidence.
//   KHÔNG có property metric trên edge này — metric nằm trên ACHIEVES_METRIC riêng.

// (Paper)-[:ACHIEVES_METRIC {value, unit, measured_on, measured_by, confidence, evidence}]->(Metric)   [v2]
//   Edge mới — context đo lường nằm hoàn toàn trên edge.
//   value:       float? — giá trị số thực (VD: 92.3)
//   unit:        string? — "%", "score", "eV", ...
//   measured_on: string? — tên Evidence mà metric được đo trên đó (VD: "squad 2.0")
//   measured_by: string? — tên Concept (method/model) thực hiện phép đo (VD: "roberta-large")
//   confidence:  float   — mặc định 0.90 (extract từ text, high confidence)
//   evidence:    string <= 200 chars
//   MERGE key trên edge: (measured_on, measured_by) — cùng metric đo trên dataset khác
//   tạo edge riêng, không overwrite

// (Concept)-[:BASED_ON {confidence}]->(Concept)
//   VD: (bert-wwm)-[:BASED_ON]->(bert)
//   "sử dụng / phụ thuộc vào" — không nhất thiết kế thừa toàn bộ
//   CẢNH BÁO: kiểm tra cycle trong ConsistencyChecker (INVERSE_EDGE check)

// (Concept)-[:EXTENDS {confidence}]->(Concept)   [v2]
//   Edge mới — "kế thừa / cải tiến".
//   VD: (roberta)-[:EXTENDS]->(bert)
//   Khác BASED_ON ở chỗ: EXTENDS ngụ ý fine-tune / improve toàn bộ,
//   BASED_ON ngụ ý dùng như một thành phần

// (Paper)-[:SUPPORTS {finding_desc, confidence}]->(Paper)   [v2]
//   Edge mới — từ FindingEntity.supports.
//   source paper ủng hộ / xác nhận kết quả của target paper.
//   finding_desc: mô tả finding làm cơ sở, tối đa 200 chars
//   target có thể là stub (chưa ingest) — merge_paper_supports() tự tạo placeholder

// (Paper)-[:CONTRADICTS {finding_desc, confidence}]->(Paper)   [v2]
//   Edge mới — từ FindingEntity.contradicts.
//   source paper phản bác / mâu thuẫn với target paper.
//   target có thể là stub — merge_paper_contradicts() tự tạo placeholder

// (Paper)-[:HAS_FINDING {confidence, evidence}]->(Finding)   [v2]
//   Edge mới — từ FindingEntity (result/hypothesis/conclusion/limitation/contribution).
//   confidence: float
//   evidence:   string <= 200 chars

// (Paper)-[:SIMILAR_TO {score, shared_methods?, shared_authors?}]->(Paper)
//   score:          float — similarity score tổng hợp (vector + graph features)
//                   index trên r.score để query top-k hiệu quả (xem Section 2)
//   shared_methods: string? — "concept_name1|concept_name2" (pipe-separated)
//                   LƯU Ý: tên field giữ nguyên "shared_methods" nhưng thực tế
//                   chứa tên Concept (không phải Method) kể từ schema v2
//   shared_authors: string? — "author_id1|author_id2"
//   Chỉ insert khi score >= threshold — threshold do caller (offline job) quyết định.

// (Paper)-[:HAS_CHUNK]->(Chunk)
//   Không có property trên edge — relationship đủ để navigate.
//   build_phase3() chạy sau khi Qdrant upsert xong để đảm bảo
//   qdrant_id đã tồn tại trước khi MERGE vào Neo4j.


// =============================================================================
// SECTION 6 — MERGE TEMPLATES
// Copy sang graph_builder.py / neo4j_client.py, thay $param bằng giá trị thực.
// Tất cả dùng MERGE để idempotent — chạy lại pipeline không tạo duplicate.
// =============================================================================

// ---------------------------------------------------------------------------
// Paper
// ---------------------------------------------------------------------------

// CẢNH BÁO: KHÔNG BAO GIỜ làm MERGE (p:Paper {doi: null}).
// Neo4j sẽ match tất cả paper không có doi thành 1 node duy nhất.
// Phải dùng if/else trong Python trước khi gọi Cypher:
//
//   if doc.doi:
//       _merge_paper_by_doi(doc)
//   else:
//       _merge_paper_by_title_year(doc)

// -- Paper (có doi) --
// MERGE (p:Paper {doi: $doi})
// ON CREATE SET
//   p.id                = $id,
//   p.title             = $title,
//   p.year              = $year,
//   p.abstract          = $abstract,
//   p.language          = $language,
//   p.domain            = $domain,
//   p.source_file       = $source_file,
//   p.source_type       = $source_type,
//   p.citation_count    = $citation_count,
//   p.chunk_ids         = $chunk_ids,
//   p.processing_status = 'parsed',
//   p.created_at        = datetime()
// ON MATCH SET
//   p.chunk_ids         = $chunk_ids,
//   p.domain            = CASE WHEN $domain IS NOT NULL THEN $domain ELSE p.domain END,
//   p.processing_status = 'parsed';

// -- Paper (fallback: doi = null, dùng title + year) --
// CẢNH BÁO: $year KHÔNG ĐƯỢC là null — dùng 0 làm sentinel nếu không rõ năm.
// MERGE (p:Paper {title: $title, year: $year})
// ON CREATE SET
//   p.id                = $id,
//   p.language          = $language,
//   p.domain            = $domain,
//   p.source_file       = $source_file,
//   p.source_type       = $source_type,
//   p.chunk_ids         = $chunk_ids,
//   p.processing_status = 'parsed',
//   p.created_at        = datetime()
// ON MATCH SET
//   p.chunk_ids         = $chunk_ids,
//   p.domain            = CASE WHEN $domain IS NOT NULL THEN $domain ELSE p.domain END,
//   p.processing_status = CASE
//     WHEN p.processing_status = 'stub' THEN 'parsed'
//     ELSE p.processing_status
//   END;

// ---------------------------------------------------------------------------
// Author + Institution + Venue + Topic
// ---------------------------------------------------------------------------

// -- Author (dùng id làm MERGE key) --
// MERGE (a:Author {id: $author_id})
// ON CREATE SET
//   a.name        = $name,
//   a.name_ascii  = $name_ascii,
//   a.email       = $email,
//   a.affiliation = $affiliation
// WITH a
// MATCH (p:Paper {id: $paper_id})
// MERGE (a)-[:WROTE {order: $order}]->(p);

// -- Institution + edge AFFILIATED_WITH --
// MERGE (i:Institution {name: $inst_name_normalized})
// ON CREATE SET
//   i.id           = randomUUID(),
//   i.display_name = $inst_display_name,
//   i.name_ascii   = $inst_name_ascii,
//   i.country      = $country,
//   i.type         = $inst_type
// WITH i
// MATCH (a:Author {id: $author_id})
// MERGE (a)-[:AFFILIATED_WITH]->(i);

// -- Venue + edge PUBLISHED_AT --
// CHÚ Ý: volume/issue/pages đặt qua ON CREATE/MATCH SET, KHÔNG trong MERGE key.
// MERGE (v:Venue {name: $venue_name_normalized})
// ON CREATE SET
//   v.id        = randomUUID(),
//   v.type      = $venue_type,
//   v.publisher = $publisher
// WITH v
// MATCH (p:Paper {id: $paper_id})
// MERGE (p)-[r:PUBLISHED_AT]->(v)
// ON CREATE SET r.volume = $volume, r.issue = $issue, r.pages = $pages
// ON MATCH SET  r.volume = $volume, r.issue = $issue, r.pages = $pages;

// -- Topic batch --
// UNWIND $topics AS topic_name
// MERGE (t:Topic {name: topic_name})
// ON CREATE SET t.id = randomUUID()
// WITH t, topic_name
// MATCH (p:Paper {id: $paper_id})
// MERGE (p)-[:HAS_TOPIC {source: $source}]->(t);

// ---------------------------------------------------------------------------
// Concept  [v2]
// Thay thế Method + Task. merge_concept() trong neo4j_client.py.
// ---------------------------------------------------------------------------

// -- Concept + edge USES_CONCEPT --
// aliases_text: append pattern — không overwrite alias cũ.
// ON MATCH SET category/domain: không overwrite non-null bằng null.
// ON MATCH SET confidence: giữ giá trị cao hơn.
//
// MERGE (c:Concept {name: $concept_name})
// ON CREATE SET
//   c.id           = randomUUID(),
//   c.category     = $category,
//   c.domain       = $domain,
//   c.aliases_text = $aliases_text
// ON MATCH SET
//   c.category     = CASE
//     WHEN $category IS NOT NULL AND $category <> 'concept'
//     THEN $category ELSE c.category END,
//   c.domain       = CASE
//     WHEN $domain IS NOT NULL THEN $domain ELSE c.domain END,
//   c.aliases_text = CASE
//     WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
//       AND (c.aliases_text IS NULL OR c.aliases_text = '')
//     THEN $aliases_text
//     WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
//       AND NOT $aliases_text IN split(c.aliases_text, '|')
//     THEN c.aliases_text + '|' + $aliases_text
//     ELSE c.aliases_text
//   END
// WITH c
// MATCH (p:Paper {id: $paper_id})
// MERGE (p)-[r:USES_CONCEPT]->(c)
// ON CREATE SET
//   r.category       = $category,
//   r.source_section = $source_section,
//   r.confidence     = $confidence,
//   r.evidence       = $evidence
// ON MATCH SET
//   r.category   = $category,
//   r.confidence = CASE WHEN $confidence > r.confidence
//                  THEN $confidence ELSE r.confidence END,
//   r.evidence   = CASE WHEN $confidence > r.confidence
//                  THEN $evidence ELSE r.evidence END;

// ---------------------------------------------------------------------------
// Evidence  [v2]
// Thay thế Dataset. merge_evidence() trong neo4j_client.py.
// ---------------------------------------------------------------------------

// -- Evidence + edge EVALUATES_ON --
// MERGE (e:Evidence {name: $evidence_name})
// ON CREATE SET
//   e.id            = randomUUID(),
//   e.evidence_type = $evidence_type,
//   e.language      = $language,
//   e.aliases_text  = $aliases_text
// ON MATCH SET
//   e.evidence_type = CASE
//     WHEN $evidence_type IS NOT NULL THEN $evidence_type ELSE e.evidence_type END,
//   e.language      = CASE
//     WHEN $language IS NOT NULL THEN $language ELSE e.language END,
//   e.aliases_text  = CASE
//     WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
//       AND (e.aliases_text IS NULL OR e.aliases_text = '')
//     THEN $aliases_text
//     WHEN $aliases_text IS NOT NULL AND $aliases_text <> ''
//       AND NOT $aliases_text IN split(e.aliases_text, '|')
//     THEN e.aliases_text + '|' + $aliases_text
//     ELSE e.aliases_text
//   END
// WITH e
// MATCH (p:Paper {id: $paper_id})
// MERGE (p)-[r:EVALUATES_ON]->(e)
// ON CREATE SET
//   r.source_section = $source_section,
//   r.confidence     = $confidence,
//   r.evidence       = $evidence
// ON MATCH SET
//   r.confidence = CASE WHEN $confidence > r.confidence
//                  THEN $confidence ELSE r.confidence END,
//   r.evidence   = CASE WHEN $confidence > r.confidence
//                  THEN $evidence ELSE r.evidence END;

// ---------------------------------------------------------------------------
// Metric  [v2]
// merge_metric() trong neo4j_client.py.
// ---------------------------------------------------------------------------

// -- Metric + edge ACHIEVES_METRIC --
// MERGE key trên node: name (canonical metric name)
// MERGE key trên edge: (measured_on, measured_by) — context đo lường
// Cùng metric trên dataset khác → edge khác, không overwrite
//
// MERGE (m:Metric {name: $metric_name})
// ON CREATE SET
//   m.id            = randomUUID(),
//   m.higher_better = $higher_better
// ON MATCH SET
//   m.higher_better = CASE
//     WHEN $higher_better IS NOT NULL THEN $higher_better ELSE m.higher_better END
// WITH m
// MATCH (p:Paper {id: $paper_id})
// MERGE (p)-[r:ACHIEVES_METRIC {measured_on: $measured_on, measured_by: $measured_by}]->(m)
// ON CREATE SET
//   r.value      = $value,
//   r.unit       = $unit,
//   r.confidence = $confidence,
//   r.evidence   = $evidence
// ON MATCH SET
//   r.value      = CASE WHEN $value IS NOT NULL THEN $value ELSE r.value END,
//   r.unit       = CASE WHEN $unit  IS NOT NULL THEN $unit  ELSE r.unit  END,
//   r.confidence = CASE WHEN $confidence > r.confidence
//                  THEN $confidence ELSE r.confidence END,
//   r.evidence   = CASE WHEN $confidence > r.confidence
//                  THEN $evidence ELSE r.evidence END;

// ---------------------------------------------------------------------------
// Finding  [v2]
// merge_finding() trong neo4j_client.py.
// ---------------------------------------------------------------------------

// -- Finding + edge HAS_FINDING --
// MERGE key: description[:200] — gần như unique theo nội dung
//
// MERGE (f:Finding {description: $description})
// ON CREATE SET
//   f.id           = randomUUID(),
//   f.finding_type = $finding_type
// ON MATCH SET
//   f.finding_type = CASE
//     WHEN $finding_type IS NOT NULL THEN $finding_type ELSE f.finding_type END
// WITH f
// MATCH (p:Paper {id: $paper_id})
// MERGE (p)-[r:HAS_FINDING]->(f)
// ON CREATE SET
//   r.confidence = $confidence,
//   r.evidence   = $evidence
// ON MATCH SET
//   r.confidence = CASE WHEN $confidence > r.confidence
//                  THEN $confidence ELSE r.confidence END,
//   r.evidence   = CASE WHEN $confidence > r.confidence
//                  THEN $evidence ELSE r.evidence END;

// ---------------------------------------------------------------------------
// Concept hierarchy  [v2]
// merge_concept_based_on() + merge_concept_extends() trong neo4j_client.py.
// ---------------------------------------------------------------------------

// -- Concept BASED_ON (sử dụng / phụ thuộc vào) --
// Cả 2 node phải tồn tại trước khi MERGE edge.
// graph_builder._merge_concept_hierarchy() tạo placeholder node nếu cần.
//
// MATCH (c1:Concept {name: $child_concept})
// MATCH (c2:Concept {name: $parent_concept})
// MERGE (c1)-[r:BASED_ON]->(c2)
// ON CREATE SET r.confidence = $confidence
// ON MATCH SET  r.confidence = CASE WHEN $confidence > r.confidence
//                              THEN $confidence ELSE r.confidence END;

// -- Concept EXTENDS (kế thừa / cải tiến)  [v2] --
// MATCH (c1:Concept {name: $child_concept})
// MATCH (c2:Concept {name: $parent_concept})
// MERGE (c1)-[r:EXTENDS]->(c2)
// ON CREATE SET r.confidence = $confidence
// ON MATCH SET  r.confidence = CASE WHEN $confidence > r.confidence
//                              THEN $confidence ELSE r.confidence END;

// ---------------------------------------------------------------------------
// Citation graph  [v2]
// merge_paper_supports() + merge_paper_contradicts() trong neo4j_client.py.
// ---------------------------------------------------------------------------

// -- SUPPORTS (source ủng hộ target)  [v2] --
// target có thể chưa tồn tại → MERGE tạo stub placeholder.
// CẢNH BÁO: source phải tồn tại — raise Neo4jMergeError nếu không tìm thấy.
//
// MATCH (p1:Paper {id: $source_paper_id})
// MERGE (p2:Paper {id: $target_paper_id})
// ON CREATE SET
//   p2.processing_status = 'stub',
//   p2.created_at        = datetime()
// MERGE (p1)-[r:SUPPORTS]->(p2)
// ON CREATE SET r.finding_desc = $finding_desc, r.confidence = $confidence
// ON MATCH SET  r.confidence   = CASE WHEN $confidence > r.confidence
//                                THEN $confidence ELSE r.confidence END;

// -- CONTRADICTS (source phản bác target)  [v2] --
// MATCH (p1:Paper {id: $source_paper_id})
// MERGE (p2:Paper {id: $target_paper_id})
// ON CREATE SET
//   p2.processing_status = 'stub',
//   p2.created_at        = datetime()
// MERGE (p1)-[r:CONTRADICTS]->(p2)
// ON CREATE SET r.finding_desc = $finding_desc, r.confidence = $confidence
// ON MATCH SET  r.confidence   = CASE WHEN $confidence > r.confidence
//                                THEN $confidence ELSE r.confidence END;

// ---------------------------------------------------------------------------
// Citation edge
// merge_citation() trong neo4j_client.py — 3 path theo thứ tự ưu tiên.
// ---------------------------------------------------------------------------

// -- Path 1: internal doc_id (confidence = 1.0)  [v2-11] --
// MATCH (p1:Paper {id: $citing_paper_id})
// MATCH (p2:Paper {id: $cited_doc_id})
// MERGE (p1)-[r:CITES]->(p2)
// ON CREATE SET r.confidence = 1.0, r.raw_ref_text = $raw_ref_text;

// -- Path 2: doi (confidence = 1.0) --
// MATCH (p1:Paper {id: $citing_paper_id})
// MERGE (p2:Paper {doi: $cited_doi})
// ON CREATE SET
//   p2.id                = randomUUID(),
//   p2.title             = $cited_title,
//   p2.year              = $cited_year,
//   p2.processing_status = 'stub',
//   p2.created_at        = datetime()
// MERGE (p1)-[r:CITES]->(p2)
// ON CREATE SET r.confidence = 1.0, r.raw_ref_text = $raw_ref_text;

// -- Path 3: title + year (confidence = 0.8 | 0.6) --
// CẢNH BÁO: $cited_year KHÔNG ĐƯỢC là null — dùng 0 làm sentinel.
// MATCH (p1:Paper {id: $citing_paper_id})
// MERGE (p2:Paper {title: $cited_title, year: $cited_year})
// ON CREATE SET
//   p2.id                = randomUUID(),
//   p2.processing_status = 'stub',
//   p2.created_at        = datetime()
// ON MATCH SET
//   p2.processing_status = CASE
//     WHEN p2.processing_status = 'stub' THEN 'stub'
//     ELSE p2.processing_status
//   END
// MERGE (p1)-[r:CITES]->(p2)
// ON CREATE SET r.confidence = $confidence, r.raw_ref_text = $raw_ref_text;

// ---------------------------------------------------------------------------
// SIMILAR_TO + Chunk
// ---------------------------------------------------------------------------

// -- SIMILAR_TO edge (offline recommendation job) --
// Threshold do caller quyết định trước khi gọi; client không check.
// MATCH (p1:Paper {id: $paper_id_1})
// MATCH (p2:Paper {id: $paper_id_2})
// MERGE (p1)-[r:SIMILAR_TO]->(p2)
// ON CREATE SET
//   r.score          = $score,
//   r.shared_methods = $shared_methods,
//   r.shared_authors = $shared_authors
// ON MATCH SET
//   r.score = $score;

// -- Chunk + edge HAS_CHUNK --
// Gọi sau khi Qdrant upsert xong để qdrant_id đã tồn tại.
// MERGE (c:Chunk {qdrant_id: $qdrant_id})
// ON CREATE SET
//   c.id      = randomUUID(),
//   c.section = $section,
//   c.page    = $page,
//   c.text    = $chunk_text
// WITH c
// MATCH (p:Paper {id: $paper_id})
// MERGE (p)-[:HAS_CHUNK]->(c);
