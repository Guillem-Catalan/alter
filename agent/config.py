"""Settings read from the environment, with an optional `.env` file at the repo root."""
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Alter"


def default_data_dir() -> Path:
    """Per-user app folder, outside the repo, so personal data can never be committed by accident."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME.lower()


def private_dir(path: Path) -> Path:
    """Create a folder only its owner can read (personal data: chats, voice notes, files)."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def inside_git_repo(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in [path, *path.parents])


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines (an optional leading `export` is allowed) without overriding the real environment."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "azure": "claudio-claude-sonnet-4-6",
}


@dataclass
class Config:
    bot_name: str
    language: str
    data_dir: Path
    skills_dirs: list[Path]
    humanize: bool

    llm_provider: str
    llm_model: str
    llm_effort: str
    llm_fallbacks: bool
    llm_fallback_provider: str
    claude_bin: str
    azure_endpoint: str
    azure_api_key: str
    azure_deployment: str
    openai_base_url: str
    openai_api_key: str

    whisper_bin: str
    whisper_model: Path
    whisper_language: str
    ffmpeg_bin: str

    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_app_secret: str
    whatsapp_verify_token: str
    whatsapp_allowed_numbers: set[str] = field(default_factory=set)
    whatsapp_api_version: str = "v25.0"
    whatsapp_graph_url: str = "https://graph.facebook.com"
    port: int = 8080

    slack_bot_token: str = ""
    slack_app_token: str = ""


def digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


def load_config(env_file: Path | None = None) -> Config:
    load_dotenv(env_file or ROOT / ".env")

    provider = _env("LLM_PROVIDER", "anthropic").lower()
    model = _env("LLM_MODEL")
    if not model and provider == "azure":
        model = _env("AZURE_CLAUDE_DEPLOYMENT")
    model = model or DEFAULT_MODELS.get(provider, "")

    data_dir = Path(_env("DATA_DIR") or default_data_dir()).expanduser()
    skills_dirs = [ROOT / "skills"] + [
        Path(p).expanduser() for p in _env("EXTRA_SKILLS_DIRS").split(os.pathsep) if p
    ]
    whisper_model = Path(
        _env("WHISPER_MODEL") or data_dir / "models" / "ggml-large-v3-turbo-q5_0.bin"
    ).expanduser()

    return Config(
        bot_name=_env("BOT_NAME", APP_NAME),
        language=_env("BOT_LANGUAGE", "en").lower()[:2],
        data_dir=data_dir,
        skills_dirs=skills_dirs,
        humanize=_flag("HUMANIZE", True),
        llm_provider=provider,
        llm_model=model,
        llm_effort=_env("LLM_EFFORT"),
        llm_fallbacks=_flag("LLM_FALLBACKS", True),
        llm_fallback_provider=_env("LLM_FALLBACK_PROVIDER").lower(),
        claude_bin=_env("CLAUDE_BIN", "claude"),
        azure_endpoint=_env("AZURE_CLAUDE_ENDPOINT").rstrip("/"),
        azure_api_key=_env("AZURE_CLAUDE_API_KEY"),
        azure_deployment=_env("AZURE_CLAUDE_DEPLOYMENT") or DEFAULT_MODELS["azure"],
        openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        openai_api_key=_env("OPENAI_API_KEY"),
        whisper_bin=_env("WHISPER_BIN", "whisper-cli"),
        whisper_model=whisper_model,
        whisper_language=_env("WHISPER_LANGUAGE", "auto"),
        ffmpeg_bin=_env("FFMPEG_BIN", "ffmpeg"),
        whatsapp_token=_env("WHATSAPP_TOKEN"),
        whatsapp_phone_number_id=_env("WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_app_secret=_env("WHATSAPP_APP_SECRET"),
        whatsapp_verify_token=_env("WHATSAPP_VERIFY_TOKEN"),
        whatsapp_allowed_numbers={digits(n) for n in _env("WHATSAPP_ALLOWED_NUMBERS").split(",") if digits(n)},
        whatsapp_api_version=_env("WHATSAPP_API_VERSION", "v25.0"),
        whatsapp_graph_url=_env("WHATSAPP_GRAPH_URL", "https://graph.facebook.com").rstrip("/"),
        port=int(_env("PORT", "8080")),
        slack_bot_token=_env("SLACK_BOT_TOKEN"),
        slack_app_token=_env("SLACK_APP_TOKEN"),
    )
