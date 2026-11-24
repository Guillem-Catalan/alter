#!/usr/bin/env python3
"""Run Alter, the publishing assistant.

  python main.py doctor            check the setup and say what is missing
  python main.py chat [-m TEXT] [-f FILE]   talk to it from the terminal, optionally with files
  python main.py whatsapp          run the WhatsApp webhook server
  python main.py slack             run the Slack bot (Socket Mode)
  python main.py transcribe FILE   transcribe an audio file (tests the voice setup)
"""
import argparse
import logging
import shutil
import sys
from pathlib import Path

from agent.brain import Agent
from agent.config import inside_git_repo, load_config
from agent.llm import LLMError, make_llm
from agent.skills import SkillLibrary, load_humanizer
from agent.transcribe import Transcriber


def build_agent(config):
    library = SkillLibrary(config.skills_dirs)
    humanizer = load_humanizer(library) if config.humanize else None
    return Agent(make_llm(config), library, config.data_dir, config.bot_name, config.language, humanizer)


def doctor(config) -> int:
    from agent.channels.whatsapp import WhatsAppChannel

    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        print(("  ok   " if good else "  FIX  ") + text)

    print(f"{config.bot_name}: setup check")
    line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]} (3.10+ needed)")
    try:
        llm = make_llm(config)
        line(True, f"Model: {config.llm_provider} / {getattr(llm, 'model', '')}")
    except (LLMError, ImportError) as e:
        line(False, f"Model: {e}")
    line(not inside_git_repo(config.data_dir),
         f"Personal data: {config.data_dir}" + (" is inside a git repository: move it (DATA_DIR)" if inside_git_repo(config.data_dir) else ""))
    library = SkillLibrary(config.skills_dirs)
    line(bool(library.skills), f"Skills: {len(library.skills)} found in {', '.join(str(d) for d in config.skills_dirs)}")
    from agent import secrets
    keychain = secrets.available()
    line(not keychain, "Keychain for connection secrets: " + (keychain or "available"))
    issues = Transcriber(config).problems()
    line(not issues, "Voice notes: " + ("; ".join(issues) if issues else "whisper.cpp and model ready"))
    missing = WhatsAppChannel(config, agent=None, transcriber=None).missing_settings()  # type: ignore[arg-type]
    line(not missing, "WhatsApp: " + (f"missing {', '.join(missing)}" if missing else "settings present"))
    line(bool(shutil.which("ngrok")), "Tunnel: ngrok " + ("installed" if shutil.which("ngrok") else "not installed (or use another tunnel)"))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    chat = sub.add_parser("chat")
    chat.add_argument("-m", "--message")
    chat.add_argument("-f", "--file", type=Path, action="append", help="attach a file (repeatable)")
    sub.add_parser("whatsapp")
    sub.add_parser("slack")
    tr = sub.add_parser("transcribe")
    tr.add_argument("file", type=Path)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load_config()

    if args.command == "doctor":
        return doctor(config)
    if args.command == "transcribe":
        print(Transcriber(config)(args.file.read_bytes(), args.file.suffix or ".ogg"))
        return 0

    agent = build_agent(config)
    if args.command == "chat":
        from agent.channels import terminal
        terminal.run(agent, Transcriber(config), config.ffmpeg_bin, args.message, args.file)
    elif args.command == "slack":
        from agent.channels import slack
        slack.run(config, agent)
    elif args.command == "whatsapp":
        from agent.channels.whatsapp import WhatsAppChannel
        channel = WhatsAppChannel(config, agent, Transcriber(config))
        missing = channel.missing_settings()
        if missing:
            print(f"Missing settings: {', '.join(missing)}. See README.md.", file=sys.stderr)
            return 1
        server = channel.make_server()
        logging.info("WhatsApp webhook listening on http://127.0.0.1:%s/webhook", config.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
