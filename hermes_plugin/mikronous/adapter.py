"""`mikronous` delivery platform: cron reminders and gateway messages land on the KDE desktop.

Outbound only. There is no inbound chat here — that is the tray app talking to the API server.
Imports Hermes gateway modules, so this file is only imported from the deferred ``register()``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms._shared import env_is_connected, seed_extra_from_env, send_error
from gateway.platforms.base import BasePlatformAdapter, SendResult

from . import deliver as deliver_mod

logger = logging.getLogger(__name__)

PLATFORM = "mikronous"
HOME_ENV = "MIKRONOUS_HOME_CHANNEL"
MAX_MESSAGE_LENGTH = 4000
PLATFORM_HINT = ("Messages on this platform are shown as desktop notifications on the user's KDE desktop. "
                 "Keep them short: a title line, then one or two lines. No markdown tables or code blocks.")


class MikronousAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config=config, platform=Platform(PLATFORM))
        extra = config.extra or {}
        home = extra.get("home_channel") or {}
        self._home_chat = (home.get("chat_id") if isinstance(home, dict) else None) or "desktop"

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        self._mark_connected()
        logger.info("[%s] desktop delivery ready (inbox %s, socket %s)", self.name, deliver_mod.INBOX,
                    deliver_mod.socket_path())
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        text = (content or "")[: self.MAX_MESSAGE_LENGTH]
        result = deliver_mod.deliver(chat_id or self._home_chat, text, source="gateway")
        if result.get("success"):
            return SendResult(success=True, message_id=result.get("message_id"), raw_response=result)
        return SendResult(success=False, error=str(result.get("error")))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": "Desktop", "type": "dm", "id": chat_id or self._home_chat}


def check_requirements() -> bool:
    return True  # stdlib + desktop binaries probed at call time


def validate_config(config) -> bool:
    return True


def _env_enablement() -> dict | None:
    return seed_extra_from_env((), home_env=HOME_ENV, home_default="desktop")


async def _standalone_send(pconfig, chat_id: str, message: str, *, thread_id: Optional[str] = None,
                           media_files: Optional[List[str]] = None, force_document: bool = False) -> Dict[str, Any]:
    """Out-of-process delivery (cron / send_message without a live gateway adapter)."""
    result = deliver_mod.deliver(chat_id or "desktop", (message or "")[:MAX_MESSAGE_LENGTH], source="cron")
    if result.get("success"):
        return {"success": True, "platform": PLATFORM, "chat_id": chat_id or "desktop", "message_id": result["message_id"]}
    return send_error(f"mikronous desktop delivery failed: {result.get('error')}")


def register_platform(ctx) -> None:
    ctx.register_platform(
        name=PLATFORM, label="Mikronous Desktop", adapter_factory=lambda cfg: MikronousAdapter(cfg),
        check_fn=check_requirements, validate_config=validate_config,
        is_connected=env_is_connected(HOME_ENV),   # on exactly in profiles whose .env sets it
        required_env=[], install_hint="",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var=HOME_ENV,
        standalone_sender_fn=_standalone_send,
        max_message_length=MAX_MESSAGE_LENGTH, emoji="🖥️", pii_safe=True,
        platform_hint=PLATFORM_HINT,
    )
