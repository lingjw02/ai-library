"""Transport exports without bootstrapping the web app on MCP imports."""

__all__ = ["create_app", "app", "KnowledgeOsMcpServer"]


def __getattr__(name):
    if name in ('create_app', 'app'):
        from importlib import import_module
        value = getattr(import_module('backend.adapters.api.app'), name)
    elif name == 'KnowledgeOsMcpServer':
        from backend.adapters.mcp.knowledge_os_mcp import KnowledgeOsMcpServer
        value = KnowledgeOsMcpServer
    else:
        raise AttributeError(name)
    globals()[name] = value
    return value
