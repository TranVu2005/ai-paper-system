"""
scripts/build_kg.py
------------------------------
Xây Knowledge Graph từ các file JSON đã ingest sẵn trong data/processed/.
KHÔNG đọc PDF — chỉ đọc JSON, reconstruct UnifiedDocument rồi chạy KG pipeline.

Flow mỗi file JSON:
  1. Load JSON → reconstruct UnifiedDocument (Section/Figure/Table/Formula/Reference)
  2. EntityExtractor    — extract Concept/Evidence/Metric/Finding/Author từ doc
  3. build_phase1       — MERGE Paper/Author/Institution/Venue/Topic/Citation vào Neo4j
                          → trả về author_id_map thực (uuid5 từ lookup fulltext)
  4. RelationExtractor  — map relations dùng author_id_map từ phase1
  5. build_phase2+3     — MERGE Concept/Evidence/Metric/Finding edges + set kg_built
  6. GraphUpdater.run_all — merge aliases, update provenance (với verified confidence),
                            check consistency

Thứ tự 3 → 4 là bắt buộc: author_id_map chỉ chính xác sau khi build_phase1
merge Author node xong. KHÔNG sinh author_id_map thủ công từ uuid5(name) —
sẽ lệch với key Neo4j đang lưu (uuid5 từ name + affiliation + fulltext lookup).

Sau khi chạy xong:
  - In summary table tại terminal
  - Lưu report JSON → data/processed/kg_report_<timestamp>.json
  - In sẵn Cypher queries để paste vào Neo4j Browser

Usage:
  # Chạy tất cả JSON trong data/processed/ (default)
  python scripts/build_kg.py

  # Chỉ định folder JSON khác
  python scripts/build_kg.py --json-dir path/to/jsons/

  # Giới hạn số lượng file
  python scripts/build_kg.py --limit 5

  # Verbose: xem chi tiết entity/relation từng file
  python scripts/build_kg.py --verbose

  # Skip file đã có status kg_built trong Neo4j
  python scripts/build_kg.py --skip-built

  # Dùng Ollama model khác
  python scripts/build_kg.py --ollama-model qwen2.5:7b

  # Dùng Anthropic backend thay vì Ollama
  python scripts/build_kg.py --backend anthropic
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Project root ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
for _noisy in ("httpx", "httpcore", "anthropic", "neo4j", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("build_kg")


# ── Color helpers ─────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"
    BLUE   = "\033[94m"

def ok(m):   return f"{C.GREEN}✓ {m}{C.RESET}"
def fail(m): return f"{C.RED}✗ {m}{C.RESET}"
def info(m): return f"{C.CYAN}→ {m}{C.RESET}"
def warn(m): return f"{C.YELLOW}⚠ {m}{C.RESET}"
def hdr(m):  return f"\n{C.BOLD}{C.BLUE}{'━'*65}\n  {m}\n{'━'*65}{C.RESET}"


# ── Result dataclass ──────────────────────────────────────────────────────────
@dataclass
class FileResult:
    file_name:    str
    paper_id:     Optional[str] = None
    title:        str           = ""
    authors:      list          = field(default_factory=list)
    year:         Optional[int] = None

    load_ok:      bool  = False
    load_time:    float = 0.0
    load_error:   str   = ""

    entity_ok:    bool  = False
    entity_time:  float = 0.0
    entity_error: str   = ""
    # [v2] đổi methods/datasets/tasks → concepts/evidences/metrics/findings
    concepts_found:  list = field(default_factory=list)
    evidences_found: list = field(default_factory=list)
    metrics_found:   list = field(default_factory=list)
    findings_found:  list = field(default_factory=list)
    authors_parsed:  list = field(default_factory=list)

    relation_ok:       bool  = False
    relation_time:     float = 0.0
    relation_error:    str   = ""
    relations_summary: str   = ""

    # builder_time = phase1_time + phase2+3_time (cộng dồn)
    builder_ok:    bool  = False
    builder_time:  float = 0.0
    builder_error: str   = ""

    updater_ok:      bool  = False
    updater_time:    float = 0.0
    updater_error:   str   = ""
    updater_summary: str   = ""

    skipped: bool = False   # True nếu skip vì đã kg_built

    @property
    def fully_ok(self) -> bool:
        return all([self.load_ok, self.entity_ok,
                    self.relation_ok, self.builder_ok, self.updater_ok])

    @property
    def total_time(self) -> float:
        return (self.load_time + self.entity_time + self.relation_time
                + self.builder_time + self.updater_time)

    def first_error(self) -> str:
        for step, err in [
            ("load",     self.load_error),
            ("entity",   self.entity_error),
            ("relation", self.relation_error),
            ("builder",  self.builder_error),
            ("updater",  self.updater_error),
        ]:
            if err:
                last = [l for l in err.strip().splitlines() if l.strip()]
                return f"[{step}] {last[-1][:100]}" if last else f"[{step}] error"
        return ""


# ── Load UnifiedDocument từ JSON ─────────────────────────────────────────────
def load_doc_from_json(json_path: Path):
    """
    Reconstruct UnifiedDocument từ file JSON.
    Trả về (doc, error_string). Thành công → error_string = "".

    Tất cả nested list (sections, figures, tables, formulas, references)
    được reconstruct thành đúng dataclass object — không phải dict thuần.
    """
    try:
        from ingestion.schema.document_schema import (
            UnifiedDocument, Section, Figure, Table, Formula, Reference
        )

        with open(json_path, encoding="utf-8") as f:
            raw = json.load(f)

        # ── Helper: chỉ lấy keys hợp lệ, bỏ key lạ ──────────────────
        def _only(cls, d: dict) -> dict:
            from dataclasses import fields as _f
            valid = {field.name for field in _f(cls)}
            return {k: v for k, v in d.items() if k in valid}

        # ── Section có thể lồng nhau qua subsections ─────────────────
        def _section(d: dict):
            subs = [_section(s) for s in (d.get("subsections") or [])]
            return Section(**{**_only(Section, d), "subsections": subs})

        sections   = [_section(s)                     for s in (raw.get("sections")   or [])]
        figures    = [Figure(**_only(Figure, f))       for f in (raw.get("figures")    or [])]
        tables     = [Table(**_only(Table, t))         for t in (raw.get("tables")     or [])]
        formulas   = [Formula(**_only(Formula, fm))    for fm in (raw.get("formulas")  or [])]
        references = [Reference(**_only(Reference, r)) for r in (raw.get("references") or [])]

        # ── Parse created_at từ ISO string ────────────────────────────
        created_at = raw.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = None

        doc = UnifiedDocument(
            title             = raw.get("title", "Unknown"),
            authors           = raw.get("authors") or [],
            year              = raw.get("year"),
            journal           = raw.get("journal"),
            doi               = raw.get("doi"),
            keywords          = raw.get("keywords") or [],
            doc_id            = raw.get("doc_id"),
            abstract          = raw.get("abstract", ""),
            full_text         = raw.get("full_text", ""),
            sections          = sections,
            tables            = tables,
            figures           = figures,
            formulas          = formulas,
            references        = references,
            source_file       = raw.get("source_file", ""),
            source_type       = raw.get("source_type", ""),
            language          = raw.get("language"),
            page_count        = raw.get("page_count"),
            processing_status = raw.get("processing_status", "parsed"),
            errors            = raw.get("errors") or [],
            created_at        = created_at,
            chunk_ids         = raw.get("chunk_ids") or [],
            publisher         = raw.get("publisher"),
            volume            = raw.get("volume"),
            issue             = raw.get("issue"),
            pages             = raw.get("pages"),
            citation_count    = raw.get("citation_count"),
        )
        return doc, ""

    except Exception:
        return None, traceback.format_exc()


# ── Neo4j connect ─────────────────────────────────────────────────────────────
def connect_neo4j(uri: str, user: str, password: str):
    try:
        from storage.graph_db.neo4j_client import Neo4jClient, Neo4jConfig
    except ImportError as e:
        raise ImportError(f"Không import được Neo4jClient: {e}")

    client = Neo4jClient(Neo4jConfig(uri=uri, username=user, password=password, database=database,))
    client.connect()
    if not client.ping():
        raise ConnectionError(
            f"Không ping được Neo4j! URI={uri}\n"
            "Chạy: docker run -d --name neo4j -p 7474:7474 -p 7687:7687 "
            "-e NEO4J_AUTH=neo4j/password123 neo4j:5"
        )
    client.ensure_schema()
    return client


# ── LLM backend ───────────────────────────────────────────────────────────────
def get_llm_backend(backend: str, model: str, base_url: str):
    if backend == "anthropic":
        from ai_module.kg.entity_extractor import AnthropicBackend
        llm = AnthropicBackend(model=model)
        print(ok(f"LLM: AnthropicBackend (model={model})"))
    else:
        from ai_module.kg.entity_extractor import OllamaBackend
        llm = OllamaBackend(model=model, base_url=base_url, timeout=300.0)
        print(ok(f"LLM: OllamaBackend (model={model}, url={base_url})"))
    return llm


# ── Neo4j stats ───────────────────────────────────────────────────────────────
def get_neo4j_stats(client) -> dict:
    """
    Query thống kê graph hiện tại.
    [v2] Đổi Method/Dataset/Task → Concept/Evidence/Metric/Finding.
         Đổi USES_METHOD/ADDRESSES_TASK → USES_CONCEPT/ACHIEVES_METRIC.
         Giữ EVALUATES_ON (tên edge không đổi, chỉ target label đổi).
    """
    queries = {
        "papers":           "MATCH (p:Paper)    RETURN count(p) AS n",
        "authors":          "MATCH (a:Author)   RETURN count(a) AS n",
        # [v2] node types mới
        "concepts":         "MATCH (c:Concept)  RETURN count(c) AS n",
        "evidences":        "MATCH (e:Evidence) RETURN count(e) AS n",
        "metrics":          "MATCH (m:Metric)   RETURN count(m) AS n",
        "findings":         "MATCH (f:Finding)  RETURN count(f) AS n",
        # [v2] edge types mới
        "USES_CONCEPT":     "MATCH ()-[r:USES_CONCEPT]->()     RETURN count(r) AS n",
        "EVALUATES_ON":     "MATCH ()-[r:EVALUATES_ON]->()     RETURN count(r) AS n",
        "ACHIEVES_METRIC":  "MATCH ()-[r:ACHIEVES_METRIC]->()  RETURN count(r) AS n",
        "BASED_ON":         "MATCH ()-[r:BASED_ON]->()         RETURN count(r) AS n",
        "EXTENDS":          "MATCH ()-[r:EXTENDS]->()          RETURN count(r) AS n",
        "SUPPORTS":         "MATCH ()-[r:SUPPORTS]->()         RETURN count(r) AS n",
        "CONTRADICTS":      "MATCH ()-[r:CONTRADICTS]->()      RETURN count(r) AS n",
        "CITES":            "MATCH ()-[r:CITES]->()            RETURN count(r) AS n",
    }
    stats = {}
    for k, q in queries.items():
        try:
            rows = client.execute_read(q)
            stats[k] = rows[0]["n"] if rows else 0
        except Exception:
            stats[k] = "?"
    return stats


# ── Xử lý 1 file JSON ────────────────────────────────────────────────────────
def process_json_file(
    json_path: Path,
    neo4j_client,
    llm_backend,
    builder,         # [fix-apoc] GraphBuilder instance dùng chung — tạo 1 lần ngoài loop
    updater,         # [fix-apoc] GraphUpdater instance dùng chung — tạo 1 lần ngoài loop
    verbose: bool = False,
    skip_built: bool = False,
) -> FileResult:
    """
    Chạy KG pipeline cho 1 file JSON theo đúng thứ tự bắt buộc:
      load → entity → phase1 → relation → phase2+3 → updater

    Thứ tự phase1 → relation là cứng:
      build_phase1() merge Author node và trả về author_id_map thực
      (uuid5 từ name+affiliation sau lookup fulltext).
      RelationExtractor cần author_id_map này để build WROTE edge đúng.
      KHÔNG sinh author_id_map thủ công — sẽ lệch với uuid5 Neo4j đang lưu.

    [v2] ExtractedEntities dùng concepts/evidences/metrics/findings thay
         methods/datasets/tasks.
    [v2] build_phase1() chỉ trả về author_id_map (không trả về paper_id).
    [v2] GraphUpdater.run_all() không nhận param relations.

    [fix-apoc] builder và updater được truyền vào thay vì tạo mới mỗi lần,
               tránh _check_apoc() chạy lại mỗi paper.
    """
    result = FileResult(file_name=json_path.name)

    # ── Import KG pipeline modules một lần ───────────────────────────
    try:
        from ai_module.kg.entity_extractor   import EntityExtractor
        from ai_module.kg.relation_extractor import RelationExtractor
    except ImportError as e:
        result.load_error = str(e)
        logger.error(fail(f"  Import FAILED: {e}"))
        return result

    # ── STEP 1: Load JSON → UnifiedDocument ──────────────────────────
    t0 = time.time()
    doc, err = load_doc_from_json(json_path)
    result.load_time = time.time() - t0

    if doc is None:
        result.load_error = err
        # [fix-traceback] Bỏ splitlines()[-1][:100] — log full traceback để dễ debug
        logger.error(fail(f"  Load FAILED:\n{err}"))
        return result

    result.load_ok = True
    result.title   = doc.title or json_path.stem
    result.authors = (doc.authors or [])[:5]
    result.year    = doc.year

    # Resolve paper_id sớm — dùng cho skip check và các bước sau
    paper_id = builder.resolve_paper_id(doc)
    result.paper_id = paper_id

    logger.info(ok(
        f"  Load OK — title='{doc.title[:55]}' "
        f"sections={len(doc.sections)} refs={len(doc.references or [])} "
        f"({result.load_time:.2f}s)"
    ))

    # ── Atomic claim — tránh 2 máy cùng process 1 paper ─────────────
    existing = neo4j_client.get_paper_by_id(paper_id)
    if existing:
        status = existing.get("processing_status")
        if status == "kg_built":
            result.skipped = True
            result.load_ok = result.entity_ok = True
            result.relation_ok = result.builder_ok = result.updater_ok = True
            logger.info(warn(f"  Skip — đã kg_built: paper_id={paper_id}"))
            return result
        if status == "processing":
            result.skipped = True
            logger.info(warn(f"  Skip — máy khác đang xử lý: paper_id={paper_id}"))
            return result

    if not neo4j_client.try_claim_paper(paper_id):
        result.skipped = True
        logger.info(warn(f"  Skip — claim thất bại: paper_id={paper_id}"))
        return result

    # ── STEPS 2–6: pipeline chính ─────────────────────────────────────
    #
    # Sau khi claim thành công, nếu pipeline fail ở bất kỳ bước nào
    # trước khi set 'kg_built', rollback về 'parsed' để lần chạy sau
    # hoặc máy khác có thể retry.
    #
    # Điều kiện rollback: result.builder_ok vẫn False khi thoát
    # (tức là phase2+3 chưa hoàn thành, 'kg_built' chưa được set).
    #
    try:
        # ── STEP 2: Entity Extraction ─────────────────────────────────
        entities = None
        t0 = time.time()
        try:
            extractor = EntityExtractor(llm_backend)
            entities  = extractor.extract(doc)

            result.entity_ok = True
            # [v2] ExtractedEntities.concepts/evidences/metrics/findings
            result.concepts_found  = [c.name for c in entities.concepts]
            result.evidences_found = [e.name for e in entities.evidences]
            result.metrics_found   = [
                f"{m.name}={m.value}" if m.value is not None else m.name
                for m in entities.metrics
            ]
            result.findings_found  = [f.finding_type for f in entities.findings]
            result.authors_parsed  = [a.name for a in entities.authors]

            if verbose:
                logger.info(f"    Concepts  : {result.concepts_found}")
                logger.info(f"    Evidences : {result.evidences_found}")
                logger.info(f"    Metrics   : {result.metrics_found}")
                logger.info(f"    Findings  : {result.findings_found}")
                logger.info(f"    Authors   : {result.authors_parsed}")

            logger.info(ok(
                f"  Entity OK — "
                f"concepts={len(entities.concepts)} "
                f"evidences={len(entities.evidences)} "
                f"metrics={len(entities.metrics)} "
                f"findings={len(entities.findings)} "
                f"({time.time()-t0:.1f}s)"
            ))
        except Exception:
            result.entity_error = traceback.format_exc()
            # [fix-traceback] Log full traceback thay vì chỉ dòng cuối
            logger.warning(fail(f"  Entity FAILED:\n{result.entity_error}"))

        result.entity_time = time.time() - t0
        if not result.entity_ok or entities is None:
            return result

        # ── STEP 3: build_phase1 → Paper / Author / Venue / Citation ──
        #
        # Mục đích kép:
        #   (a) MERGE Paper, Author, Institution, Venue, Topic, Citation vào Neo4j
        #   (b) Trả về author_id_map = {author_name: author_id} thực —
        #       uuid5 được tính từ (name, affiliation) sau lookup fulltext,
        #       nhất quán với key Neo4j đang lưu.
        #
        # [v2] build_phase1() chỉ trả về author_id_map (dict), không trả về paper_id.
        #      paper_id đã được resolve ở trên qua builder.resolve_paper_id(doc).
        #
        # PHẢI chạy trước RelationExtractor vì RelationExtractor dùng
        # author_id_map để build WROTE edge. Nếu dùng uuid5 tự sinh
        # (không có affiliation, không lookup) sẽ tạo Author node mồ côi.
        #
        author_id_map: dict[str, str] = {}
        t0 = time.time()
        try:
            # [v2] build_phase1 trả về author_id_map, KHÔNG trả về paper_id
            author_id_map = builder.build_phase1(doc, entities, paper_id=paper_id)
            result.builder_ok = False   # reset — phase1 OK nhưng chưa phải done
            logger.info(ok(
                f"  Phase1 OK — paper_id={paper_id} "
                f"authors_merged={len(author_id_map)} ({time.time()-t0:.1f}s)"
            ))
        except Exception:
            result.builder_error = traceback.format_exc()
            result.builder_time  = time.time() - t0
            # [fix-traceback] Log full traceback thay vì chỉ dòng cuối
            logger.warning(fail(f"  Phase1 FAILED:\n{result.builder_error}"))
            return result

        result.builder_time = time.time() - t0   # phase1 time; cộng thêm sau phase2+3

        # ── STEP 4: Relation Extraction ───────────────────────────────
        #
        # Dùng author_id_map từ build_phase1 (không sinh thủ công).
        # [v2] RelationResult chứa uses_concept/evaluates_on/achieves_metric/
        #      extends/supports/contradicts thay vì uses_method/addresses_task.
        #
        relations = None
        t0 = time.time()
        try:
            rel_extractor = RelationExtractor(llm_backend, confidence_threshold=0.6)
            relations     = rel_extractor.extract(doc, paper_id, entities, author_id_map)

            result.relation_ok       = True
            result.relations_summary = relations.summary()
            logger.info(ok(
                f"  Relation OK — {relations.summary()} ({time.time()-t0:.1f}s)"
            ))
        except Exception:
            result.relation_error = traceback.format_exc()
            # [fix-traceback] Log full traceback thay vì chỉ dòng cuối
            logger.warning(fail(f"  Relation FAILED:\n{result.relation_error}"))

        result.relation_time = time.time() - t0
        if not result.relation_ok or relations is None:
            return result

        # ── STEP 5: build_phase2 + phase3 + set kg_built ──────────────
        #
        # build_phase2: MERGE Concept/Evidence/Metric/Finding edges
        #               với relations đã verify qua LLM
        # build_phase3: MERGE Chunk nodes (empty ở đây — embedding pipeline đẩy riêng)
        # set_paper_status: mark 'kg_built' sau khi cả 2 phase xong
        #
        t0 = time.time()
        try:
            builder.build_phase2(paper_id, entities, relations=relations)
            builder.build_phase3(paper_id, {})   # chunk_map rỗng — embedding pipeline riêng
            neo4j_client.set_paper_status(paper_id, "kg_built")

            result.builder_ok = True
            logger.info(ok(
                f"  Phase2+3 OK — paper_id={paper_id} ({time.time()-t0:.1f}s)"
            ))
        except Exception:
            result.builder_error = traceback.format_exc()
            # [fix-traceback] Log full traceback thay vì chỉ dòng cuối
            logger.warning(fail(f"  Phase2+3 FAILED:\n{result.builder_error}"))

        result.builder_time += time.time() - t0   # cộng dồn phase1 + phase2+3
        if not result.builder_ok:
            return result

        # ── STEP 6: Graph Updater ──────────────────────────────────────
        #
        # Thứ tự nội bộ (bắt buộc theo GraphUpdater.run_all):
        #   1. EntityMerger      — gộp Concept/Evidence alias duplicate (APOC nếu có)
        #   2. ProvenanceUpdater — update confidence/evidence trên USES_CONCEPT/EVALUATES_ON
        #   3. ConsistencyChecker— phát hiện duplicate edge, orphan, no-evidence
        #
        # [v2] GraphUpdater.run_all() không nhận param relations —
        #      ProvenanceUpdater đọc thẳng từ ExtractedEntities.
        # [fix-apoc] Dùng updater instance được truyền vào (tạo 1 lần ngoài loop)
        #            thay vì tạo mới mỗi paper → _check_apoc() chỉ chạy 1 lần.
        #
        t0 = time.time()
        try:
            report = updater.run_all(
                paper_id,
                entities,
                check_orphans=True,
            )

            result.updater_ok      = True
            result.updater_summary = report.summary

            if report.has_errors:
                errs = [i.description for i in report.consistency_issues
                        if i.severity == "error"]
                logger.warning(warn(f"  Updater consistency errors: {errs}"))

            logger.info(ok(f"  Updater OK — {report.summary} ({time.time()-t0:.1f}s)"))

        except Exception:
            result.updater_error = traceback.format_exc()
            # [fix-traceback] Log full traceback thay vì chỉ dòng cuối
            logger.warning(fail(f"  Updater FAILED:\n{result.updater_error}"))

        result.updater_time = time.time() - t0
        return result

    finally:
        # ── Rollback nếu pipeline fail trước khi hoàn thành phase2+3 ──
        #
        # result.builder_ok chỉ True sau khi set_paper_status('kg_built') thành công.
        # Nếu False tại đây: claim đã được set nhưng quá trình xử lý không hoàn tất
        # → reset về 'parsed' để retry được.
        #
        # Không rollback khi:
        #   - result.skipped  : không phải lỗi, paper không thuộc phiên này
        #   - result.builder_ok: phase2+3 hoàn thành, 'kg_built' đã set đúng
        #
        if not result.skipped and not result.builder_ok:
            try:
                neo4j_client.set_paper_status(paper_id, "parsed")
                logger.info(warn(
                    f"  Rollback → status='parsed': paper_id={paper_id}"
                ))
            except Exception as _rb_err:
                logger.warning(fail(
                    f"  Rollback FAILED (status có thể bị kẹt ở 'processing'): "
                    f"{_rb_err}"
                ))


# ── Summary table ─────────────────────────────────────────────────────────────
def print_summary_table(results: list[FileResult]):
    print(hdr("KG BUILD SUMMARY"))

    col_w = [35, 6, 6, 6, 6, 6, 8, 28]
    cols  = ["File", "Load", "Entity", "Relat.", "Build", "Update", "Time(s)", "Note"]
    print("  " + " │ ".join(c.ljust(w) for c, w in zip(cols, col_w)))
    print("  " + "─┼─".join("─" * w for w in col_w))

    passed = skipped = 0
    for r in results:
        if r.skipped:
            skipped += 1
            note = "skip — already kg_built"
        elif r.fully_ok:
            passed += 1
            note = r.paper_id[:26] if r.paper_id else ""
        else:
            note = r.first_error()[:26]

        def _cell(flag, prerequisite=True):
            if r.skipped:     return f"{C.CYAN}{'SKIP'.ljust(6)}{C.RESET}"
            if flag:          return f"{C.GREEN}{'PASS'.ljust(6)}{C.RESET}"
            if prerequisite:  return f"{C.RED}{'FAIL'.ljust(6)}{C.RESET}"
            return "─".ljust(6)

        row_cells = [
            r.file_name[:col_w[0]].ljust(col_w[0]),
            _cell(r.load_ok),
            _cell(r.entity_ok,    r.load_ok),
            _cell(r.relation_ok,  r.entity_ok),
            _cell(r.builder_ok,   r.relation_ok),
            _cell(r.updater_ok,   r.builder_ok),
            f"{r.total_time:.1f}".ljust(col_w[6]),
            note[:col_w[7]].ljust(col_w[7]),
        ]
        print("  " + " │ ".join(row_cells))

    total = len(results)
    color = C.GREEN if passed == total - skipped else C.YELLOW if passed > 0 else C.RED
    print(f"\n  {color}{C.BOLD}{passed}/{total - skipped} built  │  {skipped} skipped{C.RESET}")
    avg_t = sum(r.total_time for r in results) / total if total else 0
    print(f"  Average: {avg_t:.1f}s/file  │  Total: {sum(r.total_time for r in results):.0f}s")


# ── Cypher queries helper ─────────────────────────────────────────────────────
def print_neo4j_queries(results: list[FileResult], neo4j_client):
    """
    In sẵn Cypher queries để paste vào Neo4j Browser.
    [v2] Đổi USES_METHOD/ADDRESSES_TASK → USES_CONCEPT/ACHIEVES_METRIC.
         Đổi label Method/Dataset/Task → Concept/Evidence/Metric/Finding.
    """
    print(hdr("CYPHER QUERIES — Paste vào Neo4j Browser (localhost:7474)"))
    print(f"""
{C.BOLD}{C.CYAN}1. Tổng quan graph{C.RESET}
MATCH (n)
RETURN labels(n)[0] AS label, count(n) AS total
ORDER BY total DESC;

