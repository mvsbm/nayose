# Nayose design system

Nayose is a cross-platform operational web application. It applies Human Interface Guidelines principles—clarity, deference, depth, consistency, feedback, and accessibility—without claiming native macOS fidelity or copying Apple branding. Evidence stays prominent, controls stay predictable, and system state remains available without permanently competing with the work.

## Sources of truth

- Runtime and deployment policy: `src/nayose/nayose.toml`, resolved through the typed configuration layer and environment overrides.
- Routes, navigation labels, capability gates, and semantic icon names: `frontend/ts/routes.ts`; icon geometry lives in `frontend/icons.svg` and shared markup in `frontend/ts/icons.ts`.
- Visual values: semantic custom properties in `frontend/styles.css`.
- Localized language: `frontend/ts/i18n.ts`.
- Transport and persistence: focused interfaces in `frontend/ts/port.ts`; views never duplicate these policies.

Do not add a second route catalog, view-local color palette, runtime configuration constant, or direct HTTP/storage call.

## Foundations

- **Color**: use semantic tokens (`--surface`, `--surface-2`, `--fg`, `--muted`, `--line`, `--accent`, `--ok`, `--warn`, `--bad`). Never introduce a feature color directly in a view.
- **Space**: use the shared `--space-*` scale and `--space` page gutter. Local layout exceptions need a component rule, not an inline style.
- **Type**: use the project font stack and existing hierarchy. Labels describe the task; technical identifiers are secondary and may wrap.
- **Materials and depth**: use solid content surfaces; reserve translucency and elevation for toolbar, popover, drawer, and overlay chrome. Every material must explain hierarchy or preserve context. Prefer solid surfaces when reduced transparency is requested.
- **Motion**: motion communicates change, never blocks work. Every animation has a reduced-motion path.
- **Targets**: regular controls use the shared control-size scale; native checkbox/radio controls retain a 24px minimum and all other tested targets retain at least 32px. Coarse-pointer layouts may increase these sizes. Dense data remains keyboard reachable and gets a named, bounded collection region.

## Component contracts

Each component owns its markup, states, bindings, and disposal. Renderers receive validated data and a translator; they do not call HTTP, storage, or global browser persistence. Dynamic data presentation uses semantic elements such as `progress`, `details`, table captions, and SVG attributes rather than inline CSS.

Every asynchronous component must define:

- idle or empty state;
- loading state that preserves useful previous content where safe;
- successful state;
- failure state with a localized recovery action;
- cancellation and stale-response behavior;
- `aria-busy`/live-region behavior that does not create unnecessary announcements;
- disposal with its parent `ViewScope`.

`frontend/ts/async-state.ts` is the shared State-pattern primitive. Add feature-specific data around `AsyncState<T>` rather than recreating `pending`/`error` flag combinations.

## Review gates

A frontend change is ready when:

1. it uses semantic tokens and a registered component class;
2. it has both locale strings and escaped data output;
3. its state transitions and cancellation path have focused tests;
4. keyboard, focus visibility, mobile layout, reduced motion/transparency, forced colors, and target sizing are checked;
5. templates contain no inline style attributes, preserving the strict style CSP;
6. generated `frontend/js` is rebuilt and frontend unit, architecture, and E2E gates pass where applicable.

These rules create a consistent base. Visual excellence still requires screenshot review, representative browser testing, and task-based operator research.
