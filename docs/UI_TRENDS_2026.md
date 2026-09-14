# UX evidence and verification

This document records the dashboard's UX decisions and the checks that support
those decisions. It is an engineering note, not a claim of user-study results,
industry leadership, or production readiness.

## Evidence-backed constraints

- **Human review remains explicit.** Matching results expose retrieval sources,
  field comparisons, normalized values, scores, and a review action.
- **Failure is visible.** Loading, stale-request, unavailable-model, and
  incompatible-release states render as actionable messages rather than empty
  success states.
- **State is reversible.** Review state uses a client undo path and is
  persisted by the server under the selected run.
- **Attention is protected.** Reduced-motion CSS is supported, the search
  workspace stays mounted while results change, and detail panels are
  progressively disclosed.
- **Accessibility is measured.** The UI sweep runs axe checks and verifies the
  minimum pointer-target size for every tested route.

## Current verification

Run the repository gate from the project root:

```bash
source .venv/bin/activate
python -m api.cli audit repo
```

The gate covers TypeScript compilation, frontend tests, Python tests, schema and
architecture contracts, lint, formatting, and typing. Browser E2E is opt-in:

```bash
NAYOSE_E2E=1 python -m api.cli audit repo
```

The browser tests use isolated fixture bundles. They do not establish latency,
reliability, task success, or accessibility conformance for every deployment.
Those require an environment-specific test run and, for usability claims, real
operator research.

## Open validation work

- Run the container image on each supported deployment target.
- Measure cold and warm search latency with representative generations.
- Run GPU and remote-install checks on the target hardware.
- Collect task-based feedback from reviewers before changing thresholds or
  interaction defaults.

Until those checks are run, documentation should describe the observed tests
and artifacts, not infer broader product outcomes.
