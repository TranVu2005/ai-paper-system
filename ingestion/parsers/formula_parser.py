"""
formula_parser.py
-----------------
Extract và enrich Formula objects từ nhiều nguồn.

Input sources:
  A. list[Formula] từ loader (đã extract sẵn — PDF/DOCX)
  B. raw text string (sections content) — extract LaTeX patterns
  C. HTML string — extract MathML / MathJax

Output: list[Formula] đã được enrich với:
  - formula_type chính xác: inline | block | equation | matrix | system | definition
  - source_format: "latex" | "mathml" | "unknown"
  - raw đã clean (bỏ wrapper thừa)
  - index theo thứ tự xuất hiện
  - display_text: plain-text approximation cho RAG indexing

Strategy:
  - Giữ cả LaTeX và MathML nếu cùng tồn tại, deduplicate bằng normalized raw
  - Regex patterns cho LaTeX extraction từ raw text
  - BeautifulSoup-free MathML extraction bằng regex (không muốn thêm dependency)
  - Classify type dựa trên wrapper + content patterns
  - Block regions ($$..$$ và environments) được mask trước khi extract inline
    để tránh RE_LATEX_INLINE match nhầm bên trong block expressions
"""

import re
import logging
from typing import Optional

from ingestion.schema.document_schema import Formula

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LaTeX extraction patterns
# ---------------------------------------------------------------------------

# Inline: $...$ (không phải $$)
# Dùng negative lookbehind/ahead để tránh match $$
RE_LATEX_INLINE = re.compile(
    r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
    re.DOTALL,
)

# Block: $$...$$
RE_LATEX_BLOCK = re.compile(
    r"\$\$(.+?)\$\$",
    re.DOTALL,
)

# \begin{equation}...\end{equation}
RE_LATEX_EQUATION = re.compile(
    r"\\begin\{equation\*?\}(.+?)\\end\{equation\*?\}",
    re.DOTALL,
)

# \begin{align}...\end{align}  (multi-line system)
RE_LATEX_ALIGN = re.compile(
    r"\\begin\{align\*?\}(.+?)\\end\{align\*?\}",
    re.DOTALL,
)

# \begin{matrix} / \begin{pmatrix} / \begin{bmatrix} / \begin{vmatrix}
RE_LATEX_MATRIX = re.compile(
    r"\\begin\{[pbvBV]?matrix\*?\}(.+?)\\end\{[pbvBV]?matrix\*?\}",
    re.DOTALL,
)

# \begin{cases}...\end{cases}  → system of equations
RE_LATEX_CASES = re.compile(
    r"\\begin\{cases\}(.+?)\\end\{cases\}",
    re.DOTALL,
)

# Definition-like: \begin{definition} / \begin{theorem} / \begin{lemma}
# Giữ nguyên toàn bộ wrapper (bao gồm \begin{...} và \end{...}) trong raw
# để downstream biết env context
RE_LATEX_DEFINITION = re.compile(
    r"(\\begin\{(definition|theorem|lemma|proposition|corollary|proof)\}.+?\\end\{\2\})",
    re.DOTALL | re.IGNORECASE,
)

