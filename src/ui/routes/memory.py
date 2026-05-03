"""Memory browser API routes — KG nodes, search, markdown."""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse


async def _get_kg(request: Request):
    """Get or create a KnowledgeGraph connection for this request."""
    if not hasattr(request.app.state, "_kg"):
        from src.memory.knowledge_graph import KnowledgeGraph

        workspace = request.app.state.app_context.get("workspace")
        if not workspace:
            return None
        kg_path = Path(workspace) / "memory" / "kg.db"
        if not kg_path.parent.exists():
            return None
        kg = KnowledgeGraph(kg_path)
        await kg.connect()
        request.app.state._kg = kg
    return request.app.state._kg


async def memory_nodes_list(request: Request) -> JSONResponse:
    kg = await _get_kg(request)
    if not kg:
        return JSONResponse([])
    node_type = request.query_params.get("type", "rule")
    limit = int(request.query_params.get("limit", "50"))
    nodes = await kg.list_nodes(node_type, limit=limit)
    return JSONResponse(nodes)


async def memory_search(request: Request) -> JSONResponse:
    kg = await _get_kg(request)
    if not kg:
        return JSONResponse([])
    q = request.query_params.get("q", "")
    if not q:
        return JSONResponse([])

    from src.memory.knowledge_search import KnowledgeSearch

    ks = KnowledgeSearch(kg)
    await ks.ensure_fts()
    # Rebuild once per KG connection to catch nodes inserted before FTS triggers.
    if not getattr(request.app.state, "_fts_built", False):
        await ks.rebuild_fts()
        request.app.state._fts_built = True
    node_type = request.query_params.get("type")
    limit = int(request.query_params.get("limit", "20"))
    results = await ks.fts_search(q, node_type=node_type, limit=limit)
    return JSONResponse(results)


async def memory_node_detail(request: Request) -> JSONResponse:
    kg = await _get_kg(request)
    if not kg:
        return JSONResponse({"error": "not found"}, status_code=404)
    node_id = request.path_params["node_id"]
    node = await kg.get_node(node_id)
    if not node:
        return JSONResponse({"error": "not found"}, status_code=404)
    edges_out = await kg.find_related(node_id)
    edges_in = await kg.find_related_inbound(node_id)
    return JSONResponse(
        {
            "node": node,
            "edges": {"outbound": edges_out, "inbound": edges_in},
        }
    )


async def memory_markdown(request: Request) -> JSONResponse:
    workspace = (request.app.state.app_context or {}).get("workspace")
    if not workspace:
        return JSONResponse({"sections": []})

    from src.memory.store import MemoryStore

    store = MemoryStore(Path(workspace))
    text = store.read_long_term()
    sections = MemoryStore.split_sections(text)
    return JSONResponse(
        {
            "sections": [{"title": t, "body": b} for t, b in sections],
        }
    )
