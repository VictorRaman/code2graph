"""Lightweight CFG builder for function and method top-level statements."""

from __future__ import annotations

from typing import Any

try:
    from code_graph_demo.graph_schema import Edge, Node
except ImportError:
    from graph_schema import Edge, Node


def _statement_id(owner_id: str, statement: dict[str, Any]) -> str:
    return f"stmt:{owner_id}:{statement.get('path', statement.get('index'))}"


def _first_statement_id(owner_id: str, statements: list[dict[str, Any]]) -> str | None:
    return _statement_id(owner_id, statements[0]) if statements else None


def _add_statement_nodes(
    owner_id: str,
    owner_path: str,
    statements: list[dict[str, Any]],
    nodes: list[Node],
) -> None:
    for statement in statements:
        nodes.append(
            Node(
                id=_statement_id(owner_id, statement),
                type=statement.get("type", "Statement"),
                name=statement.get("ast_type", "Statement"),
                path=owner_path,
                lineno=statement.get("lineno"),
                end_lineno=statement.get("end_lineno"),
                metadata={
                    "owner": owner_id,
                    "index": statement.get("index"),
                    "path": statement.get("path"),
                },
            )
        )
        children = statement.get("children", {})
        if isinstance(children, dict):
            for key in ("body", "orelse", "finalbody"):
                block = children.get(key, [])
                if isinstance(block, list):
                    _add_statement_nodes(owner_id, owner_path, block, nodes)
            handlers = children.get("handlers", [])
            if isinstance(handlers, list):
                for handler in handlers:
                    body = handler.get("body", []) if isinstance(handler, dict) else []
                    if isinstance(body, list):
                        _add_statement_nodes(owner_id, owner_path, body, nodes)


def _add_edge(edges: list[Edge], source: str | None, target: str | None, edge_type: str) -> None:
    if source and target:
        edges.append(Edge(source=source, target=target, type=edge_type))


def _connect_block(
    owner_id: str,
    statements: list[dict[str, Any]],
    edges: list[Edge],
    next_id: str | None = None,
    next_edge_type: str = "NEXT",
) -> list[str]:
    exits: list[str] = []
    for index, statement in enumerate(statements):
        current_id = _statement_id(owner_id, statement)
        following_id = (
            _statement_id(owner_id, statements[index + 1])
            if index + 1 < len(statements)
            else next_id
        )
        ast_type = statement.get("ast_type")
        children = statement.get("children", {})
        children = children if isinstance(children, dict) else {}

        if ast_type == "Return":
            continue

        if ast_type == "If":
            body = children.get("body", [])
            orelse = children.get("orelse", [])
            body_first = _first_statement_id(owner_id, body)
            orelse_first = _first_statement_id(owner_id, orelse)
            _add_edge(edges, current_id, body_first or following_id, "TRUE_BRANCH")
            _add_edge(edges, current_id, orelse_first or following_id, "FALSE_BRANCH")
            exits.extend(_connect_block(owner_id, body, edges, following_id))
            exits.extend(_connect_block(owner_id, orelse, edges, following_id))
            if not body and not orelse and not following_id:
                exits.append(current_id)
            continue

        if ast_type in {"For", "AsyncFor", "While"}:
            body = children.get("body", [])
            orelse = children.get("orelse", [])
            body_first = _first_statement_id(owner_id, body)
            orelse_first = _first_statement_id(owner_id, orelse)
            _add_edge(edges, current_id, body_first, "LOOP_BODY")
            _add_edge(edges, current_id, orelse_first or following_id, "LOOP_EXIT")
            body_exits = _connect_block(owner_id, body, edges)
            for exit_id in body_exits:
                _add_edge(edges, exit_id, current_id, "LOOP_BACK")
            exits.extend(_connect_block(owner_id, orelse, edges, following_id))
            if not following_id and not orelse:
                exits.append(current_id)
            continue

        if ast_type == "Try":
            body = children.get("body", [])
            orelse = children.get("orelse", [])
            finalbody = children.get("finalbody", [])
            handlers = children.get("handlers", [])
            final_first = _first_statement_id(owner_id, finalbody)
            after_try = final_first or following_id
            _add_edge(edges, current_id, _first_statement_id(owner_id, body), "TRY_BODY")
            body_exits = _connect_block(owner_id, body, edges, _first_statement_id(owner_id, orelse) or after_try)
            exits.extend(body_exits)
            if isinstance(handlers, list):
                for handler in handlers:
                    handler_body = handler.get("body", []) if isinstance(handler, dict) else []
                    _add_edge(edges, current_id, _first_statement_id(owner_id, handler_body), "EXCEPT_HANDLER")
                    exits.extend(_connect_block(owner_id, handler_body, edges, after_try))
            exits.extend(_connect_block(owner_id, orelse, edges, after_try))
            if finalbody:
                _add_edge(edges, current_id, final_first, "FINALLY_BODY")
                exits.extend(_connect_block(owner_id, finalbody, edges, following_id))
            elif not following_id:
                exits.append(current_id)
            continue

        if following_id:
            _add_edge(edges, current_id, following_id, next_edge_type)
        else:
            exits.append(current_id)
    return exits


def _add_cfg_for_callable(
    owner_id: str,
    owner_path: str,
    statements: list[dict[str, Any]],
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    if not statements:
        return

    _add_statement_nodes(owner_id, owner_path, statements, nodes)
    first_id = _first_statement_id(owner_id, statements)
    edges.append(Edge(source=owner_id, target=first_id, type="CFG_ENTRY"))
    _connect_block(owner_id, statements, edges)


def build_cfg(ast_infos: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Build CFG nodes and edges from AST summaries."""
    nodes: list[Node] = []
    edges: list[Edge] = []

    for info in ast_infos:
        if info.get("error"):
            continue
        path = info.get("file", {}).get("path", "")
        for function in info.get("functions", []):
            owner_id = f"function:{path}:{function.get('name', '')}"
            _add_cfg_for_callable(owner_id, path, function.get("statements", []), nodes, edges)
        for cls in info.get("classes", []):
            class_name = cls.get("name", "")
            for method in cls.get("methods", []):
                owner_id = f"method:{path}:{class_name}.{method.get('name', '')}"
                _add_cfg_for_callable(owner_id, path, method.get("statements", []), nodes, edges)

    return {"nodes": nodes, "edges": edges}
