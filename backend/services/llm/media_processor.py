"""
SenteFlow AI — Media Processor
================================
Downloads media from WhatsApp via Evolution API and saves it to the storage layer.
Returns a local file path ready for AI extraction.

Storage layout:
  backend/storage/receipts/     — images, PDFs, documents
  backend/storage/voice_notes/  — audio, voice notes
  backend/storage/uploads/      — other media

Voice note pipeline (v6.1):
  - Deepgram nova-2 STT replaces Groq Whisper for primary transcription.
    Deepgram has dedicated Swahili support, auto-detects language (Sheng
    code-switching), and adds punctuation/capitalization. Falls back to
    Groq Whisper if DEEPGRAM_API_KEY is not set.
  - Silero VAD segments long voice memos (>30s) into separate utterances.
    Each segment is transcribed independently — a single 3-minute recap
    voice note can produce multiple distinct business events instead of
    one merged event.

FIX (v2): Robust media download.
  - download_and_save_media now raises MediaDownloadError on failure instead
    of silently returning None. Callers can catch this and send a specific
    error message back to the user.
  - Added retry logic (2 attempts, 3s delay) for transient Evolution API
    media URL expiry — Evolution API URLs can expire by the time the
    background task runs if the queue was busy.
  - Added explicit logging of HTTP status codes on failure so you know
    whether it's a 401 (auth), 403 (expired), 404 (not found), or timeout.
"""

import asyncio
import io
import logging
import mimetypes
import os
import uuid


from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.whatsapp.client import EvolutionClient
from utils.clock import utc_now

logger = logging.getLogger(__name__)

# Base storage directory — relative to backend/
_STORAGE_BASE = os.environ.get(
    "STORAGE_BASE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage"),
)

_MIME_TO_FOLDER = {
    "image/jpeg": "receipts",
    "image/png": "receipts",
    "image/webp": "receipts",
    "image/gif": "receipts",
    "audio/ogg": "voice_notes",
    "audio/mpeg": "voice_notes",
    "audio/mp4": "voice_notes",
    "audio/wav": "voice_notes",
    "application/pdf": "receipts",
}

_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
}

_DOWNLOAD_RETRIES = 2
_RETRY_DELAY_SECONDS = 3

# Voice notes longer than this threshold (in seconds) go through the Silero
# VAD segmentation path. Shorter notes are transcribed in a single Deepgram
# call. 30s is roughly the length of a short conversational turn — anything
# longer likely contains multiple distinct utterances worth extracting
# separately.
_LONG_AUDIO_THRESHOLD_SECONDS = 30.0

# Minimum segment duration to keep. Silero occasionally emits tiny segments
# (<0.5s) that are just audio artifacts — not real speech. Filter them out.
_MIN_SEGMENT_DURATION_SECONDS = 0.5


class MediaDownloadError(Exception):
    """Raised when media cannot be downloaded after all retries."""
    pass


@dataclass
class VoiceSegment:
    """A single speech segment extracted from a longer voice memo.

    `start_ms` / `end_ms` are wallclock offsets within the original audio.
    `audio_bytes` is a self-contained WAV (16kHz, 16-bit, mono) ready to
    hand to Deepgram. `transcript` is filled in by `transcribe_segments`.
    """

    start_ms: int
    end_ms: int
    audio_bytes: bytes
    transcript: str = ""


