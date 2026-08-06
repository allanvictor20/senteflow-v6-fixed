"""
SenteFlow AI — API Routes
==========================
Thin HTTP layer. Routes do not contain business logic.
They delegate to workflows and repositories.

Route responsibilities:
  1. Parse request
  2. Call workflow / repository
  3. Return response

Nothing else.
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.auth import verify_firebase_token, verify_org_access
from core.errors import SenteFlowError, StructuredLogger, success_response
from workflows.media_extraction_workflow import run_media_extraction
from repositories.transaction_repository import TransactionRepository

# NOTE: The legacy /ws/live WebSocket route (which used Google's Gemini Live
# realtime voice API) has been removed. Live voice chat is not currently
# exposed as a separate feature — voice interaction happens via WhatsApp
# voice notes (transcribed by Deepgram, replies synthesized by ElevenLabs).
# The old GeminiLive module (services/llm/gemini_live.py) is preserved as
# legacy reference but is no longer reachable from any active route.

logger = StructuredLogger(__name__)


# ─── Dependency: repository instance ────────────────────────────────────────
# Injected at app startup (see main.py)
_repo: TransactionRepository = None


def set_repository(repo: TransactionRepository):
    global _repo
    _repo = repo


def _require_repo() -> TransactionRepository:
    """Fail with a clear 503 instead of an AttributeError on `None`."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not initialised")
    return _repo


# ─── Routers ─────────────────────────────────────────────────────────────────

health_router = APIRouter()
extract_router = APIRouter()
approve_router = APIRouter()
transaction_router = APIRouter()
summary_router = APIRouter()
audit_router = APIRouter()
live_router = APIRouter()
assistant_router = APIRouter()


# ─── Health ───────────────────────────────────────────────────────────────────

@health_router.get("/health")
def health():
    return {"status": "ok", "service": "SenteFlow AI", "version": "3.0.0"}


# ─── Extract ──────────────────────────────────────────────────────────────────

@extract_router.post("/extract")
async def extract(
    file: UploadFile = File(...),
    org_id: str = Form(...),
    uploaded_by: str = Form(...),
    invoice_prompt: str = Form(None),
    _token: dict = Depends(verify_firebase_token),
):

    """
    Multimodal extraction endpoint.
    Runs AI extraction → deterministic validation → returns structured transactions.
    Does NOT save to Firestore — awaits human approval.
    """
    logger.info("extract_request", file_name=file.filename, org_id=org_id, uploaded_by=uploaded_by)

    suffix = os.path.splitext(file.filename or "upload")[1] or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        file_content = await file.read()
        tmp.write(file_content)
        tmp_path = tmp.name

    try:
        # run_media_extraction blocks on the AI client; keep it off the loop.
        result, session_id = await asyncio.to_thread(
            run_media_extraction, tmp_path, file.filename or "upload", invoice_prompt
        )
        logger.info(
            "extract_complete",
            session_id=session_id,
            transaction_count=len(result.transactions),
            input_type=result.input_type,
            anomaly_count=len(result.anomalies),
        )

        transactions_out = []
        for txn in result.transactions:
            txn_dict = txn.model_dump()
            if txn.field_confidence:
                txn_dict["confidence_label"] = txn.field_confidence.label
                txn_dict["confidence_score"] = round(txn.field_confidence.overall, 2)
                txn_dict["confidence_color"] = txn.field_confidence.color
            transactions_out.append(txn_dict)

        return success_response({
            "session_id": session_id,
            "input_type": result.input_type,
            "language": result.language_detected,
            "summary": result.summary,
            "anomalies": result.anomalies,
            "raw_transcript": result.raw_transcript,
            "transactions": transactions_out,
            "count": len(result.transactions),
        })

    except SenteFlowError as e:
        logger.error("extract_failed", exc=e, file_name=file.filename)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("extract_unexpected_error", exc=e, file_name=file.filename)
        raise HTTPException(status_code=500, detail="Extraction failed. Please try again.")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ─── Approve ─────────────────────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    transactions: list[dict]
    org_id: str
    approved_by: str
    session_id: str = ""


class AssistantQueryRequest(BaseModel):
    org_id: str
    question: str
    sender_id: str = ""


