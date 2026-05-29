"""Logging configuration for KMB.

Call setup() once at startup (main.py).
Each module then uses: import logging; logger = logging.getLogger(__name__)
"""
from __future__ import annotations
import logging
from pathlib import Path

_LOG_PATH = Path(__file__).parent.parent / "logs" / "kmb.log"


def setup() -> None:
    _LOG_PATH.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(_LOG_PATH, encoding="utf-8"),
        ],
    )
