# Contributing to Nayose

## Workflow

Nayose uses a protected-trunk GitHub Flow. Branch from `main`, make one focused change, open a pull request, and squash-merge after all required checks pass. Do not push directly to `main` or keep long-lived integration branches.

Use short-lived branches named `<type>/<description>`:

- `feat/`, `fix/`, `refactor/`, `perf/`
- `docs/`, `test/`, `build/`, `chore/`

Commit and pull-request titles follow Conventional Commits:

```text
feat(search): add normalized alias lookup
fix(runtime): release retired engine resources
```

Keep source, tests, generated frontend JavaScript, and related documentation in the same logical change. Do not commit data, model bundles, credentials, environment files, or local runtime state.

## Development

Create the environment and install the locked development extras:

```bash
uv sync --frozen --extra matcher --extra performance --extra serve --extra dev
source .venv/bin/activate
npm --prefix frontend ci
```

Run focused checks while developing. Before requesting review, run the authoritative repository gate from a clean checkout:

```bash
python -m api.cli audit repo
```

If TypeScript changes, regenerate and commit `frontend/js/`:

```bash
npm --prefix frontend run build
```

If architecture imports change, regenerate the checked-in diagrams:

```bash
python scripts/generate_architecture_diagrams.py
```

See `AGENTS.md` for repository contracts and `ARCHITECTURE.md` for boundary ownership.

## Pull requests

A pull request should describe:

- the user or operator outcome and why it is needed;
- the owning architectural boundary and material trade-offs;
- security, schema, fingerprint, and operational implications;
- focused and full verification performed;
- rollback or recovery steps when behavior changes.

Prefer reviewable changes under roughly 400 hand-authored lines. Generated files are excluded from that guideline but must remain in sync. Resolve all review conversations and update the branch before merge.

## Releases

Only commits on `main` may be released. Release tags use `vX.Y.Z`, are immutable, and must match the version in `pyproject.toml`. Code releases do not replace immutable data-generation and model-run manifests.
