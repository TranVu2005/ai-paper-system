from __future__ import annotations

from ingestion.parsers.figure_parser import parse_figures
from ingestion.schema.document_schema import Figure


def _log(test_name: str, input_file: str, step: str, expected: str, actual: str, status: str) -> None:
    print(f"[TEST] {test_name}")
    print(f"[INPUT] {input_file}")
    print(f"[STEP] {step}")
    print(f"[EXPECTED] {expected}")
    print(f"[ACTUAL] {actual}")
    print(f"[STATUS] {status}")


def test_figure_parser_caption_and_type_detection() -> None:
    figures = [
        Figure(caption="Figure 1: Overview of the architecture", figure_type="image"),
        Figure(caption="Fig. 2 - Training loss curve", figure_type="image"),
    ]
    out = parse_figures(figures)
    assert out[0].caption.lower().startswith("overview"), (
        f"[FAIL] Expected cleaned caption without Figure prefix but got {out[0].caption}"
    )
    assert out[0].figure_type == "diagram", (
        f"[FAIL] Expected figure_type='diagram' but got {out[0].figure_type}"
    )
    assert out[1].figure_type == "chart", (
        f"[FAIL] Expected figure_type='chart' but got {out[1].figure_type}"
    )
    _log(
        "Figure Parser - caption/type",
        "synthetic figure-heavy",
        "parse_figures",
        "clean caption and classify type",
        f"types={[f.figure_type for f in out]}, captions={[f.caption for f in out]}",
        "PASS",
    )


def test_figure_parser_multiple_and_missing_caption() -> None:
    figures = [
        Figure(caption="", figure_type="image"),
        Figure(caption="Figure 4: ", figure_type="image"),
        Figure(caption="Figure 5: Pipeline workflow", figure_type="image"),
    ]
    out = parse_figures(figures)
    assert len(out) == 3, f"[FAIL] Expected 3 figures but got {len(out)}"
    assert out[0].alt_text is not None, "[FAIL] Expected generated alt_text for missing caption figure"
    assert out[2].index == 3, f"[FAIL] Expected index=3 for third figure but got {out[2].index}"
    _log(
        "Figure Parser - multiple/missing caption",
        "synthetic figure set",
        "parse_figures",
        "all figures indexed and alt_text generated",
        f"indices={[f.index for f in out]}, alt0={out[0].alt_text}",
        "PASS",
    )