@approve_router.post("/approve")
async def approve_transactions(req: ApprovalRequest, _token: dict = Depends(verify_firebase_token)):
    """
    Human approval endpoint. Saves approved transactions with full audit metadata.
    Checks for cross-session duplicates before saving.
    """
    _require_repo()
    logger.info("approve_request", count=len(req.transactions), org_id=req.org_id, approved_by=req.approved_by)

    saved_ids, skipped = _repo.save_approved_batch(
        req.transactions, req.org_id, req.approved_by, req.session_id
    )

    logger.info("approve_complete", saved=len(saved_ids), duplicates_skipped=len(skipped))

    return success_response({
        "saved_count": len(saved_ids),
        "ids": saved_ids,
        "duplicates_skipped": len(skipped),
    })


# ─── Transactions ────────────────────────────────────────────────────────────
# The repository is synchronous — these handlers stay `async def` so FastAPI
# keeps them on the event loop, but they must not `await` the repo calls.

@transaction_router.get("/transactions/{org_id}")
async def get_transactions(org_id: str, status: str = None, limit: int = 100, _: dict = Depends(verify_org_access)):
    _require_repo()
    transactions = _repo.list_transactions(org_id, status=status, limit=limit)
    return success_response({"transactions": transactions, "count": len(transactions)})


@transaction_router.get("/pending/{org_id}")
async def get_pending(org_id: str, _: dict = Depends(verify_org_access)):
    _require_repo()
    transactions = _repo.list_transactions(org_id, status="pending")
    return success_response({"transactions": transactions, "count": len(transactions)})


# ─── Summary ──────────────────────────────────────────────────────────────────

@summary_router.get("/summary/{org_id}")
async def get_summary(org_id: str, _: dict = Depends(verify_org_access)):
    _require_repo()
    summary = _repo.compute_financial_summary(org_id)
    return success_response(summary.model_dump())


@assistant_router.post("/assistant/query")
async def assistant_query(
    req: AssistantQueryRequest,
    _token: dict = Depends(verify_firebase_token),
):
    from core.auth import ensure_org_access
    from services.assistant.business_assistant import BusinessAssistant

    # org_id arrives in the body here, so the org check has to be explicit —
    # the Depends(verify_org_access) form only reads query/path parameters.
    ensure_org_access(_token, req.org_id)
    _require_repo()

    assistant = BusinessAssistant(_repo, req.org_id)
    answer = assistant.answer(req.question, req.sender_id or None)
    return success_response({
        "intent": answer.intent,
        "answer": answer.answer,
        "data": answer.data,
    })


@assistant_router.get("/customers/{org_id}")
async def get_customers(org_id: str, limit: int = 100, _: dict = Depends(verify_org_access)):
    _require_repo()
    customers = _repo.list_customers(org_id, limit=limit)
    return success_response({"customers": customers, "count": len(customers)})


@assistant_router.get("/orders/{org_id}")
async def get_orders(
    org_id: str,
    status: str = None,
    payment_status: str = None,
    delivery_status: str = None,
    limit: int = 100,
    _: dict = Depends(verify_org_access),
):
    _require_repo()
    orders = _repo.list_orders(
        org_id,
        status=status,
        payment_status=payment_status,
        delivery_status=delivery_status,
        limit=limit,
    )
    return success_response({"orders": orders, "count": len(orders)})


@assistant_router.get("/media-assets/{org_id}")
async def get_media_assets(org_id: str, limit: int = 50, _: dict = Depends(verify_org_access)):
    _require_repo()
    assets = _repo.list_media_assets(org_id, limit=limit)
    return success_response({"media_assets": assets, "count": len(assets)})


# ─── Audit ────────────────────────────────────────────────────────────────────

@audit_router.get("/audit/{org_id}/{transaction_id}")
async def get_transaction_evidence(org_id: str, transaction_id: str, _: dict = Depends(verify_org_access)):
    _require_repo()
    data = _repo.get_transaction(org_id, transaction_id)
    if not data:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return success_response({
        "transaction": data,
        "source_trace": data.get("source_trace"),
        "confidence_details": data.get("confidence_details"),
        "anomalies": data.get("anomalies", []),
    })


# ─── Legacy live voice WebSocket (deprecated) ─────────────────────────────────

@live_router.websocket("/ws/live")
async def legacy_live_voice_endpoint(websocket: WebSocket):
    """Deprecated — the legacy Gemini Live WebSocket has been removed.

    Returns a single error message and closes. Voice interaction now happens
    via WhatsApp voice notes: the bot transcribes inbound notes with Deepgram
    and replies with synthesized voice notes via ElevenLabs.
    """
    await websocket.accept()
    await websocket.send_json({
        "type": "deprecated",
        "error": (
            "The /ws/live Gemini Live WebSocket has been removed. "
            "Voice interaction now happens via WhatsApp voice notes. "
            "See README.md > Voice Notes."
        ),
    })
    await websocket.close()