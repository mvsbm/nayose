# Nayose design system

Nayose is a cross-platform operational web application. It applies Human Interface Guidelines principles—clarity, deference, depth, consistency, feedback, and accessibility—without claiming native macOS fidelity or copying Apple branding. Evidence stays prominent, controls stay predictable, and system state remains available without permanently competing with the work.

## Sources of truth

- Runtime and deployment policy: `src/nayose/nayose.toml`, resolved through the typed configuration layer and environment overrides.
- Routes, navigation labels, capability gates, and semantic icon names: `frontend/ts/routes.ts`; icon geometry lives in `frontend/icons.svg` and shared markup in `frontend/ts/icons.ts`.
- Visual values: semantic custom properties in `frontend/styles.css`.
- Localized language: `frontend/ts/i18n.ts`.
- Transport and persistence: focused interfaces in `frontend/ts/port.ts`; views never duplicate these policies.

Do not add a second route catalog, view-local color palette, runtime configuration constant, or direct HTTP/storage call.

## Component vocabulary

- **Panel**: a structural task region. `.panel` owns layout, spacing, and component composition; it does not add a surface, border, or scroll behavior by itself.
- **Card**: a visual surface inside a panel. `.card` owns background, border, radius, and padding; it is not a general scroll container.
- **Button**: an action or state change. Compose `.button` with one variant (`.button-primary`, `.button-secondary`, `.button-destructive`, `.button-toolbar`) or a focused sub-variant (`.button-icon`, `.button-filter`, `.button-quiet`, `.button-chip`, `.button-check`).
- **Link**: navigation to a route or resource. Use `.action-link` only when a link intentionally has action emphasis; do not use button classes on links.
- **Text**: use semantic HTML for headings and body copy, `.text-meta` for secondary/helper text, `.muted` for color-only de-emphasis, and `.kpi-label`/`.kpi-value` for metric pairs. Avoid feature names such as `members`, `l`, or `v` for general typography.
- **Slider**: an `input[type="range"].slider` that has a localized label, explicit min/max/step, and a visible `.slider-output` for the current value.
- **Sash**: a `role="separator"` used to resize adjacent panes. Compose the layout hook (`.side-resizer`, `.panel-resizer`, or `.admin-resizer`) with `.sash`; a sash is not a slider.
- **Viewport**: the explicit owner of bounded scrolling. Compose `.viewport` with a pane or table region; cards and panels do not acquire scrolling implicitly. `Viewport` in `frontend/ts/viewport.ts` owns viewport event subscriptions.
- **Slide-over**: a temporary off-canvas surface, currently the mobile navigation drawer. It is distinct from both a panel and a sash.

## Foundations

- **Color**: use semantic tokens (`--surface`, `--surface-2`, `--fg`, `--muted`, `--line`, `--accent`, `--ok`, `--warn`, `--bad`). Never introduce a feature color directly in a view.
- **Space**: use the shared `--space-*` scale and `--space` page gutter. Local layout exceptions need a component rule, not an inline style.
- **Type**: use the project font stack and existing hierarchy. Labels describe the task; technical identifiers are secondary and may wrap.
- **Materials and depth**: use solid content surfaces; reserve translucency and elevation for toolbar, popover, drawer, and overlay chrome. Every material must explain hierarchy or preserve context. Prefer solid surfaces when reduced transparency is requested.
- **Motion**: motion communicates change, never blocks work. Every animation has a reduced-motion path.
- **Targets**: regular controls use the shared control-size scale; native checkbox/radio controls retain a 24px minimum and all other tested targets retain at least 32px. Coarse-pointer layouts may increase these sizes. Dense data remains keyboard reachable and gets a named, bounded collection region.

## Workspace layout policy

Resizable regions are reserved for adjacent work surfaces whose widths change how an operator compares or inspects information. `SplitPane` in `frontend/ts/split-pane.ts` owns pane discovery, persisted bounds, responsive maximums, and the `ResizableRegion` interaction; `panel-layout.ts` is its compatibility facade. The shell navigation, search results/detail, entity results/detail, record comparison, admin generation controls/preview, and dataset selection/content panes expose a keyboard-accessible sash with persisted bounds. Review, metrics, data entry, and configuration remain single-flow panels; they use disclosure controls instead of artificial split handles. Mobile collapses split panes into one column and hides the sash.

Every rendered table receives the shared table decorator: a localized row search, a column selector, and ascending/descending sorting. Server-backed dataset search remains authoritative; the shared control filters and sorts the currently loaded page without pretending to replace server pagination.

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

## Performance evidence

`frontend/test/performance.test.mjs` is an opt-in real-browser gate. Run it with `NAYOSE_E2E=1` and the Brave executable/bundle variables; it reports and checks route-ready time, JavaScript transfer size, DOM size, and long tasks. Budgets are explicit environment overrides so target hardware can establish a baseline without weakening the hermetic default suite.

## Review gates

A frontend change is ready when:

1. it uses semantic tokens and a registered component class;
2. it has both locale strings and escaped data output;
3. its state transitions and cancellation path have focused tests;
4. keyboard, focus visibility, mobile layout, reduced motion/transparency, forced colors, and target sizing are checked;
5. templates contain no inline style attributes, preserving the strict style CSP;
6. generated `frontend/js` is rebuilt and frontend unit, architecture, and E2E gates pass where applicable.

These rules create a consistent base. Visual excellence still requires screenshot review, representative browser testing, and task-based operator research.
