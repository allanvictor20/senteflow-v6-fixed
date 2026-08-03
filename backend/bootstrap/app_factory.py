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
    from api.routes.customers import customers_router, set_customer_dependencies
    from api.routes.insights import insights_router, set_insights_dependencies
    from api.routes.orders import orders_router, set_order_dependencies
    from api.routes.tasks import tasks_router, set_task_dependencies
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
        # Every router keeps its collaborators in module globals, so each one
        # needs an explicit hand-off here. Miss one and its endpoints return
        # "repository not initialised" for the lifetime of the process.
        set_whatsapp_dependencies(
            wa_client=deps.wa_client,
            message_router=deps.message_router(),
        )
        set_repository(deps.repo)
        set_customer_dependencies(
            profile_repo=deps.customer_profile_repo,
            customer_memory_svc=deps.customer_memory_svc,
        )
        set_order_dependencies(
            order_repo=deps.order_repo,
            order_svc=deps.order_svc,
        )
        set_task_dependencies(task_repo=deps.task_repo)
        set_insights_dependencies(
            profile_repo=deps.customer_profile_repo,
            order_repo=deps.order_repo,
            task_repo=deps.task_repo,
            conv_agg_repo=deps.conv_agg_repo,
        )
        logger.info("routers_wired", org_id=deps.org_id)
    else:
        logger.warning("app_created_without_dependencies")

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