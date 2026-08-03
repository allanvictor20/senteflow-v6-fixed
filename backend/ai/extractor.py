"""
SenteFlow AI — AI Extraction Layer
====================================
Responsibility: Call Gemini and return raw ExtractionResult objects.
This layer does NOT validate, persist, or apply business logic.
It only converts raw files/bytes into structured domain objects.

Separation of concerns:
  AI extraction  → here (identify facts)
  Validation     → validators/transaction_validator.py (enforce rules)
  Persistence    → repositories/transaction_repository.py (save to Firestore)
"""

import logging
import mimetypes
import os
import uuid
from typing import Optional

from google import genai
from google.genai.types import GenerateContentConfig, Part

from domain.models import (
    ExtractionResult,
    FieldConfidence,
    SourceTrace,
    Transaction,
)
from prompts.extraction_prompts import (
    ACTIVE_EXTRACTION,
    ACTIVE_EXTRACTION_PROMPT_VERSION,
)

logger = logging.getLogger(__name__)

MODEL_ID = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# The client is built on first use, not at import time: constructing it eagerly
# makes this module unimportable whenever GEMINI_API_KEY is absent, which breaks
# test collection and any process that only needs the helpers below.
_client_instance: Optional["genai.Client"] = None


def _client_factory() -> "genai.Client":
    global _client_instance
    if _client_instance is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set — cannot run AI extraction")
        _client_instance = genai.Client(api_key=api_key)
    return _client_instance


class _LazyClient:
    """Proxy so existing `_client.models.generate_content(...)` calls still work."""

    def __getattr__(self, name):
        return getattr(_client_factory(), name)


_client = _LazyClient()


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
    response = _client.models.generate_content(
        model=MODEL_ID,
        contents=[f"Extract all business events from this message:\n\n{text_content}"],
        config=GenerateContentConfig(
            system_instruction=ACTIVE_EXTRACTION,
            response_schema=ExtractionResult,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    result = _apply_default_confidence(response.parsed)
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
    image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)
    response = _client.models.generate_content(
        model=MODEL_ID,
        contents=[
            "Extract all business events from this image. "
            "This may be a MoMo screenshot, receipt, or handwritten ledger. "
            "Include confidence scores.",
            image_part,
        ],
        config=GenerateContentConfig(
            system_instruction=ACTIVE_EXTRACTION,
            response_schema=ExtractionResult,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    result = _apply_default_confidence(response.parsed)
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
    pdf_part = Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    response = _client.models.generate_content(
        model=MODEL_ID,
        contents=[
            "Extract all business events and member contribution records from this document.",
            pdf_part,
        ],
        config=GenerateContentConfig(
            system_instruction=ACTIVE_EXTRACTION,
            response_schema=ExtractionResult,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    result = _apply_default_confidence(response.parsed)
    result.upload_session_id = session_id
    result.transactions = _attach_source_traces(
        result.transactions, session_id, file_name, "application/pdf"
    )
    return result


def extract_from_audio(
    audio_bytes: bytes,
    mime_type: str,
    session_id: str,
    file_name: str,
) -> ExtractionResult:
    audio_part = Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    response = _client.models.generate_content(
        model=MODEL_ID,
        contents=[
            """First transcribe this audio completely, then extract all business events mentioned.
            The speaker may use Luganda, Swahili, English or a mix.
            Pay special attention to member names, contribution amounts, and the period being paid for.
            Include confidence scores for each field.""",
            audio_part,
        ],
        config=GenerateContentConfig(
            system_instruction=ACTIVE_EXTRACTION,
            response_schema=ExtractionResult,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    result = _apply_default_confidence(response.parsed)
    result.upload_session_id = session_id
    result.transactions = _attach_source_traces(
        result.transactions, session_id, file_name, mime_type, result.raw_transcript
    )
    return result
