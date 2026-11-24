"""The conversation logic shared by every channel (WhatsApp, Slack, terminal)."""
import base64
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .documents import FILE_RE, render
from .files import Attachment
from .llm import LLMError
from .skills import SkillLibrary
from .store import UserStore, user_lock

MAX_SKILL_ROUNDS = 2
# Images and PDFs from the last few messages that had files go to the model as they are;
# older ones are represented by their extracted text, to keep requests small.
INLINE_ATTACHMENT_TURNS = 3

TAG_RE = re.compile(r"\[(LEARN|PUBLISHED|SETUP|VOICE|SKILL):\s*(.+?)\]", re.DOTALL)
TAG_WITH_SPACE_RE = re.compile(r"[ \t]*" + TAG_RE.pattern, re.DOTALL)
CODE_BLOCK_RE = re.compile(r"```[a-zA-Z]*\n?(.*?)```", re.DOTALL)

TEXTS = {
    "en": {
        "welcome": (
            "Hi! I'm {bot}, your publishing assistant for X, LinkedIn, Instagram and YouTube.\n\n"
            "Send me ideas by text or voice note and I'll turn them into posts in *your* voice. "
            "I learn from every correction.\n\n"
            "To start, tell me in one message:\n"
            "1. Your name\n"
            "2. Your handles (X, LinkedIn, Instagram, YouTube)\n"
            "3. What you're working on\n"
            "4. Your vibe: casual, professional, funny..."
        ),
        "help": (
            "*{bot}*\n\n"
            "- Tell me (or send a voice note about) what you did or what's on your mind, and which network\n"
            "- Tell me which draft you posted and I'll remember it\n"
            "- Give me feedback and I'll learn your style\n"
            "- `preferences` = what I've learned about you\n"
            "- `reset preferences` = start over\n"
            "- `profile` = your profile\n"
            "- `skills` = what I know how to do\n"
            "- `help` = this message"
        ),
        "prefs": "*What I've learned about your style:*\n",
        "no_prefs": "I haven't learned any preferences yet. Give me feedback and I'll keep them.",
        "prefs_reset": "Preferences deleted. Starting from scratch.",
        "profile": "*Your profile:*\nName: {name}\nHandles: {handles}\nContext: {context}",
        "no_profile": "No profile yet. Tell me your name, your handles and what you're working on.",
        "skills": "*What I know how to do:*\n",
        "error": "Something went wrong talking to the model: {error}",
    },
    "es": {
        "welcome": (
            "¡Hola! Soy {bot}, tu asistente para publicar en X, LinkedIn, Instagram y YouTube.\n\n"
            "Mándame ideas por texto o por audio y las convierto en posts con *tu* voz. "
            "Aprendo de cada corrección.\n\n"
            "Para empezar, cuéntame en un mensaje:\n"
            "1. Tu nombre\n"
            "2. Tus cuentas (X, LinkedIn, Instagram, YouTube)\n"
            "3. En qué estás trabajando\n"
            "4. Tu estilo: casual, profesional, con humor..."
        ),
        "help": (
            "*{bot}*\n\n"
            "- Cuéntame (o mándame un audio) qué has hecho o qué tienes en la cabeza, y para qué red\n"
            "- Dime qué borrador publicaste y lo recuerdo\n"
            "- Dame feedback y aprendo tu estilo\n"
            "- `preferencias` = lo que he aprendido de ti\n"
            "- `reset preferencias` = empezar de cero\n"
            "- `perfil` = tu perfil\n"
            "- `skills` = lo que sé hacer\n"
            "- `ayuda` = este mensaje"
        ),
        "prefs": "*Lo que he aprendido de tu estilo:*\n",
        "no_prefs": "Aún no he aprendido preferencias tuyas. Dame feedback y las iré guardando.",
        "prefs_reset": "Preferencias borradas. Empezamos de cero.",
        "profile": "*Tu perfil:*\nNombre: {name}\nCuentas: {handles}\nContexto: {context}",
        "no_profile": "Aún no tienes perfil. Dime tu nombre, tus cuentas y en qué trabajas.",
        "skills": "*Lo que sé hacer:*\n",
        "error": "Algo ha fallado al hablar con el modelo: {error}",
    },
}