async def download_and_save_media(
    media_url: Optional[str],
    mime_type: Optional[str],
    sender_id: str,
    source_hint: str,
    wa_client: "EvolutionClient",
    message_id: Optional[str] = None,
    remote_jid: Optional[str] = None,
) -> str:
    """
    Download media from WhatsApp and save to local storage.

    Download order:
      1. Evolution's getBase64FromMediaMessage (by message id) — returns the
         DECRYPTED file. This is the only reliable source: the `url` in a
         WhatsApp webhook points at the encrypted .enc file on WhatsApp's CDN.
      2. A direct URL download, but only for URLs that are not WhatsApp's
         encrypted CDN (e.g. Evolution configured with S3/MinIO storage).

    Raises:
        MediaDownloadError: if all download attempts fail.
    """
    folder = _MIME_TO_FOLDER.get(mime_type, "uploads")
    ext = _MIME_TO_EXT.get(mime_type) or mimetypes.guess_extension(mime_type or "") or ".bin"

    safe_sender = sender_id.split("@")[0].replace("+", "")
    timestamp = utc_now().strftime("%Y%m%d_%H%M%S")
    short_id = str(uuid.uuid4())[:8]
    filename = f"{source_hint}_{safe_sender}_{timestamp}_{short_id}{ext}"

    dest_dir = os.path.join(_STORAGE_BASE, folder)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)

    direct_url_ok = bool(media_url) and not _is_encrypted_whatsapp_url(media_url)

    last_error: Optional[str] = None
    for attempt in range(1, _DOWNLOAD_RETRIES + 1):
        media_bytes: Optional[bytes] = None
        try:
            if message_id and hasattr(wa_client, "get_media_bytes"):
                media_bytes = await wa_client.get_media_bytes(message_id, remote_jid)
            if not media_bytes and direct_url_ok:
                media_bytes = await wa_client.download_media(media_url)
        except Exception as exc:
            last_error = f"download exception (attempt {attempt}): {exc}"
            logger.warning(
                "media_download_exception",
                extra={"attempt": attempt, "message_id": message_id, "error": str(exc)},
            )
            if attempt < _DOWNLOAD_RETRIES:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
            continue

        if not media_bytes:
            last_error = f"empty response from Evolution API (attempt {attempt})"
            logger.warning(
                "media_download_empty",
                extra={"attempt": attempt, "message_id": message_id, "sender": safe_sender},
            )
            if attempt < _DOWNLOAD_RETRIES:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
            continue

        try:
            with open(dest_path, "wb") as f:
                f.write(media_bytes)
            logger.info(
                "media_saved",
                extra={
                    "path": dest_path,
                    "size_bytes": len(media_bytes),
                    "sender": safe_sender,
                    "attempt": attempt,
                },
            )
            return dest_path
        except Exception as exc:
            last_error = f"disk write failed: {exc}"
            logger.error("media_write_failed", extra={"error": str(exc), "path": dest_path})
            raise MediaDownloadError(last_error) from exc

    raise MediaDownloadError(
        f"Failed to download media after {_DOWNLOAD_RETRIES} attempts. Last error: {last_error}"
    )


def _is_encrypted_whatsapp_url(url: str) -> bool:
    """WhatsApp CDN media is AES-encrypted; downloading it directly yields ciphertext."""
    lowered = (url or "").lower()
    return "whatsapp.net" in lowered or lowered.split("?")[0].endswith(".enc")


# ── Audio decoding & VAD ──────────────────────────────────────────────────────


def _decode_to_pcm_16k_mono(audio_bytes: bytes, mime_type: Optional[str]) -> tuple:
    """
    Decode any audio format WhatsApp might send (ogg, m4a, mp3, wav) into
    16kHz mono 32-bit float numpy array — the format Silero VAD expects.

    Returns (samples, sample_rate). Raises on decode failure.
    """
    import numpy as np
    import soundfile as sf

    # soundfile reads from a file-like object; it auto-detects format from
    # the file header for ogg/m4a/mp3/wav on most systems (libsndfile 1.1+).
    # If the format isn't supported, we get an exception — caller falls back.
    buf = io.BytesIO(audio_bytes)
    samples, sr = sf.read(buf, dtype="float32", always_2d=False)

    # Mono: average channels if stereo
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    # Resample to 16kHz if needed — Silero VAD is trained on 16kHz.
    if sr != 16000:
        try:
            import librosa
            samples = librosa.resample(samples, orig_sr=sr, target_sr=16000)
            sr = 16000
        except ImportError:
            # librosa not available — naive linear resample as a fallback.
            # Good enough for VAD boundary detection (not for transcription).
            ratio = 16000 / sr
            indices = np.arange(0, len(samples), ratio).astype(np.int64)
            samples = samples[indices]
            sr = 16000

    return samples, sr