{C.BOLD}{C.CYAN}2. Paper vừa build — kèm số lượng entity liên kết{C.RESET}
MATCH (p:Paper)
OPTIONAL MATCH (p)-[:USES_CONCEPT]->(c:Concept)
OPTIONAL MATCH (p)-[:EVALUATES_ON]->(e:Evidence)
OPTIONAL MATCH (p)-[:ACHIEVES_METRIC]->(m:Metric)
OPTIONAL MATCH (p)-[:HAS_FINDING]->(f:Finding)
RETURN p.title    AS title,
       p.year     AS year,
       p.domain   AS domain,
       count(DISTINCT c) AS concepts,
       count(DISTINCT e) AS evidences,
       count(DISTINCT m) AS metrics,
       count(DISTINCT f) AS findings
ORDER BY p.created_at DESC LIMIT 25;

{C.BOLD}{C.CYAN}3. Visualize 1 paper (thay PAPER_ID){C.RESET}
MATCH path = (p:Paper {{id: "PAPER_ID"}})-[r]-(n)
RETURN path LIMIT 50;

{C.BOLD}{C.CYAN}4. Top Concept được dùng nhiều nhất{C.RESET}
MATCH (p:Paper)-[r:USES_CONCEPT]->(c:Concept)
RETURN c.name     AS concept,
       c.category AS category,
       c.domain   AS domain,
       count(p)   AS papers,
       avg(r.confidence) AS avg_conf
