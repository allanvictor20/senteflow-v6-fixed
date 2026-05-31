"""
SenteFlow AI - Evolution API Client
===================================
Transport layer only. No business logic.

Replaces OpenWAClient. The public methods are kept stable while the
implementation talks to Evolution API.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class EvolutionClient:
    """Thin wrapper around the Evolution API REST interface."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        session: Optional[str] = None,
        timeout: int = 30,
    ):
        self.base_url = (
            base_url or os.environ.get("EVOLUTION_BASE_URL", "http://localhost:8080")
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("EVOLUTION_API_KEY", "")
        self.session = session or os.environ.get("EVOLUTION_SESSION", "senteflow")
        self.timeout = timeout
        self._headers = {
            "Content-Type": "application/json",
            "apikey": self.api_key,
        }

    async def health_check(self) -> dict:
        """Check Evolution API is reachable. GET / returns version info."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(self.base_url, headers=self._headers)
                resp.raise_for_status()
                return {"status": "ok", "data": resp.json()}
            except Exception as exc:
                logger.error("evolution_health_check_failed", extra={"error": str(exc)})
                return {"status": "error", "error": str(exc)}

    async def get_session_status(self) -> dict:
        """Check WhatsApp connection state for the configured session."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(
                    f"{self.base_url}/instance/connectionState/{self.session}",
                    headers=self._headers,
                )
                resp.raise_for_status()
                data = resp.json()
                state = data.get("instance", {}).get("state", "unknown")
                return {"status": state, "connected": state == "open", "raw": data}
            except Exception as exc:
                logger.error("evolution_session_status_failed", extra={"error": str(exc)})
                return {"status": "unknown", "connected": False, "error": str(exc)}

    async def send_text(self, chat_id: str, text: str) -> dict:
        """Send a plain text message."""
        payload = {"number": chat_id, "text": text}
        return await self._post(f"/message/sendText/{self.session}", payload)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
    ) -> dict:
        """Send an image from a URL."""
        payload = {
            "number": chat_id,
            "mediatype": "image",
            "media": image_url,
            "caption": caption or "",
        }
        return await self._post(f"/message/sendMedia/{self.session}", payload)

    async def send_reaction(self, chat_id: str, message_id: str, emoji: str) -> dict:
        """React to a message."""
        payload = {
            "key": {"remoteJid": chat_id, "id": message_id},
            "reaction": emoji,
        }
        return await self._post(f"/message/sendReaction/{self.session}", payload)

    async def download_media(self, media_url: str) -> Optional[bytes]:
        """Download media bytes from Evolution API or WhatsApp CDN."""
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.get(media_url, headers=self._headers)
                resp.raise_for_status()
                return resp.content
            except Exception as exc:
                logger.error(
                    "evolution_download_media_failed",
                    extra={"url": media_url[:80], "error": str(exc)},
                )
                return None

    async def get_media_url(self, message_id: str) -> Optional[str]:
        """Get a media URL or base64 payload for a media message."""
        payload = {"message": {"key": {"id": message_id}}}
        result = await self._post(
            f"/chat/getBase64FromMediaMessage/{self.session}",
            payload,
        )
        return result.get("base64") or result.get("url")

    async def create_instance_if_needed(self) -> dict:
        """Create the Evolution API instance if it does not already exist."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(
                    f"{self.base_url}/instance/fetchInstances",
                    headers=self._headers,
                )
                instances = resp.json() if resp.status_code == 200 else []
                existing = [
                    instance
                    for instance in instances
                    if instance.get("instance", {}).get("instanceName") == self.session
                ]
                if existing:
                    logger.info("evolution_instance_exists", extra={"session": self.session})
                    return {"exists": True, "instance": existing[0]}
            except Exception:
                pass

        payload = {
            "instanceName": self.session,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
        }
        result = await self._post("/instance/create", payload)
        logger.info("evolution_instance_created", extra={"session": self.session})
        return result

    async def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.base_url}{endpoint}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, json=payload, headers=self._headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "evolution_http_error",
                    extra={
                        "endpoint": endpoint,
                        "status": exc.response.status_code,
                        "body": exc.response.text[:200],
                    },
                )
                return {"error": str(exc), "status_code": exc.response.status_code}
            except Exception as exc:
                logger.error(
                    "evolution_request_failed",
                    extra={"endpoint": endpoint, "error": str(exc)},
                )
                return {"error": str(exc)}


# Transitional alias for any imports not yet updated.
OpenWAClient = EvolutionClient