def _segment_with_silero(samples, sample_rate: int) -> list[VoiceSegment]:
    """
    Run Silero VAD over the decoded audio, return a list of VoiceSegments
    (each containing its own WAV-encoded bytes ready for Deepgram).
    """
    from silero_vad import load_silero_vad, get_speech_timestamps
    import torch
    import soundfile as sf

    model = load_silero_vad()
    # Silero expects a torch tensor
    wav_tensor = torch.from_numpy(samples).unsqueeze(0)

    # get_speech_timestamps returns a list of {"start": sample_idx, "end": sample_idx}
    # in sample positions within the input tensor.
    speech_ts = get_speech_timestamps(
        wav_tensor,
        model,
        sampling_rate=sample_rate,
        return_seconds=False,
        threshold=0.5,
        min_speech_duration_ms=250,
        min_silence_duration_ms=300,
        speech_pad_ms=200,
    )

    segments: list[VoiceSegment] = []
    for ts in speech_ts:
        start_sample = ts["start"]
        end_sample = ts["end"]
        duration_s = (end_sample - start_sample) / sample_rate
        if duration_s < _MIN_SEGMENT_DURATION_SECONDS:
            continue

        # Slice the segment from the original samples
        seg_samples = samples[start_sample:end_sample]

        # Encode as 16-bit PCM WAV in memory — Deepgram accepts WAV bytes.
        buf = io.BytesIO()
        sf.write(buf, seg_samples, sample_rate, format="WAV", subtype="PCM_16")
        seg_bytes = buf.getvalue()

        segments.append(VoiceSegment(
            start_ms=int(start_sample * 1000 / sample_rate),
            end_ms=int(end_sample * 1000 / sample_rate),
            audio_bytes=seg_bytes,
        ))

    return segments


def segment_voice_memo(audio_bytes: bytes, mime_type: Optional[str]) -> list[VoiceSegment]:
    """
    High-level: decode + run Silero VAD + return list of speech segments.

    Used by ai/extractor.py for long voice memos: each segment is transcribed
    independently so the LLM extractor sees one utterance at a time, producing
    multiple distinct BusinessEvents from a single voice memo.

    Returns an empty list if VAD couldn't find any speech (e.g. silent file,
    or audio format couldn't be decoded). Callers should fall back to the
    single-pass transcription path in that case.
    """
    try:
        samples, sr = _decode_to_pcm_16k_mono(audio_bytes, mime_type)
    except Exception as exc:
        logger.warning("voice_memo_decode_failed", extra={"error": str(exc), "mime": mime_type})
        return []

    try:
        segments = _segment_with_silero(samples, sr)
    except Exception as exc:
        logger.warning("voice_memo_vad_failed", extra={"error": str(exc)})
        return []

    logger.info(
        "voice_memo_segmented",
        extra={
            "segment_count": len(segments),
            "total_duration_s": round(len(samples) / sr, 1),
        },
    )
    return segments


# ── Transcription (Deepgram primary, Groq Whisper fallback) ───────────────────


