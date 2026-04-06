"""
utils/credentials.py — Secure credential storage
Supports OS keyring (recommended) and plain .env fallback.

Usage:
    Store:  python -m naukri_agent_v2.utils.credentials --store
    Fetch:  get_credentials()
"""

from __future__ import annotations

import sys
from typing import Tuple

from utils.logger import get_logger

log = get_logger("credentials")

SERVICE_NAME = "naukri_ai_agent"


def get_credentials(store: str = "env",
                    email: str = "", password: str = "") -> Tuple[str, str]:
    """
    Returns (email, password).
    store="keyring" → reads from OS keyring (secure).
    store="env"     → returns whatever was passed from Settings.
    """
    if store == "keyring":
        try:
            import keyring
            stored_email = keyring.get_password(SERVICE_NAME, "email") or email
            stored_pass  = keyring.get_password(SERVICE_NAME, "password") or password
            log.debug("Credentials loaded from OS keyring")
            return stored_email, stored_pass
        except ImportError:
            log.warning("keyring not installed — falling back to .env credentials")
        except Exception as e:
            log.warning(f"Keyring error: {e} — falling back to .env")

    return email, password


def store_credentials_in_keyring(email: str, password: str) -> bool:
    """Save credentials to OS keyring. Run once interactively."""
    try:
        import keyring
        keyring.set_password(SERVICE_NAME, "email", email)
        keyring.set_password(SERVICE_NAME, "password", password)
        log.info(f"Credentials for {email} stored in OS keyring")
        return True
    except Exception as e:
        log.error(f"Could not store in keyring: {e}")
        return False


if __name__ == "__main__":
    if "--store" in sys.argv:
        email    = input("Naukri email: ").strip()
        password = input("Naukri password: ").strip()
        store_credentials_in_keyring(email, password)
