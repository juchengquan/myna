from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from mcp.server.auth.provider import TokenVerifier

from myna import __version__
from myna.api import router as api_router
from myna.api.auth import require_metrics
from myna.config import get_settings
from myna.keys.migrate import check_schema
from myna.keys.routes import router as keys_router
from myna.keys.store import KeyStore, create_engine
from myna.keys.verifier import DatabaseKeyVerifier
from myna.logging_config import configure_logging, get_logger
from myna.mcp_server import build_mcp, build_mcp_app
from myna.middleware import MCPAuthMiddleware, MountPathMiddleware, RateLimitMiddleware
from myna.observability import render_metrics
from myna.rate_limit import RateLimiter
from myna.tracing import setup_tracing
from myna.verifiers import ChainVerifier, StaticKeyVerifier, VerifierUnavailable


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("myna")

    tracer_provider = setup_tracing(settings)

    mcp = build_mcp()
    mcp_app = build_mcp_app(mcp)

    verifier: TokenVerifier = StaticKeyVerifier(settings.mcp_api_keys)
    key_store: KeyStore | None = None
    if settings.database_url:
        key_store = KeyStore(
            create_engine(settings.database_url), max_active_keys=settings.max_active_keys
        )
        verifier = ChainVerifier(verifier, DatabaseKeyVerifier(key_store))

    allow_anonymous = settings.env == "development" and not (
        settings.mcp_api_keys or settings.database_url
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info(
            "startup",
            env=settings.env,
            version=__version__,
            mcp_auth="enabled" if not allow_anonymous else "anonymous",
            database_keys=key_store is not None,
            mcp_allowed_hosts=settings.mcp_allowed_hosts,
        )
        if key_store is not None and settings.database_url:
            await check_schema(key_store.engine, settings.database_url)
        async with mcp.session_manager.run():
            yield
        if key_store is not None:
            await key_store.engine.dispose()
        log.info("shutdown")

    app = FastAPI(
        title="Myna",
        version=__version__,
        description="MCP service backend (FastAPI + official MCP SDK)",
        lifespan=lifespan,
    )
    app.state.mcp = mcp
    app.state.key_store = key_store

    if tracer_provider is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=tracer_provider,
            excluded_urls="api/health,metrics",
        )

    app.include_router(api_router)
    if key_store is not None and settings.key_admin_api_key:
        app.include_router(keys_router, prefix="/api")
    app.mount(settings.mcp_mount_path, mcp_app)

    # Middleware stack on /mcp. Starlette runs the last `add_middleware`
    # call first on the way in, so the order below produces:
    #   request -> MCPAuthMiddleware (sets current_caller)
    #           -> RateLimitMiddleware (reads current_caller)
    #           -> MountPathMiddleware (serves /mcp without a redirect)
    #           -> mounted MCPServer app
    rate_limiter = RateLimiter(settings.mcp_rate_limit_per_minute)
    app.state.rate_limiter = rate_limiter
    app.add_middleware(MountPathMiddleware, prefix=settings.mcp_mount_path)
    app.add_middleware(
        RateLimitMiddleware,
        prefix=settings.mcp_mount_path,
        limiter=rate_limiter,
    )
    app.add_middleware(
        MCPAuthMiddleware,
        prefix=settings.mcp_mount_path,
        verifier=verifier,
        allow_anonymous=allow_anonymous,
        failure_limiter=rate_limiter,
    )

    @app.get("/metrics", include_in_schema=False, dependencies=[Depends(require_metrics)])
    async def metrics() -> Response:
        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)

    @app.exception_handler(VerifierUnavailable)
    async def verifier_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
        log.warning("key_store_unavailable", path=request.url.path)
        return JSONResponse(status_code=503, content={"detail": "key_store_unavailable"})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(status_code=500, content={"detail": "internal_server_error"})

    return app


app = create_app()
