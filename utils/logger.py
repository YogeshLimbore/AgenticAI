"""
utils/logger.py — Structured logging with Rich console + rotating file handler
Replaces all print() calls with proper log levels.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler
from rich.console import Console

console = Console()

_configured = False


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    global _configured
    if _configured:
        return logging.getLogger("naukri_agent")

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agent.log"

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # Rich console handler (colorful, human-readable)
    rich_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        show_path=False,
        markup=True,
    )
    rich_handler.setLevel(level)

    # Rotating file handler (machine-readable, max 5MB × 3 files)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, date_fmt))

    root = logging.getLogger("naukri_agent")
    root.setLevel(logging.DEBUG)
    root.addHandler(rich_handler)
    root.addHandler(file_handler)

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"naukri_agent.{name}")
