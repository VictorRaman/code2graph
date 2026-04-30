# code_graph_demo

Minimal demo for representing a Python repository as a searchable code graph.

Default target: the current directory (`.`).

## Supported Features

- AST nodes: `File`, `Class`, `Function`, `Method`, `Import`
- Call graph edges: `CALLS`
- CFG edges: `CFG_ENTRY`, `NEXT`, `TRUE_BRANCH`, `FALSE_BRANCH`, `LOOP_BODY`, `LOOP_BACK`, `LOOP_EXIT`
- DFG nodes/edges: `VariableDef`, `VariableUse`, `DEFINES`, `USES`, `DATA_FLOW`
- Query demo: keyword search over nodes plus one-hop incoming/outgoing neighbors

## Run

Build the base graph:

```bash
python3 main.py --src . --out output/code_graph.json
```

Build the graph with CFG and DFG:

```bash
python3 main.py --src . --out output/code_graph_full.json --with-cfg --with-dfg
```

Query the graph:

```bash
python3 query.py --graph output/code_graph_full.json --q build_code_graph
```

## Output Format

The exported JSON has three top-level fields:

- `metadata`: source root, language, enabled features, resolved/unresolved/ambiguous call counts
- `nodes`: code entities such as files, classes, functions, statements, variables
- `edges`: relationships such as containment, imports, calls, control flow, data flow

Generated JSON files are ignored by git. Regenerate them with the commands above.

## Test

```bash
python3 -m unittest discover
python3 -m py_compile ast_extractor.py call_graph_builder.py cfg_builder.py dfg_builder.py exporter.py graph_builder.py graph_schema.py main.py query.py repo_scanner.py
```

## Limitations

- Python only
- Static approximation only
- `CALLS`, `CFG`, and `DFG` are lightweight demos, not compiler-grade analysis
- Does not handle complex dynamic dispatch, aliases, reflection, closures, global/nonlocal scope, or polymorphism
- Attribute calls through variables, such as `node.to_dict()`, resolve only when a local type hint, constructor assignment, self attribute assignment, or module alias makes the target clear

## Next Extensions

- Use tree-sitter for multi-language parsing
- Store/query graphs with NetworkX or Neo4j
- Add embedding-based semantic retrieval
- Expand retrieved context with graph traversal
- Convert retrieved subgraphs into prompts for Graph RAG code generation
