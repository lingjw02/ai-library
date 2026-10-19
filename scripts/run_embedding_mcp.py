"""Separate Agent-side embedding MCP. Does not open the knowledge database."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json
import logging
import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from scripts.embedding.service import EmbeddingService, DEFAULT_MODEL_DIR


async def serve(service):
    definitions = [
        {'name': 'embedding_status', 'description': 'Inspect the pinned local text embedding contract; does not load weights.',
         'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
        {'name': 'embed_texts', 'description': 'Locally embed exact text. Use document for source content and query for retrieval. Preserve content_hash, space and source revision when submitting vectors to the separate memory MCP. No network inference or database writes. Overlong text is rejected, never truncated.',
         'inputSchema': {'type': 'object', 'properties': {
             'texts': {'type': 'array', 'minItems': 1, 'maxItems': 16,
                       'items': {'type': 'string', 'minLength': 1, 'maxLength': 65536}},
             'role': {'type': 'string', 'enum': ['query', 'document']}},
             'required': ['texts', 'role'], 'additionalProperties': False}}]
    schemas = {d['name']: d['inputSchema'] for d in definitions}

    async def list_tools(context, params):
        return types.ListToolsResult(tools=[types.Tool.model_validate(d) for d in definitions])

    async def call_tool(context, params):
        args = params.arguments or {}
        try:
            if params.name == 'embedding_status' and not args:
                result = service.status()
            elif params.name == 'embed_texts' and set(args) == {'texts', 'role'}:
                result = await anyio.to_thread.run_sync(service.embed, args['texts'], args['role'])
            else:
                raise ValueError('Unknown tool or unsupported arguments')
            return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result))], structured_content=result)
        except (ValueError, OSError) as exc:
            return types.CallToolResult(is_error=True, content=[types.TextContent(type='text', text=str(exc))])
        except Exception:
            logging.exception('Embedding failed')
            return types.CallToolResult(is_error=True, content=[types.TextContent(type='text', text='Embedding failed; check local service diagnostics. No vector submitted.')])

    server = Server('Local EmbeddingGemma 2', version='1.0.0', get_tool_input_schema=schemas.get,
                    on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=DEFAULT_MODEL_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    anyio.run(serve, EmbeddingService(args.model_dir))
