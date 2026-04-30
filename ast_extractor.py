"""AST extraction skeleton built on Python's standard library."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


def _get_arg_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extract simple argument names from a function-like node."""
    args = [arg.arg for arg in node.args.posonlyargs]
    args.extend(arg.arg for arg in node.args.args)
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    args.extend(arg.arg for arg in node.args.kwonlyargs)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return args


def _get_annotation_name(node: ast.AST | None) -> str | None:
    """Return a compact type name from a simple annotation."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _get_annotation_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Subscript):
        value_name = _get_annotation_name(node.value)
        if value_name and value_name.rsplit(".", 1)[-1] in {
            "list",
            "List",
            "set",
            "Set",
            "tuple",
            "Tuple",
            "Sequence",
            "Iterable",
        }:
            if isinstance(node.slice, ast.Tuple) and node.slice.elts:
                return _get_annotation_name(node.slice.elts[0])
            return _get_annotation_name(node.slice)
        return value_name
    return None


def _get_call_name(node: ast.AST) -> str | None:
    """Resolve a conservative callee name for a call expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _get_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _call_site_info(node: ast.Call, statement_path: str) -> dict[str, Any] | None:
    """Build a structured call-site summary."""
    name = _get_call_name(node.func)
    if not name:
        return None
    info: dict[str, Any] = {
        "name": name,
        "lineno": getattr(node, "lineno", None),
        "statement_path": statement_path,
        "kind": "direct",
        "receiver": None,
        "attr": None,
    }
    if isinstance(node.func, ast.Attribute):
        info["kind"] = "attribute"
        info["receiver"] = _get_call_name(node.func.value)
        info["attr"] = node.func.attr
    return info


def _collect_call_sites_from_expr(expr: ast.AST | None, statement_path: str) -> list[dict[str, Any]]:
    """Collect call sites from one expression."""
    if expr is None:
        return []
    sites: list[dict[str, Any]] = []
    for child in ast.walk(expr):
        if isinstance(child, ast.Call):
            site = _call_site_info(child, statement_path)
            if site:
                sites.append(site)
    return sites


def _statement_type(node: ast.stmt) -> str:
    """Return a compact statement type name for CFG demo nodes."""
    if isinstance(node, ast.Return):
        return "ReturnStatement"
    return f"{type(node).__name__}Statement"


def _statement_info(node: ast.stmt, index: int, path: str) -> dict[str, Any]:
    """Build a recursive statement summary."""
    info: dict[str, Any] = {
        "index": index,
        "path": path,
        "type": _statement_type(node),
        "ast_type": type(node).__name__,
        "lineno": getattr(node, "lineno", None),
        "end_lineno": getattr(node, "end_lineno", None),
        "children": {},
    }
    children: dict[str, Any] = info["children"]
    if hasattr(node, "body"):
        body = getattr(node, "body")
        if isinstance(body, list):
            children["body"] = _statement_list_info(body, f"{path}.body")
    if hasattr(node, "orelse"):
        orelse = getattr(node, "orelse")
        if isinstance(orelse, list) and orelse:
            children["orelse"] = _statement_list_info(orelse, f"{path}.orelse")
    if isinstance(node, ast.Try):
        children["handlers"] = [
            {
                "type": _get_call_name(handler.type) if handler.type else None,
                "name": handler.name,
                "body": _statement_list_info(handler.body, f"{path}.handler{idx}"),
            }
            for idx, handler in enumerate(node.handlers)
        ]
        if node.finalbody:
            children["finalbody"] = _statement_list_info(node.finalbody, f"{path}.finalbody")
    if not children:
        info.pop("children")
    return info


def _statement_list_info(statements: list[ast.stmt], prefix: str = "") -> list[dict[str, Any]]:
    """Build summaries for a list of statements."""
    return [
        _statement_info(statement, idx, f"{prefix}.{idx}" if prefix else str(idx))
        for idx, statement in enumerate(statements)
    ]


