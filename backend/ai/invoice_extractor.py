"""
SenteFlow AI — Invoice Extraction Module
=========================================
Dedicated AI extraction for invoice images (supplier invoices, receipts,
mobile money screenshots, utility bills). Separate from the general SME
extractor because invoice structure differs significantly from voice/text records.
"""

import logging

import os
from typing import Optional

from pydantic import BaseModel, Field
from google import genai
from google.genai.types import GenerateContentConfig, Part
from utils.clock import utc_now

# ACTIVE_INVOICE_EXTRACTION removed — define inline or in prompts/ module
ACTIVE_INVOICE_EXTRACTION = "Extract invoice details from the following text."

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
MODEL_ID = "gemini-2.0-flash"


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
    image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)

    base_instruction = (
        "Extract ALL structured invoice data from this image. "
        "Read every field carefully including line items, amounts, tax, and payment details."
    )
    if custom_prompt:
        base_instruction += f"\n\nAdditional focus: {custom_prompt}"

    response = _client.models.generate_content(
        model=MODEL_ID,
        contents=[base_instruction, image_part],
        config=GenerateContentConfig(
            system_instruction=ACTIVE_INVOICE_EXTRACTION,
            response_schema=InvoiceData,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )

    logger.info(
        f"Invoice extracted: {response.parsed.vendor_name} | "
        f"{response.parsed.total_amount} {response.parsed.currency} | "
        f"lang={response.parsed.language_detected}"
    )
    return response.parsed


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