# Pattern dùng để mask tất cả block regions trước khi extract inline
# Thứ tự: environments trước, $$ sau
RE_BLOCK_REGIONS = re.compile(
    r"\\begin\{(?:equation|align|matrix|pmatrix|bmatrix|vmatrix|cases|"
    r"definition|theorem|lemma|proposition|corollary|proof)\*?\}.+?"
    r"\\end\{(?:equation|align|matrix|pmatrix|bmatrix|vmatrix|cases|"
    r"definition|theorem|lemma|proposition|corollary|proof)\*?\}"
    r"|\$\$.+?\$\$",
    re.DOTALL | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# MathML extraction patterns
# ---------------------------------------------------------------------------

# <math ...>...</math>  (MathML)
RE_MATHML = re.compile(
    r"<math(?:\s[^>]*)?>(.+?)</math>",
    re.DOTALL | re.IGNORECASE,
)

# MathJax script blocks: <script type="math/tex">...</script>
RE_MATHJAX_INLINE = re.compile(
    r'<script\s+type=["\']math/tex["\']>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)

RE_MATHJAX_DISPLAY = re.compile(
    r'<script\s+type=["\']math/tex;\s*mode=display["\']>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Content-based classification patterns
# ---------------------------------------------------------------------------

MATRIX_PATTERNS = [
    r"\\begin\{[pbvBV]?matrix",
    r"\\matrix",
    r"&.*\\\\",           # & alignment với \\ newline → matrix-like
]

SYSTEM_PATTERNS = [
    r"\\begin\{cases\}",
    r"\\begin\{align",
    r"\\\\",              # multiple lines
    r"\\newline",
]

DEFINITION_PATTERNS = [
    r"\\begin\{(definition|theorem|lemma|proposition|corollary)",
    r"\\forall",
    r"\\exists",
    r":=",                # definition operator
    r"\\triangleq",
    r"\\equiv",
]

# ---------------------------------------------------------------------------
# Display text patterns (LaTeX → plain text approximation)
# ---------------------------------------------------------------------------

LATEX_TO_PLAIN = [
    (re.compile(r"\\frac\{([^}]+)\}\{([^}]+)\}"), r"(\1)/(\2)"),
    (re.compile(r"\\sqrt\{([^}]+)\}"),             r"sqrt(\1)"),
    (re.compile(r"\\sum"),                          "Σ"),
    (re.compile(r"\\prod"),                         "Π"),
    (re.compile(r"\\int"),                          "∫"),
    (re.compile(r"\\infty"),                        "∞"),
    (re.compile(r"\\alpha"),                        "α"),
    (re.compile(r"\\beta"),                         "β"),
    (re.compile(r"\\gamma"),                        "γ"),
    (re.compile(r"\\delta"),                        "δ"),
    (re.compile(r"\\theta"),                        "θ"),
    (re.compile(r"\\lambda"),                       "λ"),
    (re.compile(r"\\mu"),                           "μ"),
    (re.compile(r"\\sigma"),                        "σ"),
    (re.compile(r"\\pi"),                           "π"),
    (re.compile(r"\\leq"),                          "≤"),
    (re.compile(r"\\geq"),                          "≥"),
    (re.compile(r"\\neq"),                          "≠"),
    (re.compile(r"\\approx"),                       "≈"),
    (re.compile(r"\\times"),                        "×"),
    (re.compile(r"\\cdot"),                         "·"),
    (re.compile(r"\\left[\(\[\{]"),                 "("),
    (re.compile(r"\\right[\)\]\}]"),                ")"),
    (re.compile(r"\{([^}]*)\}"),                    r"\1"),   # bỏ {} wrapper
    (re.compile(r"\\[a-zA-Z]+"),                    ""),      # bỏ commands còn lại
    (re.compile(r"\s+"),                            " "),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_formulas(formulas: list[Formula]) -> list[Formula]:
    """
    Enrich list[Formula] đã extract từ loader.

    Args:
        formulas: list[Formula] từ pdf_loader / docx_loader

    Returns:
        list[Formula] đã enrich
    """
    if not formulas:
        return []

    enriched = [_enrich_formula(f, idx + 1) for idx, f in enumerate(formulas)]
    logger.debug("Parsed %d formulas from loader", len(enriched))
    return enriched


def extract_from_text(text: str, start_index: int = 0) -> list[Formula]:
    """
    Extract Formula objects từ raw text (sections content).
    Xử lý LaTeX patterns: $...$, $$...$$, \\begin{equation}, v.v.

    Block regions ($$...$$, environments) được mask trước khi extract inline
    để tránh RE_LATEX_INLINE match nhầm bên trong block expressions.

    Args:
        text:        Raw text content từ section
        start_index: Index bắt đầu đánh số (để merge với formulas từ nguồn khác)

    Returns:
        list[Formula] extracted và enriched
    """
    if not text:
        return []

    results = []
    seen    = set()

    # Priority order: specific environments trước, inline sau
    # Definition giữ nguyên full wrapper trong raw (env name preserved)
    extractors = [
        (_extract_definition_latex, "definition"),
        (_extract_matrix_latex,     "matrix"),
        (_extract_cases_latex,      "system"),
        (_extract_align_latex,      "system"),
        (_extract_equation_latex,   "equation"),
        (_extract_block_latex,      "block"),
    ]

    for extractor, default_type in extractors:
        for raw, detected_type in extractor(text):
            norm = _normalize_raw(raw)
            if norm in seen:
                continue
            seen.add(norm)
            results.append(Formula(
                raw           = raw.strip(),
                formula_type  = detected_type or default_type,
                source_format = "latex",
            ))

    # Mask tất cả block regions trước khi extract inline
    # → tránh $$ bị match bởi RE_LATEX_INLINE
    masked_text = RE_BLOCK_REGIONS.sub(" __BLOCK__ ", text)
    for raw, detected_type in _extract_inline_latex(masked_text):
        norm = _normalize_raw(raw)
        if norm in seen:
            continue
        seen.add(norm)
        results.append(Formula(
            raw           = raw.strip(),
            formula_type  = "inline",
            source_format = "latex",
        ))

    results = [Formula(raw=f.raw, formula_type=f.formula_type,
                       index=start_index + i, source_format=f.source_format,
                       display_text=_generate_display_text(f.raw))
               for i, f in enumerate(results)]

    logger.debug(f"Extracted {len(results)} formulas from text")
    return results


def extract_from_html(html: str, start_index: int = 0) -> list[Formula]:
    """
    Extract Formula objects từ HTML (MathML, MathJax).
    Dùng cho html_loader output.

    Args:
        html:        HTML string từ html_loader
        start_index: Index bắt đầu đánh số

    Returns:
        list[Formula] extracted và enriched
    """
    if not html:
        return []

    results = []
    seen    = set()

    # MathJax display mode trước (block), rồi inline, rồi MathML
    for pattern, default_type in [
        (RE_MATHJAX_DISPLAY, "block"),
        (RE_MATHJAX_INLINE,  "inline"),
        (RE_MATHML,          "block"),
    ]:
        for m in pattern.finditer(html):
            raw  = m.group(1).strip()
            norm = _normalize_raw(raw)
            if norm in seen:
                continue
            seen.add(norm)

            fmt          = "mathml" if pattern == RE_MATHML else "latex"
            formula_type = _classify_type(raw, default_type, fmt)

            results.append(Formula(
                raw           = raw,
                formula_type  = formula_type,
                index         = start_index + len(results),
                source_format = fmt,
                display_text  = _generate_display_text(raw) if fmt == "latex" else raw[:80],
            ))

    logger.debug(f"Extracted {len(results)} formulas from HTML")
    return results


def merge_formulas(
    *formula_lists: list[Formula],
    deduplicate: bool = True,
) -> list[Formula]:
    """
    Merge nhiều list[Formula] từ các nguồn khác nhau.
    Giữ cả LaTeX và MathML — deduplicate theo normalized raw.

    Args:
        *formula_lists: Các list Formula từ loader, text extraction, HTML extraction
        deduplicate:    True = bỏ duplicate theo normalized raw

    Returns:
        list[Formula] đã merge và re-indexed
    """
    merged = []
    seen   = set()

    for formula_list in formula_lists:
        for formula in formula_list:
            if deduplicate:
                norm = _normalize_raw(formula.raw)
                if norm in seen:
                    logger.debug(f"Deduplicated formula: {formula.raw[:40]}...")
                    continue
                seen.add(norm)
            merged.append(formula)

    # Re-index sau merge
    result = []
    for idx, f in enumerate(merged):
        result.append(Formula(
            raw           = f.raw,
            formula_type  = f.formula_type,
            index         = idx,
            source_format = getattr(f, "source_format", "unknown"),
            display_text  = getattr(f, "display_text", None) or _generate_display_text(f.raw),
        ))

    logger.debug(f"Merged {len(result)} formulas (deduplicate={deduplicate})")
    return result


# ---------------------------------------------------------------------------
# Core enrichment
# ---------------------------------------------------------------------------

def _enrich_formula(formula: Formula, index: int) -> Formula:
    """Enrich một Formula từ loader: classify type, add display_text."""
    raw           = formula.raw.strip()
    source_format = getattr(formula, "source_format", None) or _detect_format(raw)
    formula_type  = _classify_type(raw, formula.formula_type, source_format)
    display_text  = _generate_display_text(raw) if source_format == "latex" else raw[:80]

    return Formula(
        raw           = raw,
        formula_type  = formula_type,
        index         = index,
        source_format = source_format,
        display_text  = display_text,
    )


# ---------------------------------------------------------------------------
# LaTeX extractors
# ---------------------------------------------------------------------------

def _extract_inline_latex(text: str):
    for m in RE_LATEX_INLINE.finditer(text):
        yield m.group(1), "inline"

def _extract_block_latex(text: str):
    for m in RE_LATEX_BLOCK.finditer(text):
        yield m.group(1), "block"

def _extract_equation_latex(text: str):
    for m in RE_LATEX_EQUATION.finditer(text):
        yield m.group(1), "equation"

def _extract_align_latex(text: str):
    for m in RE_LATEX_ALIGN.finditer(text):
        yield m.group(1), "system"

def _extract_matrix_latex(text: str):
    for m in RE_LATEX_MATRIX.finditer(text):
        yield m.group(1), "matrix"

def _extract_cases_latex(text: str):
    for m in RE_LATEX_CASES.finditer(text):
        yield m.group(1), "system"

def _extract_definition_latex(text: str):
    """
    Extract definition environments, giữ nguyên full wrapper trong raw.
    raw = "\\begin{theorem}...\\end{theorem}" (không strip content)
    → downstream biết được env type từ raw string
    """
    for m in RE_LATEX_DEFINITION.finditer(text):
        yield m.group(1), "definition"   # group(1) = full \begin{...}...\end{...}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify_type(raw: str, current_type: str, source_format: str) -> str:
    """
    Classify formula_type dựa trên content patterns.

    Priority: definition > matrix > system > equation > block > inline

    Args:
        raw:          Raw formula string
        current_type: Type hiện tại từ loader hoặc extraction
        source_format: "latex" | "mathml" | "unknown"

    Returns:
        "inline" | "block" | "equation" | "matrix" | "system" | "definition"
    """
    # MathML — classify dựa trên display attribute
    if source_format == "mathml":
        if 'display="block"' in raw or "display='block'" in raw:
            return "block"
        return "inline"

    # LaTeX — content-based classification
    if any(re.search(p, raw) for p in DEFINITION_PATTERNS):
        return "definition"

    if any(re.search(p, raw) for p in MATRIX_PATTERNS):
        return "matrix"

    if any(re.search(p, raw) for p in SYSTEM_PATTERNS):
        return "system"

    # Giữ nguyên nếu đã được classify bởi extractor
    if current_type in ("equation", "block", "inline", "matrix", "system", "definition"):
        return current_type

    return "inline"


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _detect_format(raw: str) -> str:
    """
    Detect source format từ raw string.

    Returns:
        "latex" | "mathml" | "unknown"
    """
    if "<math" in raw or "<mrow" in raw or "<mi" in raw:
        return "mathml"
    if "\\" in raw or "$" in raw:
        return "latex"
    return "unknown"


# ---------------------------------------------------------------------------
# Display text generation
# ---------------------------------------------------------------------------

def _generate_display_text(raw: str) -> str:
    """
    Tạo plain-text approximation từ LaTeX cho RAG indexing.
    Không cần perfect — chỉ cần searchable.

    Example:
        "\\frac{1}{2} \\alpha + \\beta"  →  "(1)/(2) α + β"
    """
    if not raw:
        return ""

    text = raw
    for pattern, replacement in LATEX_TO_PLAIN:
        text = pattern.sub(replacement, text)

    return text.strip()[:200]   # truncate cho RAG


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def _normalize_raw(raw: str) -> str:
    """
    Normalize raw formula để so sánh duplicate.
    Bỏ whitespace, lowercase, bỏ wrapper characters.

    Example:
        "  \\frac{1}{2}  "  →  "\\frac{1}{2}"
        "$x + y$"           →  "x+y"
    """
    norm = raw.strip()
    norm = re.sub(r"\s+", "", norm)     # bỏ toàn bộ whitespace
    norm = norm.strip("$")             # bỏ $ wrapper
    norm = norm.lower()
    return norm


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    # --- Test A: extract từ raw text ---
    sample_text = r"""
    The loss function is defined as $\mathcal{L} = -\sum_{i} y_i \log(\hat{y}_i)$.

    The attention score is computed as:
    $$\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V$$

    \begin{equation}
        \alpha_t = \frac{\exp(e_{t})}{\sum_{k=1}^{T} \exp(e_{k})}
    \end{equation}

    \begin{align}
        x &= r\cos\theta \\
        y &= r\sin\theta
    \end{align}

    \begin{pmatrix}
        a & b \\
        c & d
    \end{pmatrix}

    \begin{cases}
        2x + y = 5 \\
        x - y  = 1
    \end{cases}

    \begin{definition}
        A function $f$ is \textit{convex} if $f(\lambda x + (1-\lambda)y) \leq \lambda f(x) + (1-\lambda)f(y)$.
    \end{definition}
    """

    print("=" * 60)
    print("TEST A: extract_from_text")
    print("=" * 60)
    formulas_text = extract_from_text(sample_text)
    for f in formulas_text:
        print(f"[{f.index}] type={f.formula_type:<12} fmt={getattr(f,'source_format','?'):<8} | {f.raw[:60].strip()}")
        if getattr(f, "display_text", None):
            print(f"      display: {f.display_text[:60]}")
    print()

    # --- Test A2: verify inline không match bên trong $$ ---
    print("=" * 60)
    print("TEST A2: inline mask — $$ không bị match bởi inline regex")
    print("=" * 60)
    tricky_text = r"Block: $$\frac{a}{b}$$ and inline: $x + y$"
    tricky_formulas = extract_from_text(tricky_text)
    block_count  = sum(1 for f in tricky_formulas if f.formula_type == "block")
    inline_count = sum(1 for f in tricky_formulas if f.formula_type == "inline")
    print(f"Block formulas  : {block_count} (expect 1)")
    print(f"Inline formulas : {inline_count} (expect 1)")
    assert block_count  == 1, f"Expected 1 block, got {block_count}"
    assert inline_count == 1, f"Expected 1 inline, got {inline_count}"
    print("✓ Block masking working correctly")
    print()

    # --- Test A3: definition giữ nguyên full wrapper ---
    print("=" * 60)
    print("TEST A3: definition raw giữ nguyên \\begin{}...\\end{}")
    print("=" * 60)
    def_text = r"""
    \begin{theorem}
        For all $x \in \mathbb{R}$, $x^2 \geq 0$.
    \end{theorem}
    """
    def_formulas = extract_from_text(def_text)
    def_formula  = next((f for f in def_formulas if f.formula_type == "definition"), None)
    assert def_formula is not None, "Should extract definition"
    assert r"\begin{theorem}" in def_formula.raw, "Should keep \\begin{theorem} in raw"
    assert r"\end{theorem}"   in def_formula.raw, "Should keep \\end{theorem} in raw"
    print(f"raw: {def_formula.raw[:80].strip()}")
    print("✓ Definition wrapper preserved in raw")
    print()

    # --- Test B: extract từ HTML ---
    sample_html = """
    <p>The formula <script type="math/tex">E = mc^2</script> is well known.</p>
    <p>The full equation is:
    <script type="math/tex; mode=display">\\int_0^\\infty e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}</script>
    </p>
    <math display="block"><mrow><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo></mrow></math>
    """

    print("=" * 60)
    print("TEST B: extract_from_html")
    print("=" * 60)
    formulas_html = extract_from_html(sample_html, start_index=len(formulas_text))
    for f in formulas_html:
        print(f"[{f.index}] type={f.formula_type:<12} fmt={getattr(f,'source_format','?'):<8} | {f.raw[:60].strip()}")
    print()

    # --- Test C: merge + deduplicate ---
    print("=" * 60)
    print("TEST C: merge_formulas (deduplicate)")
    print("=" * 60)
    dup_text = extract_from_text(r"The score $E = mc^2$ appears again.")
    merged   = merge_formulas(formulas_text, formulas_html, dup_text)
    print(f"Text formulas   : {len(formulas_text)}")
    print(f"HTML formulas   : {len(formulas_html)}")
    print(f"Dup formulas    : {len(dup_text)}")
    print(f"After merge     : {len(merged)} (deduplicated)")
    print()

    # --- Test D: enrich từ loader Formula ---
    print("=" * 60)
    print("TEST D: parse_formulas (từ loader)")
    print("=" * 60)
    loader_formulas = [
        Formula(raw=r"\alpha + \beta = \gamma",             formula_type="inline"),
        Formula(raw=r"\begin{pmatrix}1&0\\0&1\end{pmatrix}", formula_type="block"),
        Formula(raw=r"x := \frac{a}{b}",                   formula_type="inline"),
    ]
    enriched = parse_formulas(loader_formulas)
    for f in enriched:
        print(f"[{f.index}] type={f.formula_type:<12} | {f.raw[:50]}")
    print("=" * 60)
