# CI and workflow measurement

GitHub Actions is the primary CI system. The `gate` job runs the authoritative
repository audit; the separate `data-smoke` job measures generation, training,
decision auditing, and browser transport against a fixed smoke workload.

Each measured command writes a versioned JSON resource artifact containing wall
time, process-tree CPU/RSS/thread counts, I/O when the platform exposes it,
and Linux cgroup v2 counters when available. Training and generation also write
an in-workflow `workflow_metrics.json` with stage spans, work counters, and the
data fingerprint. Browser journeys emit request/response/terminal-failure
counts without capturing customer payloads; intentional stale-request aborts are
accounted for separately. Metrics are aggregated across the two Node test-worker
files and uploaded as immutable CI artifacts.

Budgets are hard safety limits, not latency SLOs for shared GitHub-hosted
runners. Change `ci/resource_budgets.json` or `ci/compiler_budgets.json` only
with a recorded workload and rationale. Timing regressions should use repeated
measurements on fixed hardware; the CI budget gate is intentionally deterministic.
The extracted sequential training runtime is explicitly exempted from the
source-size budget until its stages are split into typed modules; it remains
covered by Ruff, runtime preflight, and the smoke workflow.

Jenkins is not needed for this repository's critical path. Use GitHub Actions
for pull-request checks, artifact retention, permissions, and repository-native
status checks. Add Jenkins only when an organizational requirement justifies a
persistent agent, private-network integration, specialized hardware, or a
pre-existing Jenkins estate; if added, it should invoke the same checked-in
commands and publish the same JSON artifacts rather than fork the quality gate.
