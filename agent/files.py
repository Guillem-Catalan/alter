"""Incoming files: store a copy, work out what they are, and get the text out of the ones the model can't read directly.

Images and PDFs go to the model as they are. Office files and text files become text here.
Voice notes and videos are transcribed locally.
"""
import mimetypes
import re
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
MAX_XML_BYTES = 50 * 1024 * 1024  # guards against zip bombs inside Office files
MAX_IMAGE_BYTES = 3_500_000  # Claude accepts up to 5 MB per image once base64-encoded
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".xml", ".srt", ".vtt", ".yaml", ".yml", ".log"}
OFFICE_TYPES = {
    ".docx": "word/document.xml",
    ".pptx": "ppt/slides/",
    ".xlsx": "xl/sharedStrings.xml",
}


class UnsupportedFile(ValueError):
    pass


@dataclass
class Attachment:
    kind: str      # image | pdf | document | audio | video
    name: str      # original file name, shown to the model
    mime: str
    path: str      # relative to the user's folder
    text: str = ""  # extracted text or transcript

    def to_dict(self) -> dict:
        return asdict(self)


def guess_type(name: str, mime: str) -> tuple[str, str]:
    """Return (mime, extension) from WhatsApp's mime type and/or the file name."""
    mime = (mime or "").split(";")[0].strip().lower()
    ext = Path(name).suffix.lower()
    if not mime or mime == "application/octet-stream":
        mime = mimetypes.guess_type(name)[0] or mime or "application/octet-stream"
    if not ext:
        ext = mimetypes.guess_extension(mime) or ""
        ext = {".jpe": ".jpg", ".oga": ".ogg"}.get(ext, ext)
    return mime, ext


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS] + f"\n[... cut: the file has {len(text):,} characters, only the first {MAX_TEXT_CHARS:,} are included]"
    return text


def _read(z: zipfile.ZipFile, name: str) -> bytes:
    if z.getinfo(name).file_size > MAX_XML_BYTES:
        raise UnsupportedFile(f"{name} inside the file is too big")
    return z.read(name)


def office_text(path: Path, ext: str) -> str:
    """Plain text from .docx, .pptx and .xlsx: they are zipped XML."""
    with zipfile.ZipFile(path) as z:
        if ext == ".docx":
            root = ElementTree.fromstring(_read(z, "word/document.xml"))
            paragraphs = []
            for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                paragraphs.append("".join(t.text or "" for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")))
            return "\n".join(paragraphs)
        if ext == ".pptx":
            slides = sorted(
                (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.findall(r"\d+", n)[-1]),
            )
            out = []
            for i, name in enumerate(slides, 1):
                root = ElementTree.fromstring(_read(z, name))
                texts = [t.text or "" for t in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t")]
                out.append(f"--- Slide {i} ---\n" + "\n".join(t for t in texts if t.strip()))
            return "\n".join(out)
        if ext == ".xlsx":
            return _xlsx_text(z)
    return ""


def _xlsx_text(z: zipfile.ZipFile) -> str:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ElementTree.fromstring(_read(z, "xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(f"{ns}t")) for si in root.iter(f"{ns}si")]
    sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    out = []
    for name in sheets:
        root = ElementTree.fromstring(_read(z, name))
        rows = []
        for row in root.iter(f"{ns}row"):
            cells = []
            for c in row.iter(f"{ns}c"):
                value = c.find(f"{ns}v")
                inline = c.find(f"{ns}is")
                if c.get("t") == "s" and value is not None and value.text is not None:
                    cells.append(shared[int(value.text)] if int(value.text) < len(shared) else "")
                elif inline is not None:
                    cells.append("".join(t.text or "" for t in inline.iter(f"{ns}t")))
                else:
                    cells.append(value.text if value is not None and value.text else "")
            rows.append("\t".join(cells))
        out.append(f"--- {Path(name).stem} ---\n" + "\n".join(rows))
    return "\n".join(out)


def pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # damaged or encrypted PDFs still go to Claude as they are
        return ""


def shrink_image(path: Path, ffmpeg: str) -> Path:
    """Re-encode big images (screenshots sent as files) so they fit the model's per-image limit."""
    if path.stat().st_size <= MAX_IMAGE_BYTES:
        return path
    small = path.with_name(path.stem + "-small.jpg")
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                    "-vf", "scale='min(2000,iw)':-2", "-q:v", "4", str(small)], check=True, timeout=120)
    return small


def video_audio(path: Path, ffmpeg: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "audio.wav"
        result = subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                                 "-vn", "-ar", "16000", "-ac", "1", str(out)], capture_output=True, timeout=600)
        if result.returncode != 0 or not out.exists():
            return b""
        return out.read_bytes()


def ingest(data: bytes, mime: str, name: str, user_dir: Path, transcriber, ffmpeg: str = "ffmpeg") -> Attachment:
    """Save an incoming file in the user's folder and return what the model needs to know about it."""
    if len(data) > MAX_FILE_BYTES:
        raise UnsupportedFile(f"too big ({len(data) // 1_000_000} MB, limit {MAX_FILE_BYTES // 1_000_000} MB)")
    mime, ext = guess_type(name, mime)
    files = user_dir / "files"
    files.mkdir(parents=True, exist_ok=True)
    path = files / f"{uuid.uuid4().hex[:12]}{ext}"
    path.write_bytes(data)
    display = Path(name).name if name else path.name
    rel = lambda p: str(p.relative_to(user_dir))  # noqa: E731

    if mime in IMAGE_TYPES:
        return Attachment("image", display, mime, rel(shrink_image(path, ffmpeg)) if ffmpeg else rel(path))
    if mime == "image/jpg":
        return Attachment("image", display, "image/jpeg", rel(path))
    if mime.startswith("image/"):
        converted = path.with_suffix(".jpg")
        subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(path), str(converted)], check=True, timeout=120)
        return Attachment("image", display, "image/jpeg", rel(shrink_image(converted, ffmpeg)))
    if mime == "application/pdf" or ext == ".pdf":
        return Attachment("pdf", display, "application/pdf", rel(path), _truncate(pdf_text(path)))
    if ext in OFFICE_TYPES:
        try:
            text = office_text(path, ext)
        except (zipfile.BadZipFile, KeyError, ParseError, ValueError) as e:  # ValueError: defusedxml refusals
            raise UnsupportedFile(f"could not read {display}") from e
        return Attachment("document", display, mime, rel(path), _truncate(text))
    if mime.startswith("text/") or ext in TEXT_EXTENSIONS or mime in ("application/json", "application/xml"):
        return Attachment("document", display, mime, rel(path), _truncate(data.decode("utf-8", errors="replace")))
    if mime.startswith("audio/"):
        return Attachment("audio", display, mime, rel(path), _truncate(transcriber(data, ext or ".ogg")))
    if mime.startswith("video/"):
        audio = video_audio(path, ffmpeg)
        text = transcriber(audio, ".wav") if audio else ""
        return Attachment("video", display, mime, rel(path), _truncate(text) or "(no speech found in the video)")
    raise UnsupportedFile(f"{display} ({mime}) is not a file type I can read")