async def _transcribe_with_deepgram(audio_bytes: bytes, mime_type: Optional[str]) -> str:
    """
    Send the audio bytes to Deepgram's nova-2 model with auto-language detection.
    Returns the transcript text. Raises on failure.

    Calls the REST API directly. The previous code used the v3 SDK interface
    (`listen.asyncprerecorded`), but requirements allowed any newer major
    version, whose API is different — so the import failed and every voice
    note silently fell back to Whisper.
    """
    import httpx

    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY not set")

    params = {
        "model": os.environ.get("DEEPGRAM_MODEL", "nova-2"),
        # "multi" enables auto-language detection — useful for code-switching.
        "language": os.environ.get("DEEPGRAM_LANGUAGE", "multi"),
        "smart_format": "true",
        "punctuate": "true",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen",
            params=params,
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": mime_type or "audio/ogg",
            },
            content=audio_bytes,
        )
        resp.raise_for_status()
        data = resp.json()
    try:
        return (data["results"]["channels"][0]["alternatives"][0]["transcript"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


async def _transcribe_with_groq_whisper(audio_bytes: bytes, mime_type: Optional[str]) -> str:
    """
    Fallback: transcribe with Groq Whisper (whisper-large-v3).
    Lower accuracy on Swahili/Luganda than Deepgram but works without
    a Deepgram API key.
    """
    import os
    from io import BytesIO
    from openai import AsyncOpenAI

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    from services.llm.models import whisper_model as _whisper_model
    whisper_model = _whisper_model()

    ext_map = {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/wav": ".wav",
    }
    filename = f"voice_note{ext_map.get(mime_type, '.ogg')}"

    response = await client.audio.transcriptions.create(
        model=whisper_model,
        file=(filename, BytesIO(audio_bytes), mime_type or "audio/ogg"),
        response_format="text",
        language=None,  # auto-detect
    )
    return (response or "").strip()


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    mime_type: Optional[str] = None,
) -> str:
    """
    Transcribe a single in-memory audio blob. Tries Deepgram first, falls
    back to Groq Whisper if Deepgram fails or is unconfigured.
    """
    if os.environ.get("DEEPGRAM_API_KEY"):
        try:
            text = await _transcribe_with_deepgram(audio_bytes, mime_type)
            if text:
                return text
            logger.warning("deepgram_empty_transcript_falling_back_to_whisper")
        except Exception as exc:
            logger.warning("deepgram_transcribe_failed", extra={"error": str(exc)})

    # Fallback: Groq Whisper
    try:
        return await _transcribe_with_groq_whisper(audio_bytes, mime_type)
    except Exception as exc:
        logger.warning("groq_whisper_transcribe_failed", extra={"error": str(exc)})
        return ""


async def transcribe_segments(
    segments: list[VoiceSegment],
    mime_type: Optional[str] = None,
) -> list[VoiceSegment]:
    """
    Transcribe each VoiceSegment in parallel using Deepgram (with Whisper
    fallback per-segment). Mutates the input list — fills in `transcript`
    on each segment. Returns the same list.
    """
    if not segments:
        return segments

    async def _one(seg: VoiceSegment) -> str:
        return await transcribe_audio_bytes(seg.audio_bytes, "audio/wav")

    transcripts = await asyncio.gather(*[_one(s) for s in segments])
    for seg, txt in zip(segments, transcripts):
        seg.transcript = txt
    return segments


async def transcribe_voice_note(audio_bytes: bytes, mime_type: Optional[str]) -> list[str]:
    """
    Transcribe a WhatsApp voice note into one or more utterances.

    Short notes → one transcript. Long notes (> _LONG_AUDIO_THRESHOLD_SECONDS)
    are split with Silero VAD so a 3-minute recap ("Brian paid 50k... Sarah
    took 3 bags on credit...") becomes separate messages, each processed as
    its own business event. Decoding/VAD is CPU work, so it runs off the
    event loop.
    """
    duration = await asyncio.to_thread(estimate_audio_duration_seconds, audio_bytes, mime_type)
    if duration > _LONG_AUDIO_THRESHOLD_SECONDS:
        segments = await asyncio.to_thread(segment_voice_memo, audio_bytes, mime_type)
        if segments:
            segments = await transcribe_segments(segments, mime_type)
            texts = [s.transcript.strip() for s in segments if s.transcript and s.transcript.strip()]
            if texts:
                return texts
    text = (await transcribe_audio_bytes(audio_bytes, mime_type)).strip()
    return [text] if text else []


def estimate_audio_duration_seconds(audio_bytes: bytes, mime_type: Optional[str]) -> float:
    """
    Best-effort estimate of audio duration in seconds. Used to decide
    whether to use the segmented path (>30s) or single-pass path (≤30s).
    Returns 0.0 if duration can't be determined — caller treats unknown
    durations as "short" (single-pass).
    """
    try:
        samples, sr = _decode_to_pcm_16k_mono(audio_bytes, mime_type)
        return len(samples) / sr if sr > 0 else 0.0
    except Exception:
        return 0.0