ORDER BY papers DESC LIMIT 20;

{C.BOLD}{C.CYAN}5. Top Evidence (dataset/benchmark) được đánh giá nhiều nhất{C.RESET}
MATCH (p:Paper)-[r:EVALUATES_ON]->(e:Evidence)
RETURN e.name          AS evidence,
       e.evidence_type AS type,
       e.language      AS lang,
       count(p)        AS papers
ORDER BY papers DESC LIMIT 20;

{C.BOLD}{C.CYAN}6. Metric tốt nhất theo dataset{C.RESET}
MATCH (p:Paper)-[r:ACHIEVES_METRIC]->(m:Metric)
WHERE r.value IS NOT NULL
RETURN m.name        AS metric,
       r.measured_on AS dataset,
       r.measured_by AS model,
       r.value       AS value,
       r.unit        AS unit,
       p.title       AS paper
ORDER BY r.value DESC LIMIT 20;

{C.BOLD}{C.CYAN}7. Citation network — paper ủng hộ / phản bác nhau{C.RESET}
MATCH (p1:Paper)-[r:SUPPORTS|CONTRADICTS]->(p2:Paper)
RETURN p1.title AS source,
       type(r)  AS relation,
       p2.title AS target,
       r.confidence AS conf
ORDER BY conf DESC LIMIT 20;

