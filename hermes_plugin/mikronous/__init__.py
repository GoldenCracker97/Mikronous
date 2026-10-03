"""Mikronous Hermes plugin — KDE Plasma desktop integration.

Tools, skills, the /notes command and the docs-index hook are registered from ``tools.py``
(``provides_tools`` in plugin.yaml makes Hermes call ``register_tools`` in every process).
This deferred ``register()`` adds the ``mikronous`` delivery platform (Phase 2) so cron
reminders reach the desktop. Keep imports here light: Hermes imports it lazily.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__version__ = "0.2.0"


def register(ctx) -> None:
    """Deferred plugin entry point (gateway / cron / send_message paths)."""
    try:
        from .adapter import register_platform
    except ImportError:
        logger.debug("mikronous: platform adapter not present yet (Phase 2)")
        return
    register_platform(ctx)
