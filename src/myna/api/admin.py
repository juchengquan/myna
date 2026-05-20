from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from myna.config import Settings, get_settings

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None),
) -> None:
    if settings.admin_api_key is None:
        if settings.env != "development":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="admin_api_key not configured",
            )
        return

    expected = f"Bearer {settings.admin_api_key}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_mcp_from_request(request: Request) -> FastMCP:
    mcp = getattr(request.app.state, "mcp", None)
    if mcp is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="MCP server not initialized",
        )
    return mcp


class ToolInfo(BaseModel):
    name: str
    description: str | None = None


class ToolsResponse(BaseModel):
    tools: list[ToolInfo]


@router.get(
    "/tools",
    response_model=ToolsResponse,
    dependencies=[Depends(require_admin)],
)
async def list_tools(mcp: FastMCP = Depends(get_mcp_from_request)) -> ToolsResponse:
    tools = await mcp.list_tools()
    return ToolsResponse(
        tools=[ToolInfo(name=t.name, description=t.description) for t in tools]
    )
