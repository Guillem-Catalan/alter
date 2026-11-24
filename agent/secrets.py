"""Connection secrets (OAuth tokens, API keys) in the operating system keychain, never on disk (ADR 0004)."""
from __future__ import annotations

import json

SERVICE = "alter"


class SecretsUnavailable(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError as e:
        raise SecretsUnavailable("The keyring package is missing: pip install -r requirements.txt") from e
    if isinstance(keyring.get_keyring(), FailKeyring):
        raise SecretsUnavailable(
            "No system keychain found. On Linux install a Secret Service provider (gnome-keyring or KWallet)."
        )
    return keyring


def save(connection_id: str, secret: dict) -> None:
    _keyring().set_password(SERVICE, connection_id, json.dumps(secret))


def load(connection_id: str) -> dict | None:
    raw = _keyring().get_password(SERVICE, connection_id)
    return json.loads(raw) if raw else None


def delete(connection_id: str) -> None:
    kr = _keyring()
    try:
        kr.delete_password(SERVICE, connection_id)
    except kr.errors.PasswordDeleteError:
        pass


def available() -> str:
    """Empty string when the keychain works, otherwise why it does not."""
    try:
        _keyring()
    except SecretsUnavailable as e:
        return str(e)
    return ""
