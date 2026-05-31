"""
SenteFlow — Application Factory
==================================
Creates and configures the FastAPI app.
Separating this from main.py makes testing easier.
"""

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.errors import (
    HTTPException,
    correlation_middleware,
    http_exception_handler,
    unhandled_exception_handler,
    StructuredLogger,
)

logger = StructuredLogger(__name__)


def create_app(deps=None) -> FastAPI:
    app = FastAPI(
        title="SenteFlow AI",
        description="WhatsApp-native AI business memory assistant for SMEs",
        version="5.0.0",
    )

    # CORS: read from ALLOWED_ORIGINS env var (comma-separated).
    # Falls back to localhost only — never wildcard in production.
    _raw_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
    _allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )
    app.middleware("http")(correlation_middleware)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Register routers
    from api.routes.whatsapp import whatsapp_router, set_whatsapp_dependencies
    from api.routes.customers import customers_router
    from api.routes.insights import insights_router
    from api.routes.orders import orders_router
    from api.routes.tasks import tasks_router
    from api.routes import (
        health_router,
        extract_router,
        approve_router,
        transaction_router,
        summary_router,
        audit_router,
        live_router,
        assistant_router,
        set_repository,
    )
    
    if deps:
        set_whatsapp_dependencies(
            wa_client=deps.wa_client,
            message_router=deps.message_router(),
        )
        set_repository(deps.repo)

    app.include_router(health_router)
    app.include_router(extract_router)
    app.include_router(approve_router)
    app.include_router(transaction_router)
    app.include_router(summary_router)
    app.include_router(audit_router)
    app.include_router(live_router)
    app.include_router(assistant_router)
    app.include_router(whatsapp_router)
    app.include_router(customers_router)
    app.include_router(insights_router)
    app.include_router(orders_router)
    app.include_router(tasks_router)

    return app