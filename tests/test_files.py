"""Reading every kind of file the user can send, and creating the ones the assistant sends back."""
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent.documents import render, safe_name
from agent.files import Attachment, UnsupportedFile, ingest, office_text, pdf_text

HAS_FFMPEG = bool(shutil.which("ffmpeg"))


def heard(audio: bytes, suffix: str) -> str:
    return f"transcript of {len(audio)} bytes ({suffix})"


class IngestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def ingest(self, data: bytes, mime: str, name: str) -> Attachment:
        return ingest(data, mime, name, self.dir, heard)

    def test_image(self):
        png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                            "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
        a = self.ingest(png, "image/png", "")
        self.assertEqual((a.kind, a.mime), ("image", "image/png"))
        self.assertEqual((self.dir / a.path).read_bytes(), png)

    def test_generated_docx_and_pdf_read_back(self):
        out = self.dir / "out"
        docx = render("Plan.docx", "# Weekly plan\n- **Monday**: LinkedIn post\n| Day | Network |\n|---|---|\n| Tue | X |", out)
        text = self.ingest(docx.read_bytes(), "", docx.name).text
        for word in ("Weekly plan", "Monday", "LinkedIn post", "Tue"):
            self.assertIn(word, text)
        pdf = render("Informe ñ.pdf", "# Informe\nCanción con acentos: áéíóú. **Negrita**\n1. Uno\n2. Dos", out)
        a = self.ingest(pdf.read_bytes(), "application/pdf", pdf.name)
        self.assertEqual(a.kind, "pdf")
        self.assertIn("Canción con acentos", a.text)

    def test_xlsx_and_pptx(self):
        xlsx = self.dir / "t.xlsx"
        with zipfile.ZipFile(xlsx, "w") as z:
            z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       "<si><t>Views</t></si><si><t>Reel A</t></si></sst>")
            z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       '<sheetData><row><c t="s"><v>0</v></c></row><row><c t="s"><v>1</v></c><c><v>1200</v></c></row></sheetData></worksheet>')
        self.assertIn("Reel A\t1200", office_text(xlsx, ".xlsx"))
        pptx = self.dir / "s.pptx"
        with zipfile.ZipFile(pptx, "w") as z:
            for n, words in ((2, "Second"), (1, "First")):
                z.writestr(f"ppt/slides/slide{n}.xml", '<p:sld xmlns:p="p" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                           f"<a:t>{words} slide</a:t></p:sld>")
        text = self.ingest(pptx.read_bytes(), "", "s.pptx").text
        self.assertLess(text.index("First"), text.index("Second"))

    def test_text_and_audio(self):
        self.assertEqual(self.ingest(b"a,b\n1,2", "text/csv", "d.csv").text, "a,b\n1,2")
        a = self.ingest(b"OGG", "audio/ogg; codecs=opus", "")
        self.assertEqual((a.kind, a.text), ("audio", "transcript of 3 bytes (.ogg)"))

    def test_xml_bomb_is_refused(self):
        bomb = self.dir / "bomb.docx"
        entities = '<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        with zipfile.ZipFile(bomb, "w") as z:
            z.writestr("word/document.xml", entities + "<w:document xmlns:w='w'>&b;</w:document>")
        with self.assertRaises(UnsupportedFile):
            self.ingest(bomb.read_bytes(), "", "bomb.docx")

    def test_unknown_and_too_big(self):
        with self.assertRaises(UnsupportedFile):
            self.ingest(b"\x00\x01", "application/x-thing", "x.bin")
        with self.assertRaises(UnsupportedFile):
            self.ingest(b"0" * (26 * 1024 * 1024), "text/plain", "big.txt")

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_video_is_transcribed(self):
        video = self.dir / "v.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-shortest", str(video)], check=True)
        a = self.ingest(video.read_bytes(), "video/mp4", "")
        self.assertEqual(a.kind, "video")
        self.assertIn("(.wav)", a.text)

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_big_screenshot_is_shrunk(self):
        big = self.dir / "big.png"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "nullsrc=s=4000x4000,geq=random(1)*255:128:128",
                        "-frames:v", "1", str(big)], check=True)
        self.assertGreater(big.stat().st_size, 3_500_000)
        a = self.ingest(big.read_bytes(), "image/png", "big.png")
        self.assertEqual(a.kind, "image")
        self.assertLess((self.dir / a.path).stat().st_size, 3_500_000)


class DocumentsTest(unittest.TestCase):
    def test_names_are_safe(self):
        self.assertEqual(safe_name("../../etc/passwd"), "passwd.md")
        self.assertEqual(safe_name("plan semanal.PDF"), "plan semanal.pdf")
        self.assertEqual(safe_name("x.exe"), "x.md")

    def test_same_name_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = render("a.csv", "x", Path(tmp))
            second = render("a.csv", "y", Path(tmp))
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(), "x\n")

    def test_pdf_text_of_garbage_is_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(b"not a pdf")
            f.flush()
            self.assertEqual(pdf_text(Path(f.name)), "")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_FFMPEG and shutil.which("whisper-cli"), "needs ffmpeg and whisper.cpp")
class RealTranscriptionTest(unittest.TestCase):
    """Runs the real Transcriber on a .wav input (the path videos take)."""

    def test_wav_input(self):
        from agent.config import load_config
        from agent.transcribe import Transcriber

        config = load_config(env_file=Path("/nonexistent/.env"))
        if not config.whisper_model.exists():
            self.skipTest("whisper model not downloaded")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                            "-ar", "16000", "-ac", "1", str(wav)], check=True)
            self.assertIsInstance(Transcriber(config)(wav.read_bytes(), ".wav"), str)
