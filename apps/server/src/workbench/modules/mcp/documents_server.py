"""Explicit acceptance server: reads real project Markdown, never writes project files."""

import argparse
import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount


def build_server(root: Path) -> Server:
    root = root.resolve(strict=True)
    state = secrets.token_urlsafe(24)
    calls = 0
    output_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "text": {"type": "string"},
            "pid": {"type": "integer"},
            "read_calls": {"type": "integer"},
            "protocol": {"type": "string"},
            "client_capabilities": {"type": "object"},
            "inherited_sensitive_names": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "path",
            "text",
            "pid",
            "read_calls",
            "protocol",
            "client_capabilities",
            "inherited_sensitive_names",
        ],
        "additionalProperties": False,
    }
    tool_names = {"read_document", "read_selected_document"}

    async def list_tools(ctx, params):
        common = {"max_lines": {"type": "integer", "minimum": 1, "maximum": 40}}
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="read_document",
                    description="读取已配置项目docs目录下的真实Markdown文档。",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            **common,
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                    output_schema=output_schema,
                    annotations=types.ToolAnnotations(read_only_hint=True),
                ),
                types.Tool(
                    name="read_selected_document",
                    description="先通过用户表单选择真实项目文档，再沿原调用读取。",
                    input_schema={
                        "type": "object",
                        "properties": common,
                        "additionalProperties": False,
                    },
                    output_schema=output_schema,
                    annotations=types.ToolAnnotations(read_only_hint=True),
                ),
            ]
        )

    async def call_tool(ctx, params):
        nonlocal calls
        if params.name not in tool_names:
            raise ValueError("Unknown document tool")
        arguments = params.arguments or {}
        path = arguments.get("path")
        if params.name == "read_selected_document":
            answer = (params.input_responses or {}).get("document")
            if not isinstance(answer, types.ElicitResult):
                return types.InputRequiredResult(
                    request_state=state,
                    input_requests={
                        "document": types.ElicitRequest(
                            params=types.ElicitRequestFormParams(
                                message="请选择要读取的真实项目docs文档相对路径。",
                                requested_schema={
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string", "maxLength": 240},
                                    },
                                    "required": ["path"],
                                    "additionalProperties": False,
                                },
                            )
                        )
                    },
                )
            if params.request_state != state:
                raise ValueError("Original process state does not match")
            if answer.action != "accept":
                return types.CallToolResult(
                    is_error=True, content=[types.TextContent(type="text", text="用户未选择文档。")]
                )
            path = (answer.content or {}).get("path")
        target = (root / "docs" / str(path)).resolve(strict=True)
        if not target.is_relative_to(root / "docs") or target.suffix != ".md":
            raise ValueError("Only configured project docs Markdown can be read")
        if target.stat().st_size > 512 * 1024:
            raise ValueError("Document exceeds acceptance size limit")
        calls += 1
        sensitive_names = sorted(
            k
            for k in os.environ
            if any(marker in k.upper() for marker in ("DATABASE", "API_KEY", "TOKEN", "SECRET"))
        )
        value = {
            "path": str(target.relative_to(root)),
            "text": "\n".join(target.read_text().splitlines()[: arguments.get("max_lines", 10)]),
            "pid": os.getpid(),
            "read_calls": calls,
            "protocol": ctx.protocol_version,
            "client_capabilities": (ctx.meta or {}).get(
                "io.modelcontextprotocol/clientCapabilities", {}
            ),
            "inherited_sensitive_names": sensitive_names,
        }
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
            structured_content=value,
        )

    return Server(
        "Workbench real project documents",
        version="1",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def build_http_app(root: Path):
    manager = StreamableHTTPSessionManager(
        build_server(root), json_response=True, stateless=True, max_request_body_size=131072
    )

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    return Starlette(routes=[Mount("/mcp", app=manager.handle_request)], lifespan=lifespan)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    server = build_server(args.root)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
