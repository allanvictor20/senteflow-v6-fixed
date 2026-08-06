"""
SenteFlow AI — AI Extraction Layer (Groq + Deepgram edition)
==================================================
Responsibility: Call Groq and return raw ExtractionResult objects.
This layer does NOT validate, persist, or apply business logic.
It only converts raw files/bytes into structured domain objects.

Provider notes:
  - Text extraction uses `llama-3.3-70b-versatile` (override via GROQ_MODEL).
  - Image extraction uses `llama-3.2-90b-vision-preview` (override via GROQ_VISION_MODEL).
  - PDF: Groq does not accept PDFs directly. We extract text with pypdf,
    then run the text extractor over it.
  - Audio (≤30s): transcribe with Deepgram nova-2 (fallback: Groq Whisper),
    then run the text extractor over the transcript.
  - Audio (>30s): segment with Silero VAD → transcribe each segment with
    Deepgram → run the text extractor per-segment → merge all events.
    This lets a 3-minute recap voice memo produce multiple distinct
    BusinessEvents instead of one merged event.
  - Structured output: Groq supports `response_format={"type": "json_object"}`
    (JSON mode) but not Gemini-style `response_schema`. We embed the schema
    description in the system prompt and parse the JSON with Pydantic.

Separation of concerns:
  AI extraction  → here (identify facts)
  Validation     → validators/transaction_validator.py (enforce rules)
  Persistence    → repositories/transaction_repository.py (save to Firestore)
"""

import base64
import json
import logging
import mimetypes
import os
import uuid
from typing import Optional

from openai import OpenAI

from domain.models import (
    ExtractionResult,
    FieldConfidence,
    SourceTrace,
    Transaction,
)
from prompts.extraction_prompts import ACTIVE_EXTRACTION

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
DEFAULT_GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "llama-3.2-90b-vision-preview")
DEFAULT_GROQ_WHISPER_MODEL = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3")

MODEL_ID = DEFAULT_GROQ_MODEL

# Prompt version tag attached to source traces for auditability.
ACTIVE_EXTRACTION_PROMPT_VERSION = "groq-v1"

# Schema description embedded into prompts so the LLM emits ExtractionResult-shaped JSON.
_SCHEMA_INSTRUCTION = """
Return a JSON object with EXACTLY this shape:
{
  "input_type": "text|image|pdf|audio",
  "transactions": [
    {
      "amount": 0.0,
      "currency": "UGX",
      "transaction_type": "payment|expense|debt_created|payment_promise",
      "category": "other",
      "description": "",
      "payer": null,
      "payee": null,
      "date": "YYYY-MM-DD",
      "notes": null
    }
  ],
  "anomalies": [],
  "summary": "",
  "language_detected": "en",
  "raw_transcript": null,
  "confidence": 0.0
}
Only return valid JSON. No markdown fences. No commentary outside the JSON.
"""


# The client is built on first use, not at import time: constructing it eagerly
# makes this module unimportable whenever GROQ_API_KEY is absent, which breaks
# test collection and any process that only needs the helpers below.
_client_instance: Optional[OpenAI] = None


def _client_factory() -> OpenAI:
    global _client_instance
    if _client_instance is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set — cannot run AI extraction")
        _client_instance = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
    return _client_instance