def _var_name(node: ast.AST) -> str | None:
    """Return a simple variable name for Name or self.x style targets."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _var_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _collect_load_names(node: ast.AST) -> list[str]:
    """Collect variable names used in Load context inside an expression."""
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.append(child.id)
        elif isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load):
            name = _var_name(child)
            if name:
                names.append(name)
    return names


def _target_names(node: ast.AST | None) -> list[str]:
    """Return simple names assigned by a target expression."""
    if node is None:
        return []
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = _var_name(node)
        return [name] if name else []
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in node.elts:
            names.extend(_target_names(item))
        return names
    return []


def _event(kind: str, name: str, lineno: int | None, statement_path: str, context: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "lineno": lineno,
        "statement_path": statement_path,
        "statement_index": statement_path,
        "context": context,
    }


def _use_events(expr: ast.AST | None, statement_path: str, context: str) -> list[dict[str, Any]]:
    if expr is None:
        return []
    lineno = getattr(expr, "lineno", None)
    return [_event("use", name, lineno, statement_path, context) for name in _collect_load_names(expr)]


def _def_events(target: ast.AST | None, statement_path: str, context: str) -> list[dict[str, Any]]:
    lineno = getattr(target, "lineno", None)
    return [_event("def", name, lineno, statement_path, context) for name in _target_names(target)]


def _collect_data_flow_from_statements(
    statements: list[ast.stmt],
    prefix: str = "",
    context: str = "normal",
) -> list[dict[str, Any]]:
    """Collect define/use events recursively from statements."""
    events: list[dict[str, Any]] = []
    for index, stmt in enumerate(statements):
        path = f"{prefix}.{index}" if prefix else str(index)
        if isinstance(stmt, ast.Assign):
            events.extend(_use_events(stmt.value, path, context))
            for target in stmt.targets:
                events.extend(_def_events(target, path, context))
        elif isinstance(stmt, ast.AnnAssign):
            events.extend(_use_events(stmt.value, path, context))
            events.extend(_def_events(stmt.target, path, context))
        elif isinstance(stmt, ast.AugAssign):
            events.extend(_use_events(stmt.target, path, context))
            events.extend(_use_events(stmt.value, path, context))
            events.extend(_def_events(stmt.target, path, context))
        elif isinstance(stmt, ast.Return) and stmt.value:
            events.extend(_use_events(stmt.value, path, context))
        elif isinstance(stmt, ast.Expr):
            events.extend(_use_events(stmt.value, path, context))
        elif isinstance(stmt, ast.If):
            events.extend(_use_events(stmt.test, path, context))
            events.extend(_collect_data_flow_from_statements(stmt.body, f"{path}.body", "branch"))
            events.extend(_collect_data_flow_from_statements(stmt.orelse, f"{path}.orelse", "branch"))
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            events.extend(_use_events(stmt.iter, path, context))
            events.extend(_def_events(stmt.target, path, "loop"))
            events.extend(_collect_data_flow_from_statements(stmt.body, f"{path}.body", "loop"))
            events.extend(_collect_data_flow_from_statements(stmt.orelse, f"{path}.orelse", "loop"))
        elif isinstance(stmt, ast.While):
            events.extend(_use_events(stmt.test, path, context))
            events.extend(_collect_data_flow_from_statements(stmt.body, f"{path}.body", "loop"))
            events.extend(_collect_data_flow_from_statements(stmt.orelse, f"{path}.orelse", "loop"))
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                events.extend(_use_events(item.context_expr, path, context))
                events.extend(_def_events(item.optional_vars, path, context))
            events.extend(_collect_data_flow_from_statements(stmt.body, f"{path}.body", context))
        elif isinstance(stmt, ast.Try):
            events.extend(_collect_data_flow_from_statements(stmt.body, f"{path}.body", context))
            for handler_index, handler in enumerate(stmt.handlers):
                handler_context = f"{context}_except" if context != "normal" else "except"
                events.extend(
                    _collect_data_flow_from_statements(
                        handler.body,
                        f"{path}.handler{handler_index}",
                        handler_context,
                    )
                )
            events.extend(_collect_data_flow_from_statements(stmt.orelse, f"{path}.orelse", context))
            events.extend(_collect_data_flow_from_statements(stmt.finalbody, f"{path}.finalbody", context))
    return events


def _collect_call_sites_from_statements(statements: list[ast.stmt], prefix: str = "") -> list[dict[str, Any]]:
    """Collect call sites recursively from statements."""
    sites: list[dict[str, Any]] = []
    for index, stmt in enumerate(statements):
        path = f"{prefix}.{index}" if prefix else str(index)
        exprs: list[ast.AST | None] = []
        child_blocks: list[tuple[list[ast.stmt], str]] = []

        if isinstance(stmt, ast.Assign):
            exprs.append(stmt.value)
        elif isinstance(stmt, ast.AnnAssign):
            exprs.append(stmt.value)
        elif isinstance(stmt, ast.AugAssign):
            exprs.extend([stmt.target, stmt.value])
        elif isinstance(stmt, ast.Return):
            exprs.append(stmt.value)
        elif isinstance(stmt, ast.Expr):
            exprs.append(stmt.value)
        elif isinstance(stmt, ast.If):
            exprs.append(stmt.test)
            child_blocks.extend([(stmt.body, f"{path}.body"), (stmt.orelse, f"{path}.orelse")])
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            exprs.append(stmt.iter)
            child_blocks.extend([(stmt.body, f"{path}.body"), (stmt.orelse, f"{path}.orelse")])
        elif isinstance(stmt, ast.While):
            exprs.append(stmt.test)
            child_blocks.extend([(stmt.body, f"{path}.body"), (stmt.orelse, f"{path}.orelse")])
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            exprs.extend(item.context_expr for item in stmt.items)
            child_blocks.append((stmt.body, f"{path}.body"))
        elif isinstance(stmt, ast.Try):
            child_blocks.extend([(stmt.body, f"{path}.body"), (stmt.orelse, f"{path}.orelse")])
            child_blocks.append((stmt.finalbody, f"{path}.finalbody"))
            child_blocks.extend(
                (handler.body, f"{path}.handler{handler_index}")
                for handler_index, handler in enumerate(stmt.handlers)
            )

        for expr in exprs:
            sites.extend(_collect_call_sites_from_expr(expr, path))
        for block, block_path in child_blocks:
            sites.extend(_collect_call_sites_from_statements(block, block_path))
    return sites


def _infer_expr_type(expr: ast.AST | None) -> str | None:
    """Infer a simple constructed type from expressions like Name()."""
    if isinstance(expr, ast.Call):
        return _get_call_name(expr.func)
    return None


def _collect_type_hints(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, dict[str, str]]:
    """Collect local type hints and simple constructor assignments."""
    parameters: dict[str, str] = {}
    variables: dict[str, str] = {}

    all_args = [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]
    if node.args.vararg:
        all_args.append(node.args.vararg)
    if node.args.kwarg:
        all_args.append(node.args.kwarg)
    for arg in all_args:
        annotation = _get_annotation_name(arg.annotation)
        if annotation:
            parameters[arg.arg] = annotation
            variables[arg.arg] = annotation

    def visit_statements(statements: list[ast.stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, ast.AnnAssign):
                annotation = _get_annotation_name(stmt.annotation)
                for target in _target_names(stmt.target):
                    if annotation:
                        variables[target] = annotation
                    elif stmt.value:
                        inferred = _infer_expr_type(stmt.value)
                        if inferred:
                            variables[target] = inferred
            elif isinstance(stmt, ast.Assign):
                inferred = _infer_expr_type(stmt.value)
                if inferred:
                    for target in stmt.targets:
                        for name in _target_names(target):
                            variables[name] = inferred

            for child_name in ("body", "orelse", "finalbody"):
                child = getattr(stmt, child_name, None)
                if isinstance(child, list):
                    visit_statements(child)
            if isinstance(stmt, ast.Try):
                for handler in stmt.handlers:
                    visit_statements(handler.body)

    visit_statements(node.body)
    return {"parameters": parameters, "variables": variables}


def _build_function_info(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    """Build function or method info."""
    call_sites = _collect_call_sites_from_statements(node.body)
    return {
        "name": node.name,
        "lineno": getattr(node, "lineno", None),
        "end_lineno": getattr(node, "end_lineno", None),
        "args": _get_arg_names(node),
        "calls": [site["name"] for site in call_sites],
        "call_sites": call_sites,
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "statements": _statement_list_info(node.body),
        "data_flow": _collect_data_flow_from_statements(node.body),
        "type_hints": _collect_type_hints(node),
    }


def _build_import_info(node: ast.Import | ast.ImportFrom) -> list[dict[str, Any]]:
    """Build import items from import nodes."""
    items: list[dict[str, Any]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            items.append(
                {
                    "name": alias.name,
                    "alias": alias.asname,
                    "module": None,
                    "lineno": getattr(node, "lineno", None),
                    "type": "import",
                }
            )
    else:
        module_name = node.module
        if node.level:
            dots = "." * node.level
            module_name = f"{dots}{module_name or ''}"
        for alias in node.names:
            items.append(
                {
                    "name": alias.name,
                    "alias": alias.asname,
                    "module": module_name,
                    "lineno": getattr(node, "lineno", None),
                    "type": "from",
                }
            )
    return items


def extract_python_ast_info(file_path: str | Path) -> dict[str, Any]:
    """Extract a structured AST summary from one Python file."""
    path = Path(file_path)
    result: dict[str, Any] = {
        "file": {"path": path.as_posix(), "name": path.name},
        "classes": [],
        "functions": [],
        "imports": [],
    }

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            result["imports"].extend(_build_import_info(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append(_build_function_info(node))
        elif isinstance(node, ast.ClassDef):
            methods: list[dict[str, Any]] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(_build_function_info(item))
            result["classes"].append(
                {
                    "name": node.name,
                    "lineno": getattr(node, "lineno", None),
                    "end_lineno": getattr(node, "end_lineno", None),
                    "methods": methods,
                }
            )

    return result
