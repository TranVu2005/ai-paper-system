from __future__ import annotations

from pathlib import Path

from ingestion.processors.multimodal_normalizer import normalize_multimodal
from ingestion.schema.document_schema import Figure, Formula, Table, UnifiedDocument


def _log(test_name: str, input_file: str, step: str, expected: str, actual: str, status: str) -> None:
    print(f"[TEST] {test_name}")
    print(f"[INPUT] {input_file}")
    print(f"[STEP] {step}")
    print(f"[EXPECTED] {expected}")
    print(f"[ACTUAL] {actual}")
    print(f"[STATUS] {status}")


def test_multimodal_normalizer_normalize_formulas_and_figures(tmp_path: Path) -> None:
    image_src = tmp_path / "source.png"
    image_src.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\x89\x8f\x82"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    doc = UnifiedDocument(
        title="Multimodal Test",
        source_type="html",
        source_file="",
        figures=[Figure(caption="Figure 1: Demo", index=1, path=str(image_src))],
        formulas=[Formula(raw="\\alpha + \\beta", formula_type="inline", display_text=None, source_format="latex")],
        tables=[Table(caption="Table 1", headers=["A"], data=[["1"]])],
    )
    out = normalize_multimodal(doc, output_root=tmp_path / "out")
    assert out.figures[0].path is not None, "[FAIL] Expected normalized figure path but got None"
    assert Path(out.figures[0].path).exists(), (
        f"[FAIL] Expected normalized figure file to exist at {out.figures[0].path}"
    )
    assert out.formulas[0].display_text is not None, "[FAIL] Expected formula display_text backfilled"
    _log(
        "Multimodal Normalizer - figures/formulas",
        "synthetic figure+formula",
        "normalize_multimodal",
        "copy figure and fill formula display_text",
        f"figure_path={out.figures[0].path}, formula_display={out.formulas[0].display_text}",
        "PASS",
    )


def test_multimodal_normalizer_table_integrity(tmp_path: Path) -> None:
    doc = UnifiedDocument(
        title="Table Integrity Test",
        source_type="pdf",
        source_file="missing.pdf",
        tables=[Table(caption="Table X", headers=["H1", "H2"], data=[["v1", "v2"]])],
    )
    out = normalize_multimodal(doc, output_root=tmp_path / "out2")
    assert out.tables == doc.tables, "[FAIL] Expected tables unchanged by multimodal normalization"
    _log(
        "Multimodal Normalizer - table integrity",
        "synthetic table-only doc",
        "normalize_multimodal",
        "tables unchanged",
        f"table_count={len(out.tables)}",
        "PASS",
    )