COMMANDS = {
    "help": {"help", "ayuda", "?", "/help", "/start"},
    "prefs": {"preferences", "my preferences", "preferencias", "mis preferencias"},
    "reset_prefs": {"reset preferences", "reset preferencias", "borrar preferencias"},
    "profile": {"profile", "my profile", "perfil", "mi perfil"},
    "skills": {"skills", "/skills"},
}

INSTRUCTIONS = """You are {bot}, a social media publishing assistant for X, LinkedIn, Instagram and YouTube. \
You help one person turn raw ideas (often transcribed voice notes, so expect filler words and typos) \
into posts in THEIR voice, not yours. You are a creative partner in a chat, not a post factory.

YOUR JOB
- Work out which network the post is for. If it is unclear and it matters, ask once.
- Give 2-3 variations, each one alone in its own ``` code block with nothing else inside, so it can be copied as is.
- After the drafts, say in one line which one you would post, and ask which they will use.
- Never invent facts, numbers, clients or results. If a number is needed and unknown, ask.
- You never publish anything yourself: the user posts it.
- Chat in the user's language. Write posts in the language the user posts in (ask if unsure).

SKILLS
You have skills: detailed playbooks for specific jobs. Before writing anything a skill covers \
(a LinkedIn post, a reel script, a caption, a carousel, an X thread, a YouTube script or title...), load it: reply with ONLY \
the tag [SKILL: name] (several tags if needed) and nothing else. Its instructions will then be added \
and you answer the user. Skills already loaded are shown below; do not ask for them again.
Available skills:
{skills}

The skills were written for an agent with a file system. Here you are in a chat app: you cannot run \
scripts, read or write files, or browse the web. Adapt them:
- "Read voice.md" or a voice profile -> use <voice_pack>, <user_profile> and the learned preferences below.
- Logging to log.md or "reply yes to log it" -> when the user says they posted it, use [PUBLISHED: ...].
- Scripts (humanize.py, detect.py, hookscore.py...) -> apply their method yourself; their data files \
(JSON) are included with the skill. Drafts in code blocks are also cleaned of em dashes, invisible \
characters and stock AI words automatically after you answer.
- A skill that mentions another one (/li-human) -> load it with [SKILL: li-human] if you need it.
- plan.md, swipe files, analytics exports, transcripts -> not available unless the user pastes them in the chat; ask for them.

MEMORY TAGS (stripped before the user sees your message; use them freely)
- Style feedback ("too formal", "I like the second one", "never use emojis"): [LEARN: short preference]
- The user says they posted something: [PUBLISHED: network | exact text]  (network = x, linkedin, instagram or youtube)
- The user shares their own past posts as style examples: [VOICE: the post text]
- New user answering the welcome message: [SETUP: name | handles | one-paragraph context]
Learned preferences override every default here. When two conflict, the newest wins.

{files}

FORMATTING
{formatting}"""

FILES = """FILES
- The user can send photos, screenshots, PDFs, Word/Excel/PowerPoint and text files, voice notes and videos. \
They arrive with their message: images and PDFs as they are, the rest as extracted text or a transcript. \
Use them (a screenshot of analytics, a post to rewrite, a script, a recording to cut into Shorts...).
- You can create files. When a document is the best way to deliver something (a content plan, a report, \
a script, a calendar) or the user asks for a PDF, Word or CSV, write it like this, with Markdown inside \
(# headings, - lists, **bold**, | tables |):
[FILE: content-plan.pdf]
...
[/FILE]
Supported: .pdf .docx .md .txt .csv. No emojis inside files (PDF fonts cannot draw them). \
The file is sent after your message; say in one line what it is. \
Drafts meant to be copied and posted still go in code blocks, not in files."""

FORMATTING = {
    "whatsapp": "This is WhatsApp: *bold*, _italic_, no markdown headers, no tables, no ** or [links](). "
    "Keep chat text short. Each code block is sent as its own message so it can be copied alone.",
    "slack": "This is Slack: *bold*, _italic_, no markdown headers or tables.",
    "terminal": "This is a terminal: plain text, code blocks for drafts.",
}


