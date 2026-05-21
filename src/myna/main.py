from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from myna import __version__
from myna.api import router as api_router
from myna.config import get_settings
from myna.logging_config import configure_logging, get_logger
from myna.mcp_server import build_mcp
from myna.middleware import MCPAuthMiddleware, RateLimitMiddleware
from myna.observability import render_metrics
from myna.rate_limit import RateLimiter
from myna.tracing import setup_tracing


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("myna")

    tracer_provider = setup_tracing(settings)

    mcp = build_mcp()
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info(
            "startup",
            env=settings.env,
            version=__version__,
            mcp_auth="enabled" if settings.mcp_api_keys else "anonymous",
        )
        async with mcp.session_manager.run():
            yield
        log.info("shutdown")

    app = FastAPI(
        title="Myna",
        version=__version__,
        description="MCP service backend (FastAPI + official MCP SDK)",
        lifespan=lifespan,
    )
    app.state.mcp = mcp

    if tracer_provider is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=tracer_provider,
            excluded_urls="api/health,metrics",
        )

    app.include_router(api_router)
    app.mount(settings.mcp_mount_path, mcp_app)

    # Middleware stack on /mcp. Starlette runs the last `add_middleware`
    # call first on the way in, so the order below produces:
    #   request -> MCPAuthMiddleware (sets current_caller)
    #           -> RateLimitMiddleware (reads current_caller)
    #           -> mounted FastMCP app
    rate_limiter = RateLimiter(settings.mcp_rate_limit_per_minute)
    app.state.rate_limiter = rate_limiter
    app.add_middleware(
        RateLimitMiddleware,
        prefix=settings.mcp_mount_path,
        limiter=rate_limiter,
    )
    app.add_middleware(
        MCPAuthMiddleware,
        prefix=settings.mcp_mount_path,
        api_keys=settings.mcp_api_keys,
        allow_anonymous=settings.env == "development",
    )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(status_code=500, content={"detail": "internal_server_error"})

    return app


app = create_app()
