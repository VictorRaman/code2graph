"""Build call graph-only views from AST summaries."""

from __future__ import annotations

from typing import Any

try:
    from code_graph_demo.graph_builder import build_code_graph
except ImportError:
    from graph_builder import build_code_graph


def build_call_graph(ast_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a graph containing only local callables and CALLS edges."""
    graph = build_code_graph(
        ast_items,
        source_root="",
        features={"cfg": False, "dfg": False},
    )
    callable_ids = {
        node["id"]
        for node in graph["nodes"]
        if node.get("type") in {"Function", "Method"}
    }
    return {
        "metadata": graph["metadata"],
        "nodes": [node for node in graph["nodes"] if node["id"] in callable_ids],
        "edges": [
            edge
            for edge in graph["edges"]
            if edge.get("type") == "CALLS"
            and edge.get("source") in callable_ids
            and edge.get("target") in callable_ids
        ],
    }
