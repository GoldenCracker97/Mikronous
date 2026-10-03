"""Mikronous Hermes plugin — KDE Plasma desktop integration.

``register(ctx)`` registers everything: the desktop tools, the /notes command and the hooks
(``tools.py``) and then the ``mikronous`` delivery platform (``adapter.py``) so cron reminders
reach the desktop.

Why tools are registered here and not left to ``provides_tools``: Hermes only imports a platform
plugin's ``tools.py`` by itself for its *bundled* platforms (the deferred-load path). A user
``kind: platform`` plugin such as this one is loaded eagerly and only ``register()`` runs, so
without this call the tools and hooks never exist in any process (CLI, gateway or cron).
``register_tools`` skips names that are already registered, so the plugin is safe even if
Hermes starts pre-registering user platform tools too.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__version__ = "0.3.2"


def register(ctx) -> None:
    from .tools import register_tools

    register_tools(ctx)
    try:
        from .adapter import register_platform
    except Exception as exc:  # noqa: BLE001 - gateway modules missing outside Hermes
        logger.warning("mikronous: platform adapter unavailable: %s", exc)
        return
    register_platform(ctx)