def _parse_extraction(content: str) -> ExtractionResult:
    """Parse raw LLM JSON output into ExtractionResult, tolerant of markdown fences."""
    raw = (content or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("extraction_json_parse_failed", extra={"error": str(exc), "snippet": raw[:120]})
        # Return an empty result rather than propagating — callers expect an object.
        return ExtractionResult(transactions=[], confidence=0.0, summary="")
    return ExtractionResult.model_validate(data)


def _run_text_extraction(user_prompt: str, system_prompt: str = ACTIVE_EXTRACTION) -> ExtractionResult:
    """Common path: send a text prompt to Groq with JSON mode, parse, return."""
    client = _client_factory()
    response = client.chat.completions.create(
        model=DEFAULT_GROQ_MODEL,
        temperature=0.0,
        max_tokens=2000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt + "\n\n" + _SCHEMA_INSTRUCTION},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    return _parse_extraction(content)


# ─── Default Confidence Fallback ──────────────────────────────────────────────

def _apply_default_confidence(result: ExtractionResult) -> ExtractionResult:
    """Ensure every transaction has a FieldConfidence object."""
    for txn in result.transactions:
        if txn.field_confidence is None:
            txn.field_confidence = FieldConfidence(
                amount=0.7, currency=0.8, transaction_type=0.7, category=0.6,
                payer=0.5 if txn.payer else 0.0,
                payee=0.5 if txn.payee else 0.0,
                date=0.8 if txn.date else 0.0,
            )
    return result


# ─── Source Trace Attachment ──────────────────────────────────────────────────

def _attach_source_traces(
    transactions: list[Transaction],
    session_id: str,
    file_name: str,
    mime_type: str,
    transcript: Optional[str] = None,
) -> list[Transaction]:
    """Attach source provenance data to each extracted transaction."""
    for txn in transactions:
        snippet = None
        if transcript and txn.description:
            words = txn.description.lower().split()[:3]
            for word in words:
                idx = transcript.lower().find(word)
                if idx != -1:
                    start = max(0, idx - 30)
                    end = min(len(transcript), idx + 80)
                    snippet = f"...{transcript[start:end]}..."
                    break
        txn.source_trace = SourceTrace(
            upload_session_id=session_id,
            source_file_name=file_name,
            source_mime_type=mime_type,
            transcript_snippet=snippet,
            extraction_prompt_version=ACTIVE_EXTRACTION_PROMPT_VERSION,
        )
    return transactions


# ─── Media-Type Specific Extractors ──────────────────────────────────────────

def extract_from_text(
    text_content: str,
    session_id: str,
    file_name: str = "text_input",
) -> ExtractionResult:
    user_prompt = f"Extract all business events from this message:\n\n{text_content}"
    result = _run_text_extraction(user_prompt)
    result = _apply_default_confidence(result)
    result.input_type = "text"
    result.upload_session_id = session_id
    result.transactions = _attach_source_traces(
        result.transactions, session_id, file_name, "text/plain", text_content[:500]
    )
    return result


def extract_from_image(
    image_bytes: bytes,
    mime_type: str,
    session_id: str,
    file_name: str,
) -> ExtractionResult:
    """
    Extract business events from an image using Groq's vision model
    (default: llama-3.2-90b-vision-preview). The image is sent as a base64
    data URL alongside a text instruction.
    """
    client = _client_factory()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type or 'image/jpeg'};base64,{b64}"

    user_text = (
        "Extract all business events from this image. "
        "This may be a MoMo screenshot, receipt, or handwritten ledger. "
        "Include confidence scores."
    )

    response = client.chat.completions.create(
        model=DEFAULT_GROQ_VISION_MODEL,
        temperature=0.0,
        max_tokens=2000,
        messages=[
            {
                "role": "system",
                "content": ACTIVE_EXTRACTION + "\n\n" + _SCHEMA_INSTRUCTION,
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    )
    content = response.choices[0].message.content or ""
    result = _parse_extraction(content)
    result = _apply_default_confidence(result)
    result.input_type = "image"
    result.upload_session_id = session_id
    result.transactions = _attach_source_traces(
        result.transactions, session_id, file_name, mime_type
    )
    for txn in result.transactions:
        if txn.field_confidence:
            txn.field_confidence.ocr = 0.85
    return result


def extract_from_pdf(
    pdf_bytes: bytes,
    session_id: str,
    file_name: str,
) -> ExtractionResult:
    """
    Groq does not accept PDF inputs directly. Extract text with pypdf,
    then run the text extractor over the result. Returns an empty
    ExtractionResult if no text could be extracted (e.g. scanned PDFs).
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is required for PDF extraction. Install it with: pip install pypdf"
        ) from exc

    try:
        from io import BytesIO
        reader = PdfReader(BytesIO(pdf_bytes))
        text_parts = [page.extract_text() or "" for page in reader.pages]
        extracted_text = "\n\n".join(text_parts).strip()
    except Exception as exc:
        logger.warning("pdf_text_extract_failed", extra={"error": str(exc), "file": file_name})
        return ExtractionResult(
            input_type="pdf",
            upload_session_id=session_id,
            transactions=[],
            confidence=0.0,
            summary=f"Failed to extract text from PDF: {exc}",
        )

    if not extracted_text:
        logger.warning("pdf_no_text_extracted", extra={"file": file_name})
        return ExtractionResult(
            input_type="pdf",
            upload_session_id=session_id,
            transactions=[],
            confidence=0.0,
            summary="No machine-readable text found in PDF (may be a scanned image).",
        )

    user_prompt = (
        "Extract all business events and member contribution records from this document.\n\n"
        f"--- PDF TEXT ---\n{extracted_text}\n--- END PDF TEXT ---"
    )
    result = _run_text_extraction(user_prompt)
    result = _apply_default_confidence(result)
    result.input_type = "pdf"
    result.upload_session_id = session_id
    result.transactions = _attach_source_traces(
        result.transactions, session_id, file_name, "application/pdf", extracted_text[:500]
    )
    return result


def extract_from_audio(
    audio_bytes: bytes,
    mime_type: str,
    session_id: str,
    file_name: str,
) -> ExtractionResult:
    """
    Audio extraction pipeline:
      - Short audio (≤30s): single-pass transcription + one extraction call.
      - Long audio (>30s):  Silero VAD segmentation → per-segment
        transcription → per-segment extraction → merged result.

    Deepgram nova-2 is the primary STT (better Swahili/Luganda accuracy,
    auto-language detection for Sheng code-switching, adds punctuation).
    Falls back to Groq Whisper if Deepgram is not configured.

    The full transcript (all segments concatenated) is preserved as
    `raw_transcript` on the result for downstream debugging.
    """
    import asyncio
    from services.llm.media_processor import (
        estimate_audio_duration_seconds,
        segment_voice_memo,
        transcribe_audio_bytes,
        transcribe_segments,
        _LONG_AUDIO_THRESHOLD_SECONDS,
    )

    # Step 1: decide single-pass vs segmented
    duration = estimate_audio_duration_seconds(audio_bytes, mime_type)
    logger.info(
        "audio_extraction_starting",
        extra={
            "file": file_name,
            "duration_s": round(duration, 1) if duration else "unknown",
            "segmented": duration > _LONG_AUDIO_THRESHOLD_SECONDS,
        },
    )

    if duration > _LONG_AUDIO_THRESHOLD_SECONDS:
        return _extract_from_audio_segmented(
            audio_bytes, mime_type, session_id, file_name
        )

    # ── Short-audio path: single-pass ──────────────────────────────────────
    # Transcribe with Deepgram (falls back to Whisper internally)
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # We're inside an async caller (e.g. media_extraction_workflow called
        # via asyncio.to_thread). Create a fresh loop for the sync path.
        import threading
        result_box = {}
        def _run():
            try:
                new_loop = asyncio.new_event_loop()
                try:
                    result_box["t"] = new_loop.run_until_complete(
                        transcribe_audio_bytes(audio_bytes, mime_type)
                    )
                finally:
                    new_loop.close()
            except Exception as exc:
                result_box["err"] = exc
        t = threading.Thread(target=_run)
        t.start()
        t.join()
        if "err" in result_box:
            raise result_box["err"]
        transcript = result_box.get("t", "") or ""
    else:
        transcript = loop.run_until_complete(
            transcribe_audio_bytes(audio_bytes, mime_type)
        )

    transcript = (transcript or "").strip()
    if not transcript:
        logger.warning("audio_empty_transcript", extra={"file": file_name})
        return ExtractionResult(
            input_type="audio",
            upload_session_id=session_id,
            transactions=[],
            confidence=0.0,
            raw_transcript="",
            summary="Audio transcription returned empty text.",
        )

    # Step 2: extract business events from transcript
    user_prompt = (
        "The following is a transcript of a voice note from a small business owner. "
        "The speaker may use Luganda, Swahili, English or a mix. "
        "Pay special attention to member names, contribution amounts, and the period being paid for. "
        "Extract all business events.\n\n"
        f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---"
    )
    result = _run_text_extraction(user_prompt)
    result = _apply_default_confidence(result)
    result.input_type = "audio"
    result.upload_session_id = session_id
    result.raw_transcript = transcript
    result.transactions = _attach_source_traces(
        result.transactions, session_id, file_name, mime_type, transcript
    )
    return result


def _extract_from_audio_segmented(
    audio_bytes: bytes,
    mime_type: str,
    session_id: str,
    file_name: str,
) -> ExtractionResult:
    """
    Long-audio path: Silero VAD segments the audio into separate utterances,
    each segment is transcribed with Deepgram and run through the LLM
    extractor independently. All transactions are merged into one result.

    This lets an owner send a 3-minute recap voice memo covering multiple
    customers/products and have each become its own BusinessEvent, instead
    of the LLM trying to parse everything in one shot and missing details.
    """
    import asyncio
    from services.llm.media_processor import segment_voice_memo, transcribe_segments

    # Step 1: segment with Silero VAD
    segments = segment_voice_memo(audio_bytes, mime_type)
    if not segments:
        # VAD failed or found no speech — fall back to single-pass
        logger.warning("audio_vad_no_segments_fallback_single_pass", extra={"file": file_name})
        # Recursively call extract_from_audio but force the duration check to
        # take the short path. We do this by transcribing the whole thing
        # directly here, avoiding infinite recursion.
        loop = asyncio.get_event_loop()
        transcript = loop.run_until_complete(
            transcribe_audio_bytes(audio_bytes, mime_type)
        ) if not loop.is_running() else _run_async_in_thread(
            transcribe_audio_bytes(audio_bytes, mime_type)
        )
        transcript = (transcript or "").strip()
        if not transcript:
            return ExtractionResult(
                input_type="audio",
                upload_session_id=session_id,
                transactions=[],
                confidence=0.0,
                summary="Audio transcription returned empty text (VAD fallback).",
            )
        user_prompt = (
            "The following is a transcript of a voice note from a small business owner. "
            "The speaker may use Luganda, Swahili, English or a mix. "
            "Extract all business events.\n\n"
            f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---"
        )
        result = _run_text_extraction(user_prompt)
        result = _apply_default_confidence(result)
        result.input_type = "audio"
        result.upload_session_id = session_id
        result.raw_transcript = transcript
        result.transactions = _attach_source_traces(
            result.transactions, session_id, file_name, mime_type, transcript
        )
        return result

    # Step 2: transcribe each segment with Deepgram (parallel)
    loop = asyncio.get_event_loop()
    if loop.is_running():
        segments = _run_async_in_thread(transcribe_segments(segments, mime_type))
    else:
        segments = loop.run_until_complete(transcribe_segments(segments, mime_type))

    # Filter out segments with empty transcripts
    valid_segments = [s for s in segments if s.transcript.strip()]
    if not valid_segments:
        return ExtractionResult(
            input_type="audio",
            upload_session_id=session_id,
            transactions=[],
            confidence=0.0,
            raw_transcript="",
            summary="All voice segments transcribed as empty.",
        )

    # Step 3: extract events per-segment (synchronous — uses the OpenAI client)
    all_transactions = []
    segment_transcripts = []
    for idx, seg in enumerate(valid_segments):
        seg_text = seg.transcript.strip()
        segment_transcripts.append(seg_text)
        logger.info(
            "audio_segment_extracting",
            extra={"segment": idx, "start_ms": seg.start_ms, "end_ms": seg.end_ms, "chars": len(seg_text)},
        )
        user_prompt = (
            "The following is a transcript of ONE utterance from a small business owner's "
            "voice memo. The speaker may use Luganda, Swahili, English or a mix. "
            f"This is segment {idx + 1} of {len(valid_segments)}. "
            "Extract the business event(s) described in this segment only — do not "
            "invent events from earlier or later segments.\n\n"
            f"--- SEGMENT TRANSCRIPT ---\n{seg_text}\n--- END SEGMENT ---"
        )
        seg_result = _run_text_extraction(user_prompt)
        seg_result = _apply_default_confidence(seg_result)
        # Tag each transaction with which segment it came from
        for txn in seg_result.transactions:
            if not txn.notes:
                txn.notes = f"voice segment {idx + 1}/{len(valid_segments)} ({seg.start_ms//1000}s–{seg.end_ms//1000}s)"
            else:
                txn.notes = f"{txn.notes} [segment {idx + 1}/{len(valid_segments)}]"
            all_transactions.append(txn)

    # Step 4: build merged result
    full_transcript = "\n\n---\n\n".join(segment_transcripts)
    merged = ExtractionResult(
        input_type="audio",
        upload_session_id=session_id,
        transactions=all_transactions,
        confidence=_mean_confidence_or_default(all_transactions),
        raw_transcript=full_transcript,
        summary=f"Extracted {len(all_transactions)} event(s) from {len(valid_segments)} voice segment(s).",
    )
    merged.transactions = _attach_source_traces(
        merged.transactions, session_id, file_name, mime_type, full_transcript
    )
    return merged


def _run_async_in_thread(coro):
    """Run an async coroutine from within a sync context that's already
    inside a running loop. Spawns a fresh event loop in a worker thread.
    """
    import asyncio, threading
    box = {}
    def _run():
        try:
            new_loop = asyncio.new_event_loop()
            try:
                box["result"] = new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        except Exception as exc:
            box["err"] = exc
    t = threading.Thread(target=_run)
    t.start()
    t.join()
    if "err" in box:
        raise box["err"]
    return box.get("result")


def _mean_confidence_or_default(transactions: list) -> float:
    """Compute mean confidence across a list of transactions, return 0.0 if empty."""
    if not transactions:
        return 0.0
    scores = [getattr(t, "field_confidence", None) for t in transactions]
    valid = [s.mean for s in scores if s is not None and hasattr(s, "mean")]
    return round(sum(valid) / len(valid), 3) if valid else 0.0
