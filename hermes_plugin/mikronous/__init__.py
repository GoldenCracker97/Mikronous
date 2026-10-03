"""Mikronous Hermes plugin — KDE Plasma desktop integration.

Phase 0: stub so `hermes plugins enable mikronous` succeeds and the gateway loads.
Phase 1 adds desktop tools (tools.py); Phase 2 adds the `mikronous` delivery platform (adapter.py).
Keep this module import-light: Hermes imports it in every process (CLI, TUI, gateway, cron).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


def register(ctx) -> None:
    """Plugin entry point called by the Hermes plugin loader."""
    logger.debug("mikronous plugin %s loaded (Phase 0 stub: no tools yet)", __version__)
