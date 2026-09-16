# SDLC design principles

This project uses HIG as a usability constraint, not as a visual brand. The same engineering principles apply from discovery through operations: every decision should make behavior clearer, change safer, and failures recoverable.

## Principles for maintainers and developers

| Principle | Working rule | Review question |
|---|---|---|
| Single Responsibility | Keep domain rules, transport, persistence, presentation, and composition in their owning layer. | Can this change be tested without booting unrelated layers? |
| Open/Closed | Add a focused port, strategy, or presenter rather than branching existing views for every capability. | Does a new backend or view require modifying stable policy? |
| Dependency Inversion | Views depend on `WorkbenchPort` and `Store`; adapters own HTTP, storage, and browser APIs. | Could a MockPort exercise this behavior? |
| Interface Segregation | Prefer capability-specific ports and narrow protocols over a universal service object. | Does this consumer need every method it receives? |
| Liskov Substitution | Keep adapters behaviorally compatible: cancellation, errors, schemas, and lifecycle must match the port contract. | Can the test adapter replace production without special cases? |
| KISS and YAGNI | Prefer the smallest explicit abstraction that removes repeated policy. Do not introduce a framework or speculative extension point. | Is this abstraction solving a current, measured problem? |
| DRY | Centralize only shared policy—tokens, async states, validation, escaping, and error presentation. Keep feature-specific markup local. | If this rule changes, is there one owner? |
| Cohesion and locality | Put a fact beside the code that owns it and expose it through a typed boundary. | Is state being duplicated across a view, store, and adapter? |
| Immutability at boundaries | Treat configuration, manifests, route values, and API responses as validated snapshots before use. | Can mutation create a stale or cross-run state? |
| Explicit state transitions | Represent loading, success, failure, cancellation, and empty states as named states, not combinations of booleans. | Are impossible states representable? |

## Patterns used deliberately

- **State**: `frontend/ts/async-state.ts` models asynchronous UI work as a discriminated union. A renderer can exhaustively handle idle, loading, ready, and error states.
- **Command**: reversible user mutations go through `Store` commands so undo and persistence remain explicit.
- **Adapter/Facade**: `ApiAdapter` and application ports keep views independent of HTTP and persistence details.
- **Strategy**: runtime resource and embedding backends vary behind narrow policies selected at the composition root.
- **Observer**: store subscriptions and lifecycle scopes update views without making views own global effects.
- **Presenter**: render modules convert validated domain data into localized, escaped UI output.

Patterns are not goals. Introduce one only when it isolates a real axis of change or makes a contract testable.

## Delivery loop

1. **Discover**: state the user outcome, affected boundary, non-goals, accessibility needs, security implications, and measurable acceptance criteria.
2. **Design**: identify the owning layer, applicable principles, contract changes, state transitions, and failure/recovery path. Record material trade-offs in an ADR or feature document.
3. **Implement**: make the smallest vertical slice. Keep views port-driven, route values encoded, labels localized, and mutations reversible.
4. **Verify**: add a pure unit test for rules, a view/adapter contract test for behavior, and an API or E2E journey when the real boundary is crossed. Test keyboard use, reduced motion, forced colors, and both locales for UI changes.
5. **Operate**: emit request/job/resource telemetry, preserve request IDs, define timeout and retry behavior, and document capacity assumptions and rollback steps.
6. **Learn**: review failures and performance evidence; fix the owning layer rather than adding compensating branches elsewhere.

## Definition of done

A change is complete only when:

- contracts, schemas, fingerprints, and hidden-truth boundaries remain intact;
- loading, empty, success, error, cancellation, and retry behavior are intentional;
- UI output is localized, escaped, keyboard accessible, and usable at narrow widths;
- state mutations are scoped, persisted through the port when required, and undoable where user initiated;
- focused tests, typecheck, lint/format, architecture gates, and generated frontend artifacts are current;
- operational impact, resource limits, observability, and recovery are documented when applicable.

This checklist is a guardrail for maintainability. It does not replace judgment, measurement, or review by the owning team.