{C.BOLD}{C.CYAN}8. Concept hierarchy — BASED_ON + EXTENDS{C.RESET}
MATCH path = (c1:Concept)-[:BASED_ON|EXTENDS*1..3]->(c2:Concept)
RETURN path LIMIT 30;

{C.BOLD}{C.CYAN}9. Paper không có Concept (extraction có thể fail){C.RESET}
MATCH (p:Paper {{processing_status: 'kg_built'}})
WHERE NOT (p)-[:USES_CONCEPT]->()
RETURN p.id, p.title LIMIT 20;

{C.BOLD}{C.CYAN}10. Finding summary theo loại{C.RESET}
MATCH (p:Paper)-[r:HAS_FINDING]->(f:Finding)
RETURN f.finding_type AS type,
       count(f)       AS total,
       avg(r.confidence) AS avg_conf
ORDER BY total DESC;
""")

    built = [r for r in results if r.fully_ok and not r.skipped]
    if built:
        print(f"{C.BOLD}{C.CYAN}Paper IDs (dùng trong query 3):{C.RESET}")
        for r in built:
            print(f"  {C.GREEN}✓{C.RESET} {r.paper_id}  {C.GRAY}← {r.file_name[:40]}{C.RESET}")

    print(f"\n{C.BOLD}{C.CYAN}Stats Neo4j hiện tại:{C.RESET}")
    try:
        stats = get_neo4j_stats(neo4j_client)
        for k, v in stats.items():
            bar = "█" * min(int(v) // 2, 30) if isinstance(v, int) else ""
            print(f"  {k:<22} {str(v):>5}  {C.GRAY}{bar}{C.RESET}")
    except Exception as e:
        print(warn(f"  Không đọc được stats: {e}"))


# ── Save report JSON ──────────────────────────────────────────────────────────
def save_report(results: list[FileResult], output_dir: Path) -> Path:
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"kg_report_{ts}.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "run_at":  datetime.now().isoformat(),
        "total":   len(results),
        "built":   sum(1 for r in results if r.fully_ok and not r.skipped),
        "skipped": sum(1 for r in results if r.skipped),
        "failed":  sum(1 for r in results if not r.fully_ok and not r.skipped),
        "results": [asdict(r) for r in results],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"Report saved → {path}")
    return path


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Build KG từ JSON đã ingest — không cần đọc PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json-dir",   default="data/processed",
                        help="Thư mục chứa file JSON (default: data/processed)")
    parser.add_argument("--limit",      type=int, default=None, metavar="N",
                        help="Số file tối đa (default: tất cả)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="In chi tiết entity/relation từng file")
    parser.add_argument("--skip-built", action="store_true",
                        help="Skip file đã có status kg_built trong Neo4j")
    parser.add_argument("--backend",    default="ollama",
                        choices=["ollama", "anthropic"],
                        help="LLM backend (default: ollama)")
    parser.add_argument(
        "--database",
        default="system_kg",           # default cho 1000 file
        help="Neo4j database name (default: system_kg)"
    )

    # Neo4j
    parser.add_argument("--neo4j-uri",      default=os.getenv("NEO4J_URI",      ""))
    parser.add_argument("--neo4j-user",     default=os.getenv("NEO4J_USER",     os.getenv("NEO4J_USERNAME", "")))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", ""))

    # Ollama
    parser.add_argument("--ollama-model",    default=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    parser.add_argument("--ollama-url",      default=os.getenv("OLLAMA_URL",   "http://localhost:11434"))

    # Anthropic
    parser.add_argument("--anthropic-model", default=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"))

    args = parser.parse_args()

    json_dir = Path(args.json_dir)
    if not json_dir.exists():
        print(fail(f"Thư mục không tồn tại: {json_dir.resolve()}"))
        sys.exit(1)

    # Lấy tất cả JSON, bỏ qua report files
    json_files = sorted(
        p for p in json_dir.glob("*.json")
        if not p.name.startswith(("e2e_report_", "kg_report_"))
    )
    if args.limit:
        json_files = json_files[:args.limit]

    if not json_files:
        print(warn(f"Không tìm thấy file JSON trong: {json_dir.resolve()}"))
        sys.exit(0)

    print(hdr("Build KG từ JSON Cache"))
    print(f"  JSON dir   : {json_dir.resolve()}")
    print(f"  Files      : {len(json_files)}")
    print(f"  Backend    : {args.backend}")
    print(f"  Neo4j      : {args.neo4j_uri}")
    if args.backend == "ollama":
        print(f"  Ollama     : {args.ollama_url} / {args.ollama_model}")
    else:
        print(f"  Anthropic  : {args.anthropic_model}")
    print(f"  Skip built : {args.skip_built}")
    print(f"  Verbose    : {args.verbose}")

    # ── Connect services ──────────────────────────────────────────────
    print(hdr("Khởi tạo kết nối"))

    print(info("Connecting Neo4j ..."))
    try:
        neo4j_client = connect_neo4j(
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_password,
            database=args.database,        # 🔥 thêm dòng này
        )
        print(ok(f"Neo4j OK — {args.neo4j_uri}"))
    except Exception as e:
        print(fail(f"Neo4j connection failed:\n{e}"))
        sys.exit(1)

    print(info("Initializing LLM backend ..."))
    try:
        llm_model = args.anthropic_model if args.backend == "anthropic" else args.ollama_model
        llm = get_llm_backend(args.backend, llm_model, args.ollama_url)
    except Exception as e:
        print(fail(f"LLM init failed:\n{e}"))
        neo4j_client.close()
        sys.exit(1)

    # ── [fix-apoc] Tạo GraphBuilder + GraphUpdater 1 lần ngoài loop ──
    #
    # GraphUpdater tạo EntityMerger trong __init__, EntityMerger gọi _check_apoc()
    # mỗi lần khởi tạo. Tạo 1 lần ở đây → _check_apoc() chỉ chạy 1 lần duy nhất,
    # không log warning lặp lại mỗi paper.
    #
    print(info("Initializing GraphBuilder + GraphUpdater ..."))
    try:
        from ai_module.kg.graph_builder import GraphBuilder
        from ai_module.kg.graph_updater import GraphUpdater
        builder = GraphBuilder(neo4j_client)
        updater = GraphUpdater(neo4j_client)
        print(ok("GraphBuilder + GraphUpdater OK"))
    except Exception as e:
        print(fail(f"Builder/Updater init failed:\n{e}"))
        neo4j_client.close()
        sys.exit(1)

    # ── Stats trước khi chạy ─────────────────────────────────────────
    print(info("Neo4j stats TRƯỚC:"))
    stats_before = get_neo4j_stats(neo4j_client)
    for k in ["papers", "concepts", "evidences", "metrics", "findings"]:
        print(f"    {k}: {stats_before.get(k, '?')}")

    # ── Chạy từng file ────────────────────────────────────────────────
    print(hdr(f"Xử lý {len(json_files)} file JSON"))

    results: list[FileResult] = []
    total_start = time.monotonic()

    for i, json_path in enumerate(json_files):
        print(f"\n{C.BOLD}[{i+1:02d}/{len(json_files):02d}] {json_path.name}{C.RESET}")
        result = process_json_file(
            json_path    = json_path,
            neo4j_client = neo4j_client,
            llm_backend  = llm,
            builder      = builder,   # [fix-apoc] truyền instance dùng chung
            updater      = updater,   # [fix-apoc] truyền instance dùng chung
            verbose      = args.verbose,
            skip_built   = args.skip_built,
        )
        results.append(result)

        if result.skipped:
            status = warn("Skipped — already kg_built")
        elif result.fully_ok:
            status = ok("Tất cả stages PASS")
        else:
            status = warn(f"Partial: {result.first_error()[:80]}")
        print(f"  → {status}  [{result.total_time:.1f}s]")

    total_elapsed = time.monotonic() - total_start

    # ── Output ────────────────────────────────────────────────────────
    print_summary_table(results)
    print_neo4j_queries(results, neo4j_client)

    report_path = save_report(results, json_dir)
    print(f"\n  {C.CYAN}Report: {report_path}{C.RESET}")
    print(f"  Total elapsed: {total_elapsed:.0f}s")

    try:
        neo4j_client.close()
    except Exception:
        pass

    failed = sum(1 for r in results if not r.fully_ok and not r.skipped)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
