"""Slack channel (Socket Mode): DMs and mentions. Off unless you run `python main.py slack`.

Text in, text and generated files out (files sent to the bot in Slack are not read yet).

Needs `pip install slack-bolt`, a Slack app with Socket Mode, the `message.im` and
`app_mention` events, the `chat:write`, `im:write` and `files:write` scopes and the Messages tab enabled.
"""
from ..brain import Agent
from ..config import Config


def run(config: Config, agent: Agent) -> None:
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError as e:
        raise SystemExit("The Slack channel needs slack-bolt: pip install slack-bolt") from e
    missing = [n for n, v in (("SLACK_BOT_TOKEN", config.slack_bot_token), ("SLACK_APP_TOKEN", config.slack_app_token)) if not v]
    if missing:
        raise SystemExit(f"Missing settings: {', '.join(missing)}")

    app = App(token=config.slack_bot_token)

    def answer(event, say):
        if event.get("subtype") or not event.get("user"):
            return
        reply = agent.handle(event["user"], event.get("text", ""), channel="slack")
        if reply.text:
            say(reply.text)
        for path in reply.files:
            app.client.files_upload_v2(channel=event["channel"], file=str(path), filename=path.name)

    app.event("message")(answer)
    app.event("app_mention")(answer)
    print("Slack channel running (Socket Mode). Ctrl+C to stop.")
    SocketModeHandler(app, config.slack_app_token).start()
