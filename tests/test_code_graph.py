from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ast_extractor import extract_python_ast_info
from call_graph_builder import build_call_graph
from graph_builder import build_code_graph
from repo_scanner import scan_python_files


class CodeGraphTest(unittest.TestCase):
    def _graph_from_sources(
        self,
        root: Path,
        sources: dict[str, str],
        *,
        cfg: bool = False,
        dfg: bool = False,
    ) -> dict:
        for name, source in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        ast_infos = [
            extract_python_ast_info(path)
            for path in sorted(root.rglob("*.py"), key=lambda item: item.as_posix())
        ]
        return build_code_graph(ast_infos, source_root=str(root), features={"cfg": cfg, "dfg": dfg})

    def test_scan_python_files_ignores_generated_and_env_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "keep.py"
            keep.write_text("x = 1\n", encoding="utf-8")
            ignored = root / "__pycache__" / "ignored.py"
            ignored.parent.mkdir()
            ignored.write_text("x = 2\n", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "ignored.py").write_text("x = 3\n", encoding="utf-8")
            notes = root / "notes.txt"
            notes.write_text("not python\n", encoding="utf-8")

            self.assertEqual(scan_python_files(root), [keep])
            self.assertEqual(scan_python_files(keep), [keep])
            self.assertEqual(scan_python_files(notes), [])

    def test_scan_python_files_rejects_missing_path(self) -> None:
        with self.assertRaises(FileNotFoundError):
            scan_python_files("does-not-exist")

    def test_extract_python_ast_info_collects_core_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "",
                        "def helper():",
                        "    return Path.cwd()",
                        "",
                        "class Runner:",
                        "    def run(self):",
                        "        return helper()",
                    ]
                ),
                encoding="utf-8",
            )

            info = extract_python_ast_info(path)

        self.assertNotIn("error", info)
        self.assertEqual([item["name"] for item in info["imports"]], ["os", "Path"])
        self.assertEqual([item["name"] for item in info["functions"]], ["helper"])
        self.assertEqual(info["classes"][0]["name"], "Runner")
        self.assertEqual(info["classes"][0]["methods"][0]["calls"], ["helper"])

    def test_extract_python_ast_info_returns_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.py"
            path.write_text("def broken(:\n", encoding="utf-8")

            info = extract_python_ast_info(path)

        self.assertIn("SyntaxError", info["error"])

    def test_build_code_graph_resolves_calls_conservatively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text(
                "\n".join(
                    [
                        "def build_parser():",
                        "    return None",
                        "",
                        "def unique():",
                        "    return None",
                        "",
                        "def main(node):",
                        "    build_parser()",
                        "    unique()",
                        "    node.to_dict()",
                        "    return None",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "query.py").write_text(
                "\n".join(
                    [
                        "def build_parser():",
                        "    return None",
                        "",
                        "def main():",
                        "    build_parser()",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "models.py").write_text(
                "\n".join(
                    [
                        "class Node:",
                        "    def to_dict(self):",
                        "        return {}",
                        "",
                        "class Edge:",
                        "    def to_dict(self):",
                        "        return {}",
                        "",
                        "class Worker:",
                        "    def helper(self):",
                        "        return None",
                        "",
                        "    def run(self):",
                        "        self.helper()",
                    ]
                ),
                encoding="utf-8",
            )

            ast_infos = [
                extract_python_ast_info(path)
                for path in sorted(root.glob("*.py"), key=lambda item: item.name)
            ]
            graph = build_code_graph(ast_infos, source_root=str(root))

        calls = {
            (edge["source"], edge["target"], edge["metadata"]["callee"])
            for edge in graph["edges"]
            if edge["type"] == "CALLS"
        }

        self.assertIn(
            ("function:%s/main.py:main" % root.as_posix(), "function:%s/main.py:build_parser" % root.as_posix(), "build_parser"),
            calls,
        )
        self.assertNotIn(
            ("function:%s/main.py:main" % root.as_posix(), "function:%s/query.py:build_parser" % root.as_posix(), "build_parser"),
            calls,
        )
        self.assertIn(
            ("function:%s/main.py:main" % root.as_posix(), "function:%s/main.py:unique" % root.as_posix(), "unique"),
            calls,
        )
        self.assertIn(
            (
                "method:%s/models.py:Worker.run" % root.as_posix(),
                "method:%s/models.py:Worker.helper" % root.as_posix(),
                "self.helper",
            ),
            calls,
        )
        self.assertFalse(any(edge[2] == "node.to_dict" for edge in calls))
        self.assertEqual(graph["metadata"]["ambiguous_calls"], 0)

    def test_build_code_graph_resolves_typed_attribute_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = self._graph_from_sources(
                root,
                {
                    "models.py": "\n".join(
                        [
                            "class Node:",
                            "    def to_dict(self):",
                            "        return {}",
                            "",
                            "class Edge:",
                            "    def to_dict(self):",
                            "        return {}",
                        ]
                    ),
                    "helpers.py": "def make():\n    return None\n",
                    "main.py": "\n".join(
                        [
                            "import helpers as h",
                            "from models import Node",
                            "",
                            "def typed(node: Node):",
                            "    return node.to_dict()",
                            "",
                            "def constructed():",
                            "    item = Node()",
                            "    item.to_dict()",
                            "    h.make()",
                            "",
                            "def unknown(node):",
                            "    node.to_dict()",
                            "",
                            "class Holder:",
                            "    def set_node(self):",
                            "        self.node = Node()",
                            "",
                            "    def run(self):",
                            "        self.node.to_dict()",
                        ]
                    ),
                },
            )

        calls = {
            (edge["source"], edge["target"], edge["metadata"]["callee"], edge["metadata"]["resolution"])
            for edge in graph["edges"]
            if edge["type"] == "CALLS"
        }
        node_to_dict = f"method:{root.as_posix()}/models.py:Node.to_dict"
        edge_to_dict = f"method:{root.as_posix()}/models.py:Edge.to_dict"

        self.assertIn(
            (
                f"function:{root.as_posix()}/main.py:typed",
                node_to_dict,
                "node.to_dict",
                "inferred_type",
            ),
            calls,
        )
        self.assertIn(
            (
                f"function:{root.as_posix()}/main.py:constructed",
                node_to_dict,
                "item.to_dict",
                "inferred_type",
            ),
            calls,
        )
        self.assertIn(
            (
                f"function:{root.as_posix()}/main.py:constructed",
                f"function:{root.as_posix()}/helpers.py:make",
                "h.make",
                "module",
            ),
            calls,
        )
        self.assertIn(
            (
                f"method:{root.as_posix()}/main.py:Holder.run",
                node_to_dict,
                "self.node.to_dict",
                "inferred_type",
            ),
            calls,
        )
        self.assertFalse(any(call[0].endswith(":unknown") and call[1] in {node_to_dict, edge_to_dict} for call in calls))

    def test_cfg_includes_nested_branches_loops_and_try(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = self._graph_from_sources(
                root,
                {
                    "flow.py": "\n".join(
                        [
                            "def flow(x):",
                            "    if x:",
                            "        if x > 1:",
                            "            y = 1",
                            "        else:",
                            "            y = 2",
                            "    else:",
                            "        y = 3",
                            "    for item in [1, 2]:",
                            "        y += item",
                            "    return y",
                            "    y = 99",
                            "",
                            "def handle():",
                            "    try:",
                            "        x = 1",
                            "    except ValueError:",
                            "        x = 2",
                            "    finally:",
                            "        x = 3",
                            "    return x",
                        ]
                    )
                },
                cfg=True,
            )

        edge_types = {edge["type"] for edge in graph["edges"]}
        self.assertTrue({"TRUE_BRANCH", "FALSE_BRANCH", "LOOP_BODY", "LOOP_BACK", "LOOP_EXIT"}.issubset(edge_types))
        self.assertTrue({"TRY_BODY", "EXCEPT_HANDLER", "FINALLY_BODY"}.issubset(edge_types))

        node_ids = {node["id"] for node in graph["nodes"]}
        owner = f"function:{root.as_posix()}/flow.py:flow"
        self.assertIn(f"stmt:{owner}:0.body.0.body.0", node_ids)
        return_id = f"stmt:{owner}:2"
        after_return_id = f"stmt:{owner}:3"
        self.assertFalse(
            any(edge["source"] == return_id and edge["target"] == after_return_id for edge in graph["edges"])
        )

    def test_dfg_includes_recursive_statement_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = self._graph_from_sources(
                root,
                {
                    "data.py": "\n".join(
                        [
                            "def data(items):",
                            "    total: int = 0",
                            "    for item in items:",
                            "        total += item",
                            "    with open('x') as handle:",
                            "        text = handle.read()",
                            "    return total",
                        ]
                    )
                },
                dfg=True,
            )

        variable_defs = {node["name"] for node in graph["nodes"] if node["type"] == "VariableDef"}
        variable_uses = {node["name"] for node in graph["nodes"] if node["type"] == "VariableUse"}
        self.assertTrue({"total", "item", "handle", "text"}.issubset(variable_defs))
        self.assertTrue({"items", "item", "total", "open", "handle.read"}.issubset(variable_uses))
        self.assertTrue(any(edge["type"] == "DATA_FLOW" for edge in graph["edges"]))

    def test_build_call_graph_returns_callable_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(
                "\n".join(
                    [
                        "def helper():",
                        "    return None",
                        "",
                        "def main():",
                        "    helper()",
                    ]
                ),
                encoding="utf-8",
            )
            graph = build_call_graph([extract_python_ast_info(path)])

        self.assertEqual({node["type"] for node in graph["nodes"]}, {"Function"})
        self.assertEqual([edge["type"] for edge in graph["edges"]], ["CALLS"])

    def test_cli_generates_graph_and_query_finds_function(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "sample.py").write_text(
                "def target_function():\n    return 1\n",
                encoding="utf-8",
            )
            out = root / "graph.json"

            main_result = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--src",
                    str(src),
                    "--out",
                    str(out),
                    "--with-cfg",
                    "--with-dfg",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(main_result.returncode, 0, main_result.stderr)
            graph = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(graph["metadata"]["source_root"], str(src))

            query_result = subprocess.run(
                [sys.executable, "query.py", "--graph", str(out), "--q", "target_function"],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(query_result.returncode, 0, query_result.stderr)
            self.assertIn("target_function", query_result.stdout)
            self.assertIn("summary: matches=", query_result.stdout)


if __name__ == "__main__":
    unittest.main()
