"""Voice notes to text with whisper.cpp, entirely on this machine."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import Config


class TranscriptionError(RuntimeError):
    pass


class Transcriber:
    def __init__(self, config: Config):
        self.whisper = config.whisper_bin
        self.model = config.whisper_model
        self.language = config.whisper_language
        self.ffmpeg = config.ffmpeg_bin

    def problems(self) -> list[str]:
        """What is missing for transcription to work (empty list when ready)."""
        issues = []
        if not shutil.which(self.ffmpeg):
            issues.append(f"ffmpeg not found ({self.ffmpeg}). macOS: brew install ffmpeg")
        if not shutil.which(self.whisper):
            issues.append(f"whisper.cpp not found ({self.whisper}). macOS: brew install whisper.cpp")
        if not self.model.exists():
            issues.append(f"Whisper model missing at {self.model}. Run: scripts/download-whisper-model.sh")
        return issues

    def __call__(self, audio: bytes, suffix: str = ".ogg") -> str:
        issues = self.problems()
        if issues:
            raise TranscriptionError("; ".join(issues))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / f"input{suffix}"
            wav = Path(tmp) / "converted.wav"  # never the same name as the input (which may be .wav)
            source.write_bytes(audio)
            # whisper.cpp expects 16 kHz mono PCM.
            self._run([self.ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(source),
                       "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
            out = self._run([self.whisper, "-m", str(self.model), "-f", str(wav),
                             "-l", self.language, "-nt", "-np"])
        return " ".join(line.strip() for line in out.splitlines() if line.strip())

    @staticmethod
    def _run(cmd: list[str]) -> str:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        except subprocess.TimeoutExpired as e:
            raise TranscriptionError(f"{Path(cmd[0]).name} took too long") from e
        if result.returncode != 0:
            raise TranscriptionError(f"{Path(cmd[0]).name} failed: {result.stderr.strip()[-300:]}")
        return result.stdout
