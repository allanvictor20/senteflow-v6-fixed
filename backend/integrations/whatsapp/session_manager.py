"""
SenteFlow AI - Evolution API Session Manager
============================================
Manages Evolution API instance lifecycle.
"""

import logging
import os
from typing import Optional

from integrations.whatsapp.client import EvolutionClient

logger = logging.getLogger(__name__)


class WhatsAppSessionManager:
    """Handles Evolution API session lifecycle and readiness checks."""

    def __init__(self, client: Optional[EvolutionClient] = None):
        self.client = client or EvolutionClient()

    async def ensure_instance(self) -> dict:
        """Create the Evolution API instance if it does not exist yet."""
        return await self.client.create_instance_if_needed()

    async def get_status(self) -> dict:
        """Get a full session status report."""
        health = await self.client.health_check()
        session = await self.client.get_session_status()
        return {
            "connected": session.get("connected", False),
            "state": session.get("status", "unknown"),
            "session_name": os.environ.get("EVOLUTION_SESSION", "senteflow"),
            "evolution_url": os.environ.get("EVOLUTION_BASE_URL", "http://localhost:8080"),
            "health": health,
            "session": session,
        }

    async def is_ready(self) -> bool:
        """Return True when the WhatsApp session is connected."""
        try:
            status = await self.get_status()
            return status["connected"]
        except Exception:
            return False
    
    async def reconnect_if_needed(self) -> bool:
        """
        Check session state and attempt reconnect if disconnected.
        Returns True if connected after the call, False otherwise.
        """
        try:
            if await self.is_ready():
                return True
            logger.warning("whatsapp_session_disconnected_attempting_reconnect")
            await self.client.create_instance_if_needed()
            # Give Evolution API a moment to establish the connection
            import asyncio
            await asyncio.sleep(3)
            connected = await self.is_ready()
            if connected:
                logger.info("whatsapp_session_reconnected")
            else:
                logger.warning("whatsapp_reconnect_failed")
            return connected
        except Exception as exc:
            logger.error("whatsapp_reconnect_error", extra={"error": str(exc)})
            return False

    async def send_startup_notification(self, admin_chat_id: str) -> None:
        """Send a startup notification to confirm the bot is live."""
        if not admin_chat_id:
            return
        try:
            await self.client.send_text(
                admin_chat_id,
                "SenteFlow AI is online.\n\n"
                "Your WhatsApp business assistant is ready.\n"
                'Send "help" to see what I can do.',
            )
            logger.info("startup_notification_sent", extra={"to": admin_chat_id})
        except Exception as exc:
            logger.warning("startup_notification_failed", extra={"error": str(exc)})
