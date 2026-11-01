from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from myna.api.auth import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def get_mcp_from_request(request: Request) -> FastMCP:
    mcp: FastMCP | None = getattr(request.app.state, "mcp", None)
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


class ResourceInfo(BaseModel):
    name: str
    uri: str
    description: str | None = None
    mime_type: str | None = None
    is_template: bool = False


class ResourcesResponse(BaseModel):
    resources: list[ResourceInfo]


class PromptArgumentInfo(BaseModel):
    name: str
    description: str | None = None
    required: bool = False


class PromptInfo(BaseModel):
    name: str
    description: str | None = None
    arguments: list[PromptArgumentInfo] = []


class PromptsResponse(BaseModel):
    prompts: list[PromptInfo]


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


@router.get(
    "/resources",
    response_model=ResourcesResponse,
    dependencies=[Depends(require_admin)],
)
async def list_resources(
    mcp: FastMCP = Depends(get_mcp_from_request),
) -> ResourcesResponse:
    statics = await mcp.list_resources()
    templates = await mcp.list_resource_templates()
    items: list[ResourceInfo] = [
        ResourceInfo(
            name=r.name or str(r.uri),
            uri=str(r.uri),
            description=r.description,
            mime_type=r.mimeType,
            is_template=False,
        )
        for r in statics
    ]
    items.extend(
        ResourceInfo(
            name=t.name or t.uriTemplate,
            uri=t.uriTemplate,
            description=t.description,
            mime_type=t.mimeType,
            is_template=True,
        )
        for t in templates
    )
    return ResourcesResponse(resources=items)


@router.get(
    "/prompts",
    response_model=PromptsResponse,
    dependencies=[Depends(require_admin)],
)
async def list_prompts(
    mcp: FastMCP = Depends(get_mcp_from_request),
) -> PromptsResponse:
    prompts = await mcp.list_prompts()
    return PromptsResponse(
        prompts=[
            PromptInfo(
                name=p.name,
                description=p.description,
                arguments=[
                    PromptArgumentInfo(
                        name=a.name,
                        description=a.description,
                        required=bool(a.required),
                    )
                    for a in (p.arguments or [])
                ],
            )
            for p in prompts
        ]
    )
