"""
SenteFlow AI — WhatsApp Reply Sender
=====================================
Adapter layer for sending conversational replies to WhatsApp via Evolution API.

Voice-aware replies (v6.1):
  When the inbound message was a voice note, the reply should be a synthesized
  voice note (same modality) rather than plain text. This is critical for
  low-literacy users and makes the bot feel like a person, not a chatbot.

  Voice synthesis uses ElevenLabs (ELEVENLABS_API_KEY). Falls back to plain
  text if:
    - ELEVENLABS_API_KEY is not set
    - TTS synthesis fails for any reason
    - the inbound message was NOT a voice note

  Audio format: WhatsApp voice notes are OGG/Opus. Evolution API's
  /message/sendWhatsAppAudio endpoint accepts a base64-encoded audio buffer
  or a public URL. We synthesize MP3 via ElevenLabs and let Evolution API
  handle the format conversion on its end.
"""

import base64
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class WhatsAppReplySender:
    """Adapter around EvolutionClient that adds voice-note reply support."""

    def __init__(self, wa_client):
        self.wa_client = wa_client

    async def send(self, chat_id: str, text: str) -> dict:
        """Send a plain text reply."""
        return await self.wa_client.send_text(chat_id, text)

    async def send_voice(
        self,
        chat_id: str,
        text: str,
        voice: Optional[str] = None,
    ) -> dict:
        """
        Synthesize `text` to speech via ElevenLabs and send as a WhatsApp
        voice note. Falls back to plain text if ElevenLabs is unavailable
        or synthesis fails.

        Args:
            chat_id: WhatsApp chat ID (phone number@s.whatsapp.net)
            text:    The reply text to synthesize.
            voice:   Optional ElevenLabs voice name (default: env ELEVENLABS_VOICE
                     or "Rachel")
        """
        try:
            audio_bytes = await _synthesize_with_elevenlabs(text, voice)
            if not audio_bytes:
                # Synthesis returned empty — fall back to text
                logger.warning("voice_reply_empty_audio_falling_back_to_text", extra={"chat_id": chat_id})
                return await self.wa_client.send_text(chat_id, text)
            return await _send_audio_bytes(self.wa_client, chat_id, audio_bytes)
        except Exception as exc:
            logger.warning(
                "voice_reply_failed_falling_back_to_text",
                extra={"error": str(exc), "chat_id": chat_id},
            )
            return await self.wa_client.send_text(chat_id, text)

    async def send_voice_aware(
        self,
        chat_id: str,
        text: str,
        inbound_was_voice: bool = False,
    ) -> dict:
        """
        Convenience method: send a voice reply if the inbound message was a
        voice note, otherwise send plain text. This is the entry point
        workflows should call — they just need to pass the inbound modality.
        """
        if inbound_was_voice and os.environ.get("ELEVENLABS_API_KEY"):
            return await self.send_voice(chat_id, text)
        return await self.wa_client.send_text(chat_id, text)


async def send(wa_client, chat_id: str, text: str) -> dict:
    """Module-level convenience: send a plain text reply."""
    return await WhatsAppReplySender(wa_client).send(chat_id, text)


async def send_voice(wa_client, chat_id: str, text: str, voice: Optional[str] = None) -> dict:
    """Module-level convenience: send a synthesized voice-note reply."""
    return await WhatsAppReplySender(wa_client).send_voice(chat_id, text, voice)


async def send_voice_aware(
    wa_client,
    chat_id: str,
    text: str,
    inbound_was_voice: bool = False,
) -> dict:
    """Module-level convenience: send voice if inbound was voice, else text."""
    return await WhatsAppReplySender(wa_client).send_voice_aware(
        chat_id, text, inbound_was_voice
    )


# ── ElevenLabs synthesis ──────────────────────────────────────────────────────


def _speakable(text: str) -> str:
    """Drop WhatsApp formatting marks and emoji so TTS doesn't read them out."""
    import re
    # The "(confidence: 93%)" footnote helps on screen but is noise when spoken.
    cleaned = re.sub(r"\n?_?\(confidence: \d+%\)_?", "", text or "")
    cleaned = re.sub(r"[*_~`]", "", cleaned)
    cleaned = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", cleaned)
    return re.sub(r"[ \t]+", " ", cleaned).strip()


async def _synthesize_with_elevenlabs(text: str, voice: Optional[str] = None) -> Optional[bytes]:
    """
    Synthesize `text` into MP3 bytes with ElevenLabs. Returns None if the API
    key isn't set or the call fails.

    Uses the HTTP API directly: the SDK call was synchronous (it blocked the
    event loop) and was passed a voice NAME where the API needs a voice ID,
    so it always failed. ELEVENLABS_VOICE may be either a name or an ID.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        return None
    voice_name = voice or os.environ.get("ELEVENLABS_VOICE", "Rachel")
    return await _synthesize_via_http(_speakable(text), api_key, voice_name)


_voice_id_cache: dict[str, str] = {}


async def _synthesize_via_http(text: str, api_key: str, voice_name: str) -> Optional[bytes]:
    import httpx

    model_id = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2")
    try:
        voice_id = _voice_id_cache.get(voice_name)
        if not voice_id:
            async with httpx.AsyncClient(timeout=15) as client:
                voices_resp = await client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": api_key},
                )
                voices_resp.raise_for_status()
                voices = voices_resp.json().get("voices", [])
            voice_id = next(
                (
                    v["voice_id"] for v in voices
                    if v.get("voice_id") == voice_name or v.get("name", "").lower() == voice_name.lower()
                ),
                None,
            )
            if not voice_id and voices:
                # Default to the first available voice if the name didn't match
                voice_id = voices[0]["voice_id"]
            if not voice_id:
                return None
            _voice_id_cache[voice_name] = voice_id

        async with httpx.AsyncClient(timeout=30) as client:
            tts_resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": text,
                    "model_id": model_id,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.7},
                },
            )
            tts_resp.raise_for_status()
            return tts_resp.content
    except Exception as exc:
        logger.warning("elevenlabs_http_synthesis_failed", extra={"error": str(exc)})
        return None


# ── Evolution API audio upload ────────────────────────────────────────────────


async def _send_audio_bytes(wa_client, chat_id: str, audio_bytes: bytes) -> dict:
    """
    Send pre-synthesized audio bytes to WhatsApp as a voice note via
    Evolution API's /message/sendWhatsAppAudio endpoint.

    Evolution API accepts either a public URL or a base64-encoded payload.
    Since we don't have a public URL for the just-synthesized audio, we
    send it as base64.
    """
    import httpx

    b64 = base64.b64encode(audio_bytes).decode("ascii")
    payload = {
        "number": chat_id,
        "audio": b64,
        "encoding": True,
        "mediatype": "audio",
    }

    # Use the EvolutionClient's session info to build the URL — we don't
    # call wa_client.send_text() here because we need the audio endpoint.
    url = f"{wa_client.base_url}/message/sendWhatsAppAudio/{wa_client.session}"
    headers = {
        "Content-Type": "application/json",
        "apikey": wa_client.api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=wa_client.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error(
            "evolution_send_audio_failed",
            extra={"chat_id": chat_id, "error": str(exc)},
        )
        raise
