"""
SenteFlow AI — Error Handling & Structured Logging
====================================================
Centralized exception handling, correlation IDs, structured JSON logging,
retry logic for AI calls, and typed API response envelope.
"""

import functools
import logging
import time
import traceback
import uuid
from contextvars import ContextVar
from typing import Any, Optional, TypeVar, Callable

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ─── Correlation ID Context ────────────────────────────────────────────────────
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    return _correlation_id.get() or "no-corr-id"


def set_correlation_id(cid: str):
    _correlation_id.set(cid)


# ─── Structured JSON Logger ────────────────────────────────────────────────────

class StructuredLogger:
    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _log(self, level: int, event: str, **fields):
        extra = {
            "correlation_id": get_correlation_id(),
            "service": "senteflow-api",
            **fields,
        }
        field_str = " ".join(f"{k}={v!r}" for k, v in extra.items())
        self._logger.log(level, f"{event} | {field_str}")

    def info(self, event: str, **fields):
        self._log(logging.INFO, event, **fields)

    def warning(self, event: str, **fields):
        self._log(logging.WARNING, event, **fields)

    def error(self, event: str, exc: Optional[Exception] = None, **fields):
        if exc:
            fields["error"] = str(exc)
            fields["traceback"] = traceback.format_exc()
        self._log(logging.ERROR, event, **fields)

    def debug(self, event: str, **fields):
        self._log(logging.DEBUG, event, **fields)
    
    def timer(self, event: str, **fields):
        from contextlib import contextmanager
        from time import perf_counter

        @contextmanager
        def _timer():
            t = perf_counter()
            try:
                yield
            finally:
                self.info(event, duration_ms=round((perf_counter() - t) * 1000), **fields)

        return _timer()


# ─── API Response Envelope ─────────────────────────────────────────────────────

class APIResponse(BaseModel):
    success: bool
    correlation_id: str
    data: Optional[Any] = None
    error: Optional[str] = None
    error_code: Optional[str] = None


def success_response(data: Any) -> dict:
    return APIResponse(
        success=True,
        correlation_id=get_correlation_id(),
        data=data,
    ).model_dump()


def error_response(message: str, code: str = "INTERNAL_ERROR") -> dict:
    return APIResponse(
        success=False,
        correlation_id=get_correlation_id(),
        error=message,
        error_code=code,
    ).model_dump()


# ─── Exception Handlers ───────────────────────────────────────────────────────

async def http_exception_handler(request: Request, exc: HTTPException):
    logger = StructuredLogger("error_handler")
    logger.warning(
        "http_exception",
        path=str(request.url),
        status_code=exc.status_code,
        detail=exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(str(exc.detail), f"HTTP_{exc.status_code}"),
    )


async def unhandled_exception_handler(request: Request, exc: Exception):
    logger = StructuredLogger("error_handler")
    logger.error(
        "unhandled_exception",
        exc=exc,
        path=str(request.url),
        method=request.method,
    )
    return JSONResponse(
        status_code=500,
        content=error_response(
            "An unexpected error occurred. Please try again.",
            "INTERNAL_ERROR",
        ),
    )


# ─── Request Correlation Middleware ───────────────────────────────────────────

async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())[:8]
    set_correlation_id(cid)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = cid
    return response


# ─── AI Call Retry Decorator ──────────────────────────────────────────────────

T = TypeVar("T")


# ─── AI Call Retry Decorators ─────────────────────────────────────────────────
# Two variants:
#   with_retry        — synchronous, uses time.sleep (for sync callers only)
#   async_with_retry  — async, uses asyncio.sleep (use this for all async AI calls)

T = TypeVar("T")


def with_retry(
    max_attempts: int = 3,
    delay_seconds: float = 1.5,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple = (Exception,),
):
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            logger = StructuredLogger(func.__module__)
            delay = delay_seconds
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        logger.error("retry_exhausted", func=func.__name__, attempts=attempt, exc=e)
                        raise
                    logger.warning("retry_attempt", func=func.__name__, attempt=attempt, delay=delay, error=str(e))
                    time.sleep(delay)
                    delay *= backoff_factor
            raise last_exc
        return wrapper
    return decorator


def async_with_retry(
    max_attempts: int = 3,
    delay_seconds: float = 1.5,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple = (Exception,),
):
    """
    Async-safe retry decorator. Uses asyncio.sleep so the event loop
    stays unblocked between retry attempts. Use this on all async AI callers.

    Usage:
        @async_with_retry(max_attempts=3)
        async def call_gemini(...): ...
    """
    import asyncio as _asyncio

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            logger = StructuredLogger(func.__module__)
            delay = delay_seconds
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        logger.error("retry_exhausted", func=func.__name__, attempts=attempt, exc=e)
                        raise
                    logger.warning("retry_attempt", func=func.__name__, attempt=attempt, delay=delay, error=str(e))
                    await _asyncio.sleep(delay)
                    delay *= backoff_factor
            raise last_exc
        return wrapper
    return decorator
# ─── Domain Exception Types ──────────────────────────────────────────────────

class SenteFlowError(Exception):
    def __init__(self, message: str, code: str = "SENTEFLOW_ERROR"):
        super().__init__(message)
        self.code = code


class ExtractionError(SenteFlowError):
    def __init__(self, message: str):
        super().__init__(message, "EXTRACTION_ERROR")


class ValidationError(SenteFlowError):
    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR")


class DuplicateTransactionError(SenteFlowError):
    def __init__(self, hash_val: str):
        super().__init__(f"Duplicate transaction: {hash_val}", "DUPLICATE_TRANSACTION")
        self.hash = hash_val


class UnsupportedFileTypeError(SenteFlowError):
    def __init__(self, mime_type: str):
        super().__init__(f"Unsupported file type: {mime_type}", "UNSUPPORTED_FILE_TYPE")