@dataclass
class Reply:
    text: str
    files: list[Path] = field(default_factory=list)


class Agent:
    def __init__(self, llm, library: SkillLibrary, data_dir: Path, bot_name: str, language: str, humanizer=None):
        self.llm = llm
        self.library = library
        self.data_dir = data_dir
        self.bot_name = bot_name
        self.texts = TEXTS.get(language, TEXTS["en"])
        self.humanizer = humanizer

    def t(self, key: str, **kwargs) -> str:
        return self.texts[key].format(bot=self.bot_name, **kwargs)

    def handle(self, uid: str, text: str, channel: str = "terminal",
               attachments: list[Attachment] | None = None) -> Reply:
        """Answer one message from one user. Messages from the same user are processed in order."""
        text = text.strip()
        attachments = attachments or []
        if not text and not attachments:
            return Reply("")
        with user_lock(uid):
            store = UserStore(self.data_dir, uid)
            if not attachments:
                command = self._command(store, text)
                if command is not None:
                    return Reply(command)
            if not store.profile().get("setup_done") and not store.has_started():
                store.save_history([])
                return Reply(self.t("welcome"))
            return self._converse(store, text, channel, attachments)

    def _command(self, store: UserStore, text: str) -> str | None:
        low = text.lower().strip(" .!¡¿")
        if low in COMMANDS["help"]:
            return self.t("help")
        if low in COMMANDS["prefs"]:
            prefs = store.preferences()
            return self.t("prefs") + "\n".join(f"- {p}" for p in prefs) if prefs else self.t("no_prefs")
        if low in COMMANDS["reset_prefs"]:
            store.reset_preferences()
            return self.t("prefs_reset")
        if low in COMMANDS["profile"]:
            p = store.profile()
            if p.get("setup_done"):
                return self.t("profile", name=p["name"], handles=p.get("handles", ""), context=p["context"])
            return self.t("no_profile")
        if low in COMMANDS["skills"]:
            return self.t("skills") + "\n".join(f"- {name}" for name in self.library.skills)
        return None

    def _converse(self, store: UserStore, text: str, channel: str, attachments: list[Attachment]) -> Reply:
        turn: dict = {"role": "user", "content": text}
        if attachments:
            turn["attachments"] = [a.to_dict() for a in attachments]
        history = store.history() + [turn]
        try:
            response, nudge = "", ""
            for _ in range(MAX_SKILL_ROUNDS + 1):
                system = self._system(store, channel) + ([nudge] if nudge else [])
                response = self.llm.complete(system, self._for_model(store, history))
                wanted = [
                    name.strip() for kind, name in TAG_RE.findall(response)
                    if kind == "SKILL" and name.strip() in self.library
                ]
                new = [name for name in wanted if name not in store.active_skills()]
                if new:
                    store.activate_skills(new)
                    continue
                if wanted and not TAG_RE.sub("", response).strip():
                    nudge = "The skills you asked for are already loaded above. Answer the user now, without [SKILL:] tags."
                    continue
                break
        except LLMError as e:
            return Reply(self.t("error", error=e))

        response, files = self._make_files(store, response)
        clean = self._apply_tags(store, response)
        if not clean and not files:
            # The model only asked for skills it already had; never send an empty message.
            clean = "…"
        sent = "".join(f"\n[sent file: {f.name}]" for f in files)
        history.append({"role": "assistant", "content": (clean + sent).strip()})
        store.save_history(history)
        return Reply(self._humanize_drafts(clean), files)

    def _make_files(self, store: UserStore, response: str) -> tuple[str, list[Path]]:
        files = []
        for name, content in FILE_RE.findall(response):
            try:
                files.append(render(name, content, store.dir / "outbox"))
            except Exception as e:  # a broken document must not lose the rest of the answer
                response += f"\n\n({name}: {type(e).__name__})"
        return FILE_RE.sub("", response), files

    def _for_model(self, store: UserStore, history: list[dict]) -> list[dict]:
        """History as the model sees it: recent images and PDFs as bytes, older files as text."""
        with_files = [i for i, m in enumerate(history) if m.get("attachments")]
        inline = set(with_files[-INLINE_ATTACHMENT_TURNS:])
        out = []
        for i, message in enumerate(history):
            if not message.get("attachments"):
                out.append({"role": message["role"], "content": message["content"]})
                continue
            parts = []
            for a in message["attachments"]:
                part = {k: a[k] for k in ("kind", "name", "mime", "text")}
                path = store.dir / a["path"]
                if i in inline and a["kind"] in ("image", "pdf") and path.exists():
                    part["data"] = base64.standard_b64encode(path.read_bytes()).decode()
                parts.append(part)
            out.append({"role": "user", "content": message["content"], "attachments": parts})
        return out

    def _system(self, store: UserStore, channel: str) -> list[str]:
        stable = INSTRUCTIONS.format(
            bot=self.bot_name,
            skills=self.library.index() or "(none installed)",
            files=FILES,
            formatting=FORMATTING.get(channel, FORMATTING["terminal"]),
        )
        profile = store.profile()
        if profile.get("setup_done"):
            profile_text = f"Name: {profile['name']}\nHandles: {profile.get('handles', '')}\nContext: {profile['context']}"
        else:
            profile_text = "NEW USER: they just saw the welcome message. Extract their profile from their answer with [SETUP: ...]."
        prefs = store.preferences()
        today = store.published_today()
        recent = store.recent_published()
        parts = [
            f"<user_profile>\n{profile_text}\n</user_profile>",
            f"<voice_pack>\n{store.voice_pack() or '(no voice samples yet)'}\n</voice_pack>",
        ]
        if prefs:
            parts.append("LEARNED PREFERENCES (follow these above any default):\n" + "\n".join(f"- {p}" for p in prefs))
        if recent:
            parts.append("Posts this user chose to publish recently (their real voice):\n" + "\n".join(
                f"- [{p.get('network', 'x')}] {p.get('text', '')[:200]}" for p in recent))
        today_text = "\n".join(f"- [{p.get('time', '')} {p.get('network', 'x')}] {p.get('text', '')[:150]}" for p in today)
        parts.append(
            f"<today>\n{datetime.now().strftime('%A %d %B %Y, %H:%M')}\n"
            f"{'Published today (do not repeat topics):' + chr(10) + today_text if today else 'Nothing published yet today.'}\n</today>"
        )
        for name in store.active_skills():
            skill = self.library.get(name)
            if skill:
                parts.append(f'<skill name="{name}" loaded="yes">\n{skill.body()}\n</skill>')
        return [stable, "\n\n".join(parts)]

    def _apply_tags(self, store: UserStore, response: str) -> str:
        for kind, value in TAG_RE.findall(response):
            value = value.strip()
            if kind == "LEARN":
                store.add_preference(value)
            elif kind == "PUBLISHED":
                network, sep, post = value.partition("|")
                if sep and network.strip().lower() in ("x", "twitter", "linkedin", "instagram", "youtube"):
                    store.add_published(network.strip().lower(), post.strip())
                else:
                    store.add_published("x", value)
            elif kind == "VOICE":
                store.add_voice_sample(value)
            elif kind == "SETUP":
                parts = [p.strip() for p in value.split("|")]
                if len(parts) >= 3:
                    store.save_profile(parts[0], parts[1], " | ".join(parts[2:]))
        clean = TAG_WITH_SPACE_RE.sub("", response)
        return re.sub(r"\n{3,}", "\n\n", clean).strip()

    def _humanize_drafts(self, text: str) -> str:
        if not self.humanizer:
            return text
        humanizer = self.humanizer
        return CODE_BLOCK_RE.sub(lambda m: f"```\n{humanizer(m.group(1))}\n```", text)


def split_drafts(text: str) -> list[str]:
    """Split a reply into chat text and one message per draft, so each draft can be copied alone."""
    messages = []
    pos = 0
    for match in CODE_BLOCK_RE.finditer(text):
        before = text[pos:match.start()].strip()
        if before:
            messages.append(before)
        draft = match.group(1).strip()
        if draft:
            messages.append(draft)
        pos = match.end()
    after = text[pos:].strip()
    if after:
        messages.append(after)
    return messages
