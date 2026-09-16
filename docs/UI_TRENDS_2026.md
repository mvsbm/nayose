# UX evidence and verification

This document records the cross-platform operational web application's UX decisions and the checks that support those decisions. It is an engineering note, not a claim of native macOS fidelity, user-study results, industry leadership, or production readiness.

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
- **Accessibility is measured.** The UI sweep runs axe checks and verifies target sizes for every tested route: 24px for compact native checkbox/radio controls and 32px for other interactive elements.
- **Chrome defers to work.** One route-driven sidebar owns navigation. Environment, appearance, and diagnostics use contextual disclosures instead of permanent duplicate rails or status bars.
- **Configuration has one owner.** Runtime policy comes from the typed `nayose.toml` configuration path; routes, visual tokens, and locale strings each have one frontend owner.

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

## Operator validation protocol

Before claiming production usability, recruit 5–8 representative reviewers and observe these tasks without coaching:

1. find an existing customer and explain the displayed evidence;
2. review an ambiguous pair, record a decision, undo it, and restore it;
3. identify why a match was not accepted automatically;
4. register one customer after checking for duplicates;
5. compare two runs and identify a meaningful metric change;
6. recover from an unavailable model, timeout, or rejected CSV.

Record completion, errors, elapsed time, confidence, unclear language, and assistance requested. Do not change scoring thresholds from usability feedback alone; route model-policy changes through evaluation and release gates.

## Open validation work

- Run the container image on each supported deployment target.
- Measure cold and warm search latency with representative generations.
- Run GPU and remote-install checks on the target hardware.
- Complete the operator protocol above with actual reviewers.
- Complete keyboard-only and VoiceOver task passes in Safari on macOS.
- Review approved screenshots for both themes, both locales, regular/compact density, forced colors, reduced motion, and 200%/400% reflow.

Until those checks are run, documentation should describe observed tests and artifacts, not infer broader product outcomes.
