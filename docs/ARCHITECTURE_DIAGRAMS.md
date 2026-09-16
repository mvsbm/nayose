# Live architecture diagrams

The rendered diagrams are generated from the current import graph; they are not a second hand-maintained architecture model.

```bash
python scripts/generate_architecture_diagrams.py
python scripts/generate_architecture_diagrams.py --check
```

The command parses Python imports with the standard-library AST parser and resolves local TypeScript imports. It writes Mermaid to [`generated/architecture.md`](generated/architecture.md), which GitHub and compatible Markdown viewers render directly. CI checks that the generated document matches the source tree.

- **HLD**: subsystem dependency graph.
- **LLD**: internal Python module dependency graph.
- **Frontend**: TypeScript module dependency graph.

The diagrams intentionally show static dependencies, not runtime data flow, deployment topology, or performance. Those remain documented in [`ARCHITECTURE_HLD.md`](ARCHITECTURE_HLD.md) and [`ARCHITECTURE_ROADMAP.md`](ARCHITECTURE_ROADMAP.md).
