"""
SenteFlow AI — Invoice Extraction Module (Groq edition)
=========================================================
Dedicated AI extraction for invoice images (supplier invoices, receipts,
mobile money screenshots, utility bills). Separate from the general SME
extractor because invoice structure differs significantly from voice/text records.

Groq migration notes:
  - Uses Groq's vision model (default: llama-3.2-90b-vision-preview).
  - Switched from Gemini's `response_schema=Pydantic` to JSON mode
    (`response_format={"type": "json_object"}`) + Pydantic parsing.
  - The InvoiceData schema description is embedded into the prompt.
"""

import base64
import json
import logging

import os
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel, Field
from utils.clock import utc_now

# ACTIVE_INVOICE_EXTRACTION removed — define inline or in prompts/ module
ACTIVE_INVOICE_EXTRACTION = "Extract invoice details from the following text."

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "llama-3.2-90b-vision-preview")
MODEL_ID = DEFAULT_GROQ_VISION_MODEL

# The client is built on first use — same pattern as ai/extractor.py
_client_instance: Optional[OpenAI] = None


def _client() -> OpenAI:
    global _client_instance
    if _client_instance is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set — cannot run invoice extraction")
        _client_instance = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
    return _client_instance


# ─── Invoice Schemas ──────────────────────────────────────────────────────────

class InvoiceLineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    total: Optional[float] = None
    unit: Optional[str] = None


class InvoiceData(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    vendor_phone: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_address: Optional[str] = None
    currency: str = Field("UGX")
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    tax_rate: Optional[str] = None
    discount: Optional[float] = None
    total_amount: float = Field(...)
    payment_method: Optional[str] = None
    payment_status: Optional[str] = None
    mobile_money_ref: Optional[str] = None
    line_items: list[InvoiceLineItem] = Field(default=[])
    invoice_type: str = Field("supplier_invoice")
    language_detected: str = Field("en")
    notes: Optional[str] = None
    confidence: str = Field("high")


# Schema hint embedded into the system prompt — Groq does not support
# Gemini-style `response_schema=Pydantic`, so we describe the JSON shape
# explicitly and parse the model's JSON-mode output with Pydantic.
_INVOICE_SCHEMA_HINT = """
Return a JSON object with EXACTLY this shape:
{
  "invoice_number": null,
  "invoice_date": null,
  "due_date": null,
  "vendor_name": null,
  "vendor_address": null,
  "vendor_phone": null,
  "vendor_tax_id": null,
  "buyer_name": null,
  "buyer_address": null,
  "currency": "UGX",
  "subtotal": null,
  "tax_amount": null,
  "tax_rate": null,
  "discount": null,
  "total_amount": 0.0,
  "payment_method": null,
  "payment_status": null,
  "mobile_money_ref": null,
  "line_items": [
    {"description": "", "quantity": null, "unit_price": null, "total": null, "unit": null}
  ],
  "invoice_type": "supplier_invoice",
  "language_detected": "en",
  "notes": null,
  "confidence": "high"
}
- Use null for any field that cannot be determined.
- `total_amount` is required.
- `line_items` may be an empty array if no individual lines are visible.
- Only return valid JSON. No markdown fences. No commentary outside the JSON.
"""


# ─── Invoice Extractor ────────────────────────────────────────────────────────

def extract_invoice(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    custom_prompt: Optional[str] = None,
) -> InvoiceData:
    """
    Extract structured invoice data from an image.
    Returns a fully structured InvoiceData object.
    """
    client = _client()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

    base_instruction = (
        "Extract ALL structured invoice data from this image. "
        "Read every field carefully including line items, amounts, tax, and payment details."
    )
    if custom_prompt:
        base_instruction += f"\n\nAdditional focus: {custom_prompt}"

    response = client.chat.completions.create(
        model=DEFAULT_GROQ_VISION_MODEL,
        temperature=0.0,
        max_tokens=2500,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": ACTIVE_INVOICE_EXTRACTION + "\n\n" + _INVOICE_SCHEMA_HINT,
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": base_instruction},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    )

    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

    parsed = InvoiceData.model_validate_json(raw)

    logger.info(
        f"Invoice extracted: {parsed.vendor_name} | "
        f"{parsed.total_amount} {parsed.currency} | "
        f"lang={parsed.language_detected}"
    )
    return parsed


# ─── Invoice → Transactions Converter ────────────────────────────────────────

def invoice_to_transactions(invoice: InvoiceData) -> list[dict]:
    """
    Convert an InvoiceData into a list of raw transaction dicts.
    Returns one primary transaction + optional per-line-item transactions.
    These are passed to the validation layer before use.
    """
    from datetime import datetime
    now = utc_now().isoformat()

    txn_type_map = {
        "supplier_invoice": "expense",
        "sales_invoice":    "income",
        "receipt":          "expense",
        "mobile_money_receipt": "expense",
        "utility_bill":     "expense",
        "tax_invoice":      "expense",
        "other":            "expense",
    }
    txn_type = txn_type_map.get(invoice.invoice_type, "expense")

    docs = []

    # Primary transaction (invoice total)
    notes_parts = [
        f"Payment: {invoice.payment_method or 'unknown'}",
        f"Status: {invoice.payment_status or 'unknown'}",
    ]
    if invoice.mobile_money_ref:
        notes_parts.append(f"MoMo ref: {invoice.mobile_money_ref}")
    if invoice.tax_amount:
        notes_parts.append(f"Tax: {invoice.tax_amount} ({invoice.tax_rate})")
    if invoice.notes:
        notes_parts.append(invoice.notes)

    docs.append({
        "amount":           invoice.total_amount,
        "currency":         invoice.currency,
        "type":             txn_type,
        "category":         invoice.invoice_type,
        "description":      f"Invoice from {invoice.vendor_name or 'Unknown vendor'}",
        "date":             invoice.invoice_date,
        "payer":            invoice.buyer_name,
        "payee":            invoice.vendor_name,
        "reference":        invoice.invoice_number,
        "notes":            " | ".join(notes_parts),
        "is_invoice_total": True,
    })

    # Per-line-item transactions (only when amounts present)
    for item in invoice.line_items:
        if item.total and item.total > 0:
            docs.append({
                "amount":           item.total,
                "currency":         invoice.currency,
                "type":             txn_type,
                "category":         "line_item",
                "description":      item.description,
                "date":             invoice.invoice_date,
                "payer":            invoice.buyer_name,
                "payee":            invoice.vendor_name,
                "reference":        invoice.invoice_number,
                "notes":            f"Qty: {item.quantity} {item.unit or ''} @ {invoice.currency} {item.unit_price or 'N/A'} each".strip(),
                "is_invoice_total": False,
            })

    return docs
