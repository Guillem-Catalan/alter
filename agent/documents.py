"""Files the assistant creates: the model calls the create_file tool with Markdown and this turns it into the file.

Supported: .pdf (fpdf2), .docx (python-docx), .md, .txt, .csv.
"""
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SUPPORTED = (".pdf", ".docx", ".md", ".txt", ".csv")
# Font families with real bold/italic files, first one found wins; a wide-coverage font fills missing glyphs.
FONT_FAMILIES = [
    {"": "/System/Library/Fonts/Supplemental/Arial.ttf", "B": "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "I": "/System/Library/Fonts/Supplemental/Arial Italic.ttf", "BI": "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"},
    {"": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "B": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "I": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "BI": "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"},
]
FALLBACK_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def safe_name(name: str) -> str:
    name = Path(name.strip()).name
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^\w .-]", "", stem, flags=re.UNICODE).strip(" .") or "document"
    if ext not in SUPPORTED:
        ext = ".md"
    return f"{stem[:80]}{ext}"


def _blocks(markdown: str) -> Iterator[tuple[str, Any]]:
    """Yield (kind, payload) for a small Markdown subset: headings, bullets, numbered items, tables, paragraphs."""
    lines = markdown.strip("\n").splitlines()
    i = 0
    paragraph: list[str] = []

    def flush() -> Iterator[tuple[str, Any]]:
        if paragraph:
            yield ("p", " ".join(s.strip() for s in paragraph))
            paragraph.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            yield from flush()
        elif m := re.match(r"^(#{1,4})\s+(.*)", stripped):
            yield from flush()
            yield (f"h{len(m.group(1))}", m.group(2))
        elif stripped.startswith("|"):
            yield from flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            width = max(len(r) for r in rows) if rows else 0
            yield ("table", [r + [""] * (width - len(r)) for r in rows])
            continue
        elif m := re.match(r"^[-*•]\s+(.*)", stripped):
            yield from flush()
            yield ("bullet", m.group(1))
        elif m := re.match(r"^(\d+)[.)]\s+(.*)", stripped):
            yield from flush()
            yield ("number", f"{m.group(1)}. {m.group(2)}")
        elif re.fullmatch(r"-{3,}|\*{3,}", stripped):
            yield from flush()
            yield ("rule", "")
        else:
            paragraph.append(stripped)
        i += 1
    yield from flush()


def _plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|`(.+?)`", lambda m: next(g for g in m.groups() if g), text)


def render_pdf(markdown: str, path: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=18)
    font = next((f for f in FONT_FAMILIES if Path(f[""]).exists()), None)
    if font:
        for style, file in font.items():
            pdf.add_font("Body", style, file if Path(file).exists() else font[""])
        fallback = next((f for f in FALLBACK_FONTS if Path(f).exists()), None)
        if fallback:
            pdf.add_font("Fallback", "", fallback)
            pdf.set_fallback_fonts(["Fallback"])
        family = "Body"
    else:
        family = "Helvetica"  # core font: Latin-1 only

    def text(s: str) -> str:
        return s if font else s.encode("latin-1", "replace").decode("latin-1")

    pdf.add_page()
    sizes = {"h1": 20, "h2": 16, "h3": 13, "h4": 12}
    for kind, payload in _blocks(markdown):
        if kind in sizes:
            pdf.ln(3)
            pdf.set_font(family, "B", sizes[kind])
            pdf.multi_cell(0, sizes[kind] * 0.5, text(_plain(payload)), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif kind == "table":
            pdf.set_font(family, "", 9)
            # Wider columns for longer content, but never too narrow to read.
            longest = [max(len(_plain(row[c])) for row in payload) for c in range(len(payload[0]))]
            widths = tuple(max(n, 8) for n in longest)
            with pdf.table(first_row_as_headings=True, line_height=5, col_widths=widths, text_align="LEFT") as table:
                for row in payload:
                    r = table.row()
                    for cell in row:
                        r.cell(text(_plain(cell)))
            pdf.ln(2)
        elif kind == "rule":
            y = pdf.get_y() + 2
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(5)
        else:
            pdf.set_font(family, "", 11)
            # fpdf2 markdown: **bold**, __italic__
            body = text(re.sub(r"(?<![*\w])\*(?!\*)(.+?)(?<!\*)\*(?![*\w])", r"__\1__", payload))
            if kind == "bullet":
                body = "•  " + body if font else "-  " + body
                pdf.set_x(pdf.l_margin + 4)
            elif kind == "number":
                pdf.set_x(pdf.l_margin + 4)
            pdf.multi_cell(0, 6, body, markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.5)
    pdf.output(str(path))


def _docx_runs(paragraph, text: str) -> None:
    for part in re.split(r"(\*\*.+?\*\*|__.+?__|\*.+?\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("__") and part.endswith("__") or (part.startswith("*") and part.endswith("*") and len(part) > 2):
            paragraph.add_run(part.strip("_*")).italic = True
        else:
            paragraph.add_run(part)


def render_docx(markdown: str, path: Path) -> None:
    import docx

    document = docx.Document()
    for kind, payload in _blocks(markdown):
        if kind.startswith("h"):
            document.add_heading(_plain(payload), level=int(kind[1]))
        elif kind == "table":
            table = document.add_table(rows=0, cols=len(payload[0]) if payload else 1)
            table.style = "Table Grid"
            for n, row in enumerate(payload):
                cells = table.add_row().cells
                for cell, value in zip(cells, row):
                    cell.text = ""
                    _docx_runs(cell.paragraphs[0], f"**{value}**" if n == 0 and value else value)
        elif kind == "bullet":
            _docx_runs(document.add_paragraph(style="List Bullet"), payload)
        elif kind == "number":
            _docx_runs(document.add_paragraph(style="List Number"), payload.split(". ", 1)[-1])
        elif kind == "rule":
            document.add_paragraph("")
        else:
            _docx_runs(document.add_paragraph(), payload)
    document.save(str(path))


def render(name: str, content: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_name(name)
    path = out_dir / filename
    n = 2
    while path.exists():
        path = out_dir / f"{Path(filename).stem}-{n}{Path(filename).suffix}"
        n += 1
    ext = path.suffix
    if ext == ".pdf":
        render_pdf(content, path)
    elif ext == ".docx":
        render_docx(content, path)
    else:
        path.write_text(content.strip() + "\n")
    return path
