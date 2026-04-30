"""Build a unified JSON-ready code graph from AST summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from code_graph_demo.cfg_builder import build_cfg
    from code_graph_demo.dfg_builder import build_dfg
    from code_graph_demo.graph_schema import Edge, Node
except ImportError:
    from cfg_builder import build_cfg
    from dfg_builder import build_dfg
    from graph_schema import Edge, Node


def _add_node(nodes: dict[str, Node], node: Node) -> None:
    nodes.setdefault(node.id, node)


def _add_edge(edges: dict[tuple[str, str, str], Edge], edge: Edge) -> None:
    edges.setdefault((edge.source, edge.target, edge.type), edge)


def _module_names(path: str) -> set[str]:
    """Return simple module names that may refer to a Python file path."""
    module = Path(path).with_suffix("").as_posix().replace("/", ".")
    names = {module}
    if "." in module:
        names.add(module.rsplit(".", 1)[-1])
    return names


def _single_or_none(items: list[str]) -> str | None:
    return items[0] if len(items) == 1 else None


def _simple_type_name(name: str | None, aliases: dict[str, str]) -> str | None:
    """Normalize an annotation or constructor name to a simple class name."""
    if not name:
        return None
    first, *rest = name.split(".")
    mapped = aliases.get(first, first)
    if rest:
        mapped = f"{mapped}.{'.'.join(rest)}"
    return mapped.rsplit(".", 1)[-1]


def _resolve_call(
    caller: dict[str, str | None],
    call_site: dict[str, Any],
    by_name: dict[str, list[dict[str, str | None]]],
    by_class_method: dict[tuple[str, str], list[dict[str, str | None]]],
    by_module_function: dict[tuple[str, str], list[dict[str, str | None]]],
    import_aliases: dict[str, dict[str, str]],
    class_attr_types: dict[tuple[str, str], str],
) -> tuple[list[str], bool, str]:
    """Resolve a call name to local callable ids.

    The resolver is intentionally conservative. Ambiguous matches are skipped
    instead of producing misleading CALLS edges.
    """
    call_name = str(call_site.get("name", ""))
    aliases = import_aliases.get(str(caller["path"]), {})
    if "." not in call_name:
        candidates = by_name.get(call_name, [])
        same_file = [item["id"] for item in candidates if item["path"] == caller["path"]]
        target = _single_or_none(same_file)
        if target:
            return [target], False, "direct_same_file"

        all_targets = [item["id"] for item in candidates]
        target = _single_or_none(all_targets)
        return ([target], False, "direct_unique") if target else ([], bool(all_targets), "direct")

    base, method_name = call_name.rsplit(".", 1)
    if base in {"self", "cls"} and caller.get("class_name"):
        candidates = by_class_method.get((str(caller["class_name"]), method_name), [])
        same_file = [item["id"] for item in candidates if item["path"] == caller["path"]]
        target = _single_or_none(same_file)
        return ([target], False, base) if target else ([], bool(same_file), base)

    type_hints = caller.get("type_hints")
    variable_types = type_hints if isinstance(type_hints, dict) else {}
    receiver = call_site.get("receiver")
    if isinstance(receiver, str):
        type_name = variable_types.get(receiver)
        if not type_name and receiver.startswith("self.") and caller.get("class_name"):
            type_name = class_attr_types.get((str(caller["class_name"]), receiver))
        type_name = _simple_type_name(type_name, aliases)
        if type_name:
            typed_candidates = by_class_method.get((type_name, method_name), [])
            same_file = [item["id"] for item in typed_candidates if item["path"] == caller["path"]]
            target = _single_or_none(same_file) or _single_or_none(
                [item["id"] for item in typed_candidates]
            )
            if target:
                return [target], False, "inferred_type"
            if typed_candidates:
                return [], True, "inferred_type"

    class_candidates = by_class_method.get((base, method_name), [])
    same_file = [item["id"] for item in class_candidates if item["path"] == caller["path"]]
    target = _single_or_none(same_file)
    if target:
        return [target], False, "class"
    target = _single_or_none([item["id"] for item in class_candidates])
    if target:
        return [target], False, "class_unique"
    if class_candidates:
        return [], True, "class"

    module_name = aliases.get(base, base)
    module_candidates = by_module_function.get((module_name, method_name), [])
    target = _single_or_none([item["id"] for item in module_candidates])
    return ([target], False, "module") if target else ([], bool(module_candidates), "module")


def build_code_graph(
    ast_infos: list[dict[str, Any]],
    source_root: str,
    features: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build a JSON-ready code graph from extracted Python AST info."""
    merged_features = {
        "ast": True,
        "call_graph": True,
        "cfg": False,
        "dfg": False,
    }
    if features:
        merged_features.update(features)

    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str, str], Edge] = {}
    errors: list[dict[str, str]] = []
    callable_records: list[dict[str, str | None]] = []
    callable_calls: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    import_aliases: dict[str, dict[str, str]] = {}

    for info in ast_infos:
        file_info = info.get("file", {})
        path = file_info.get("path", "")
        name = file_info.get("name", path)

        if info.get("error"):
            errors.append({"path": path, "error": str(info["error"])})
            continue

        file_id = f"file:{path}"
        _add_node(nodes, Node(id=file_id, type="File", name=name, path=path))

        for item in info.get("imports", []):
            import_name = item.get("name", "")
            alias = item.get("alias") or import_name
            lineno = item.get("lineno")
            import_id = f"import:{path}:{import_name}:{lineno}"
            if item.get("type") == "import":
                import_aliases.setdefault(path, {})[str(alias)] = str(import_name)
            elif item.get("type") == "from":
                import_aliases.setdefault(path, {})[str(alias)] = str(import_name)
            _add_node(
                nodes,
                Node(
                    id=import_id,
                    type="Import",
                    name=import_name,
                    path=path,
                    lineno=lineno,
                    metadata={
                        "module": item.get("module"),
                        "import_type": item.get("type"),
                        "alias": item.get("alias"),
                    },
                ),
            )
            _add_edge(edges, Edge(source=file_id, target=import_id, type="IMPORTS"))

        for function in info.get("functions", []):
            function_name = function.get("name", "")
            function_id = f"function:{path}:{function_name}"
            _add_node(
                nodes,
                Node(
                    id=function_id,
                    type="Function",
                    name=function_name,
                    path=path,
                    lineno=function.get("lineno"),
                    end_lineno=function.get("end_lineno"),
                    metadata={
                        "args": function.get("args", []),
                        "is_async": function.get("is_async", False),
                        "calls": function.get("calls", []),
                    },
                ),
            )
            _add_edge(edges, Edge(source=file_id, target=function_id, type="CONTAINS"))
            record = {
                "id": function_id,
                "name": function_name,
                "path": path,
                "class_name": None,
                "kind": "function",
                "type_hints": function.get("type_hints", {}).get("variables", {}),
            }
            callable_records.append(record)
            callable_calls.append(
                (
                    record,
                    function.get("call_sites")
                    or [{"name": name, "lineno": None} for name in function.get("calls", [])],
                )
            )

        for cls in info.get("classes", []):
            class_name = cls.get("name", "")
            class_id = f"class:{path}:{class_name}"
            _add_node(
                nodes,
                Node(
                    id=class_id,
                    type="Class",
                    name=class_name,
                    path=path,
                    lineno=cls.get("lineno"),
                    end_lineno=cls.get("end_lineno"),
                ),
            )
            _add_edge(edges, Edge(source=file_id, target=class_id, type="CONTAINS"))

            for method in cls.get("methods", []):
                method_name = method.get("name", "")
                method_id = f"method:{path}:{class_name}.{method_name}"
                _add_node(
                    nodes,
                    Node(
                        id=method_id,
                        type="Method",
                        name=method_name,
                        path=path,
                        lineno=method.get("lineno"),
                        end_lineno=method.get("end_lineno"),
                        metadata={
                            "class_name": class_name,
                            "args": method.get("args", []),
                            "is_async": method.get("is_async", False),
                            "calls": method.get("calls", []),
                        },
                    ),
                )
                _add_edge(edges, Edge(source=class_id, target=method_id, type="CONTAINS"))
                record = {
                    "id": method_id,
                    "name": method_name,
                    "path": path,
                    "class_name": class_name,
                    "kind": "method",
                    "type_hints": method.get("type_hints", {}).get("variables", {}),
                }
                callable_records.append(record)
                callable_calls.append(
                    (
                        record,
                        method.get("call_sites")
                        or [{"name": name, "lineno": None} for name in method.get("calls", [])],
                    )
                )

    if merged_features.get("cfg"):
        cfg = build_cfg(ast_infos)
        for node in cfg["nodes"]:
            _add_node(nodes, node)
        for edge in cfg["edges"]:
            _add_edge(edges, edge)

    if merged_features.get("dfg"):
        dfg = build_dfg(ast_infos)
        for node in dfg["nodes"]:
            _add_node(nodes, node)
        for edge in dfg["edges"]:
            _add_edge(edges, edge)

    by_name: dict[str, list[dict[str, str | None]]] = {}
    by_class_method: dict[tuple[str, str], list[dict[str, str | None]]] = {}
    by_module_function: dict[tuple[str, str], list[dict[str, str | None]]] = {}
    class_attr_types: dict[tuple[str, str], str] = {}
    for record in callable_records:
        name = str(record["name"])
        by_name.setdefault(name, []).append(record)
        class_name = record.get("class_name")
        if class_name:
            by_class_method.setdefault((str(class_name), name), []).append(record)
            type_hints = record.get("type_hints")
            if isinstance(type_hints, dict):
                for variable, type_name in type_hints.items():
                    if str(variable).startswith("self.") and isinstance(type_name, str):
                        class_attr_types[(str(class_name), str(variable))] = type_name
        elif record.get("kind") == "function":
            for module_name in _module_names(str(record["path"])):
                by_module_function.setdefault((module_name, name), []).append(record)

    resolved_calls = 0
    unresolved_calls = 0
    ambiguous_calls = 0
    for caller, call_sites in callable_calls:
        for call_site in call_sites:
            targets, ambiguous, resolution = _resolve_call(
                caller,
                call_site,
                by_name,
                by_class_method,
                by_module_function,
                import_aliases,
                class_attr_types,
            )
            if not targets:
                if ambiguous:
                    ambiguous_calls += 1
                else:
                    unresolved_calls += 1
                continue
            resolved_calls += len(targets)
            for target_id in targets:
                _add_edge(
                    edges,
                    Edge(
                        source=str(caller["id"]),
                        target=target_id,
                        type="CALLS",
                        metadata={
                            "callee": call_site.get("name"),
                            "lineno": call_site.get("lineno"),
                            "resolution": resolution,
                        },
                    ),
                )

    metadata: dict[str, Any] = {
        "source_root": source_root,
        "language": "python",
        "generated_by": "code_graph_demo",
        "features": merged_features,
        "resolved_calls": resolved_calls,
        "unresolved_calls": unresolved_calls,
        "ambiguous_calls": ambiguous_calls,
        "resolution_notes": (
            "Calls are resolved from direct names, module aliases, self/cls methods, "
            "and simple local type hints or constructor assignments."
        ),
    }
    if errors:
        metadata["errors"] = errors

    return {
        "metadata": metadata,
        "nodes": [node.to_dict() for node in nodes.values()],
        "edges": [edge.to_dict() for edge in edges.values()],
    }
