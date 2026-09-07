# UI/UX 2026: Trend Basis + Gap Assessment for Nayose

**Date:** 2026-09-06 · **Author:** repo engineering (evidence-first)
**Method:** fetched and read the sources below live; every claim about them is
grounded in the text actually retrieved this session (saved extracts in
`/tmp/uxresearch/`). Where a "trend" is an inference rather than a quotable
source claim, it is marked *(inference)*.

**Implementation status:** P0–P2 and E1–E5 are implemented in the frontend.
The strict frontend suite, 12-journey Chromium E2E suite, axe WCAG scan, and
repository audit all pass.

## 1. Sources (reputable, verified reachable)

| Source | What it is | Key 2026 signal (verbatim-anchored) |
|---|---|---|
| [NN/g — State of UX in 2026](https://www.nngroup.com/articles/state-of-ux-2026/) — Moran, Budiu, Gibbons, Jan 16 2026 | The most authoritative annual UX research review | **AI fatigue / post-hype AI**; "winners treat AI as a tool that **recedes into the background**"; **"in 2026, trust will be a major design problem for AI experiences"** — requires *transparency, control, consistency, support when the system fails*; **UI is commoditizing** (design systems/tokens + AI mediation), differentiators = curated taste, research-informed context, judgment. |
| [State of CSS 2026](https://2026.stateofcss.com/en-US/) — 4,902 respondents, May–Jun 2026 | Largest CSS community survey | Favorite new feature: **Anchor Positioning**; top pain points: positioning, `:has()`, **`@container` size queries**, CSS nesting, **animations**, **theming**, cascade management, **`color-mix()`**, **scroll-driven animations**, Popover API, `@scope`. |
| [State of CSS 2025 — Features](https://2025.stateofcss.com/en-US/features/) | Same survey, 2025 edition | `:has()` 80% used; aspect-ratio #2 usage; subgrid loved. |
| [Material Design 3 — Design tokens](https://m3.material.io/foundations/design-tokens/overview) | Google's cross-platform design standard | Token-based theming (color/type/space), system-to-component patterns; **dynamic color**, adaptable light/dark. |
| [WCAG 2.2 QuickRef](https://www.w3.org/WAI/WCAG22/quickref/) | W3C accessibility norm (Oct 2023) | New SCs incl. **2.5.8 Target Size (Minimum) 24×24px**, 2.4.11 Focus Not Obscured, 2.5.7 Dragging, redundant entry. |

## 2. What the GUI currently has (inventory from code)

**Information model exposed** (all server-owned, DuckDB-backed):
- Metrics KPI cards (%/ratio toggle), entity counts, edges/singleton summary; confusion matrix; compare-runs delta table.
- Hybrid search: exact + sparse + semantic (engine state labeled honestly), normalized-input preview, "もしかして" suggestions, facets (person/company/multi-record), twin/homophone badges.
- Match workbench: evidence-first candidate cards with per-field lineage comparisons, nearest entities, score-band context (`at_or_above/total`), threshold sandbox (session-only verdict preview — scores never mutate), retrieval diagnostics (backend, exact-mode), engine-warm status strip.
- Review queue: band histogram, reviewer persistence, flip-to-check decisions with server-side persistence + undo Command.
- Entity page: member rows (select/compare/clear), pagination, size/sort knobs.
- Admin: generate/train forms with live progress meter + ETA (streamed, honest), danger zone (two-step confirm), open-by-default authorization.
- Data entry wizard: import → dry-run → match-check → register/undo; always to `data/manual` (never the immutable generation).
- Chrome: ⌘K palette, theme toggle, ja/en locale (persisted), run selector, URL-as-state deep links (`#/review?nayose=…`).

**Design-system surface** (from `styles.css`, 148 lines):
- Design tokens via CSS custom properties: `--seed`, full light/dark palettes under `[data-theme]`, `color-scheme`, shadows, easing, space clamp, persisted comfortable/compact density, and a self-hosted variable Noto Sans JP font (`font-display: swap`).
- OKLCH-native staining: `color-mix(in oklch, …)` for row hover, heat cells, diff-highlight, skeleton shimmer.
- Consensus language (comment): *calm tech, progressive disclosure, uncertainty-first, soft-glass chrome, motion as meaning, reduced-motion respected, local-first*.
- Components registered + arch-enforced (`tests/test_arch_contract.py` scans `styles.css` registry).
- A11y primitives already present: skip link, `:focus-visible` outlines, `aria-live` statuses (`role="status"`, `aria-live="polite"`), `@media (prefers-reduced-motion: reduce)` global kill-switch, landmarks (`header`/`nav`/`main`), tables with `caption`/`scope`, `aria-pressed`, `aria-selected` listbox in palette, `aria-current` nav.

## 3. The Gap — repo state vs. the 2026 signals

Legend: ✅ already aligned · 🟡 partial · ❌ gap. File refs in parentheses.

### A. Trust & honest AI (NN/g 2026 #1 message: AI fatigue → background AI + trust)
| Signal | State | Comment |
|---|---|---|
| AI recedes into background; not every pixel gets an "AI sparkle" | ✅ | Semantic search is **labeled, not hyped**: engine state, exact-mode notice, "scoringHelp", honest counts. No fabricated confidence. |
| Transparency — show how the decision was made | ✅ | Evidence-first cards, thresholds visible, sandbox preview, lineage comparisons, model-diagnostics. |
| Control — user can override/undo | ✅ | Review decisions + undo Command; session-only threshold override (apply-then-revert); data-entry register/undo. |
| Consistency | 🟡 | Threshold source is labeled (manifest vs session) — but the **sandbox UI** does not yet persist its "apply to next match" across reloads (session only by design; fine, documented). |
| Support when the system fails | ✅ | Inline errors now carry bilingual "why/what next" guidance; paused matching exposes an explicit recovery action and admin/data-entry phases retain their recovery context. |
| Human curation/verification layer | ✅ | Review queue + decisions = exactly the NN/g "human direction, curation, verification" pattern. |

### B. UI commoditization (NN/g) → deepen the *experience layer*, not surfaces
| Signal | State | Comment |
|---|---|---|
| Standardized tokens/components (already "cheap") | ✅ | Tokens + registry + arch guard. |
| Navigate by delegation (agents/assistants) | ✅ | ⌘K provides keyboard-first navigation plus a small explicit action vocabulary for theme, density, and help — intent, not a chatbot. *(inference)* |
| Research-informed contextual detail | 🟡 | Band histograms + score context are strong; missing: **in-view definitions/help** for reviewer vocabulary (most is in `members` microcopy, some behind details). |

### C. Trust-worthy interactive patterns (WCAG 2.2 + M3 + SOC 2025/26)
| Item | State | Gap |
|---|---|---|
| Target size ≥ 24×24px (WCAG 2.2 SC 2.5.8) | ✅ gated | The Chromium ui-sweep measures every visible interactive candidate and fails below 24×24px; breadcrumbs, table links, file inputs, ranges, and checkboxes are styled to preserve the floor.
| Focus not obscured / visible focus everywhere | ✅ | Global `:focus-visible` remains active; the palette traps focus and restores it to its opener, and the sweep exercises the dialog path.
| Modal correctness | ✅ | Palette exposes dialog semantics, traps Tab focus, supports Escape, and restores focus to `#cmd`. |
| System theme / prefers-color-scheme | ✅ | Persisted light/dark/auto preference follows `matchMedia` in auto mode and updates the browser `theme-color`. |
| `color-mix()`, `:has()`, container queries, anchor positioning | ✅/🟡 | `color-mix()`, `:has()` row states, and `@container` histogram sizing are live; anchor positioning remains unnecessary for the current compact ruler. |
| Scroll-driven animations | 🟡 | Status strip/`rt-out` could use scroll-driven emphasis; **gate behind reduced-motion** (already global). |
| View Transitions API for route changes | 🟡 | None; SPA swaps are instant (fast = good calm-tech). Optional micro-diff transitions on KPIs/histogram bars (FLIP), not page chrome. |
| `meta[name=theme-color]` | ✅ | The appearance projection updates the browser chrome color for light and dark effective themes. |
| Typography ramp (M3 tokens) | 🟡 | Single 15px base + a few sizes; introduce `--text-*` ramp (xs…xl) in tokens, reuse in `.kpi .v`, `.members`, `.crumb`. |
| Consistent CJK rendering everywhere (esp. Docker/CI headless) | ✅ | Variable Noto Sans JP is vendored under `frontend/fonts/`, loaded locally with `font-display: swap`, license text, and a preload hint. |
| Automated accessibility verification | ✅ | `@axe-core/playwright` scans every swept route with WCAG 2 A/AA/2.2 AA tags; the suite also gates labels, focus, visibility, stateful controls, and target size. |
| Data-dense dashboard ergonomics | ✅ | KPI hierarchy, tabular numerals, unit toggle, entity page-size control, and persisted global comfortable/compact density are all available. |
| Calm-tech restraint | ✅ | Already the design language; 2026 trend actually supports *less*, not more. |
| Empty states teach | ✅ | `.empty` + guidance microcopy everywhere. |

## 4. The unifying philosophy: Calm Technology (Weiser & Brown; Amber Case)

The code has already declared it — the `styles.css` header comment: *"calm tech, progressive disclosure, uncertainty-first, soft-glass chrome + SOLID data surfaces (AA contrast), motion as meaning, reduced-motion respected, local-first."*

That is not decoration; it is a complete design philosophy with one axiom:

> **The interface informs from the periphery, intervenes only at the moment of human judgment, and fails calmly.**

Rooted in Mark Weiser's calm computing (Weiser & Brown, *The Coming Age of Calm Technology*, Xerox PARC 1996) — technology that "informs but doesn't demand focus" — and Amber Case's operating principles (*Calm Technology: Principles and Patterns for Human Flourishing*, O'Reilly 2015): technology should require the smallest possible amount of attention; inform and create calm; **work even when it fails**; dwell in the periphery; amplify the best of technology and humanity; communicate without shouting; work without being asked; function when broken.

Every P0–P2 item is a **derivation** of these principles — not a checklist of things NN/g mentioned:

| Calm-tech principle | Derived work item |
|---|---|
| Require the smallest amount of attention (low-precision interaction — position encoding beats fine targeting, Cleveland & McGill 1984) | **P0.2** target-size floor (controls always hittable, no precision tax); **P0.1** focus trap + restore (attention continuity: the eye is returned to where it was); **P2** density toggle (the user decides how much attention the surface demands) |
| Inform & create calm from the periphery | **P2** KPI FLIP + histogram rise as *peripheral* motion (motion-as-meaning, reduced-motion-gated); **P1** `@container`-responsive histogram labels that adapt without interrupting |
| **Work even when it fails** | **P1** failure affordances — every error gets a labeled "why/what next" (engine guidance, job phase hint). Calm error is *explained* error |
| Work without being asked; match the user's context | **P0.3** system theme (the surface follows the room — ambient, never a chore); **P0.4** local-first fonts (no network flash, no CLS; the page is *quietly there*) |
| Amplify the best of technology and humanity | **P1** axe-core a11y gate: accessibility is how calm tech stays calm *for everyone* — measured, not claimed |
| Communicate without shouting; respect user culture | **P2** `:has()` row states; the ja/en, theme, reviewer persistence already present — the tool *remembers you and doesn't brag about it* |

So the roadmap below is not "what 2026 articles suggested"; the sources confirmed the *direction*, and the philosophy **generates** the items.

## 4.1 The experience layer — a true HID review (what the user *feels*)

The mechanical review asks "does element X exist and work". A philosophical review asks: *what is the moment-by-moment felt experience, and where does it break?*

**The core scenario — an auditor at the review queue.** The task is high-stakes, repetitive, accountable (each verdict becomes the permanent record). The experience must provide: (1) a quiet, trustworthy environment (calm attention); (2) a decisive, confident *act* of judgment (human authority — NN/g 2026's non-commoditizable layer); (3) visible, retrievable consequences (radical honesty); (4) tolerance for the auditor's own fallibility (reversibility without shame).

Felt-state audit of today's UI, from the philosophy's own standpoint:

**1. Attention is front-loaded — the machine shouts, the human whispers.**
KPI numbers, status strip, thresholds, evidence tables are all *foreground*; the candidate's detail is a wall of cards, while the auditor's *verdict* is a small toggle. Calm tech says data dwells in the periphery until the moment of judgment. *Gap **E1 — decision-first composition**:* the human's act (採用/未採用) is the hero element on screen; candidates are stacked evidence behind progressive disclosure. NN/g 2026 says human judgment is the differentiated layer — here the UI literally doesn't heroize it yet.

**2. Confidence is decoded, not felt.**
Scores are numbers the auditor must read and compare — pure cognitive load. The existing threshold ruler is *positional*, which the eye reads first (Cleveland & McGill: position > length > angle > area > color). *Gap **E2 — felt uncertainty:*** make "where this candidate sits on the confidence continuum" the *primary* encoding (ruler dot, band position, gap line to runner-up — the honest "gap is information, not a decision condition" note already exists; elevate it visually), keep the exact number as the footnote. The auditor then *feels* the margin instead of decoding it.

**3. The act of judgment is not heroized — the world doesn't visibly change.**
When a reviewer flips a check, the row dims and a count ticks. The system registered the event, but the user does not *feel* "my verdict is now part of the record". *Gap **E3 — ambient confirmation:*** a brief "記録済" state on the row and the run's decision count ticking in the status strip — no toast theater, just the world having *changed* (the dual of "works even when it fails": when it works, the change is perceivable).

**4. The conceptual model of review is implicit (Norman's evaluation gulf).**
The reviewer-name field and the decision history are unexplained; the auditor may never form the mental model "my session is a named contribution to an immutable audit trail". *Gap **E4 — review-as-record:*** explain, at the model's seam (next to the reviewer field), what a decision is, who authored it, where it lives (`models/decisions`), and that it is undoable-while-uncommitted but permanent once part of the run. The user must always be able to see what state the system is in and how it got there (Norman, *The Design of Everyday Things*).

**5. Flow is interrupted by mechanics.**
The palette is fast; but each toggle re-paints the whole decisions list — screen flicker is attention loss. *Gap **E5 — rhythm preservation:*** keep the toggled row stable while only count/state change (or flip state in place) — the reviewer's challenge/feedback rhythm is preserved (Csikszentmihalyi's flow: task now, feedback immediately); this is also P2's FLIP motion, gated by reduced-motion.

These five gaps come from the *same* axiom as P0–P2 (attention integrity, human authority, calm failure) — but they target the **felt** experience, which is what "design deeper to differentiate" (NN/g 2026) means for an audit tool: the differentiator is the calm, authoritative moment of human verification — exactly what agents cannot commoditize.

## 5. Prioritized application roadmap (philosophy-derived)

**Status: complete.**

**P0 — Trust & a11y foundations (highest ROI, all testable):**
1. **Palette → accessible dialog**: `role="dialog"` + `aria-modal="true"`, focus trap (Tab/Shift-Tab within), focus restore to `#cmd` on close. Pure `app.ts` change + sweep assertions (open → tab-around → close → focus on #cmd).
2. **WCAG 2.2 target-size floor → measured gate**: extend `collectCandidates` to assert every interactive candidate has ≥24×24px hit area (already true today: 26–44px measured) — keeps the floor honest forever.
3. **Theme "auto"**: 3-state (light/dark/system) persisted; `matchMedia('(prefers-color-scheme: dark)')` applies token set; `meta theme-color` follows. `store.ts setTheme` + `app.ts`; new unit tests.
4. **Self-host Noto Sans JP variable** (`frontend/fonts/`), `font-display: swap`, preload; keeps local-first (no CDN), fixes Docker/CI typography drift.

**P1 — Experience layer (NN/g "deeper", still additive):**
5. **Failure affordance upgrade**: every error surface gets a labeled "why/what next" line (engine-unavailable guidance, job-failure phase hint) — `rt-error`, admin job error, data-entry notices.
6. **Palette action vocabulary** (`⌘K` intents, e.g. `generate smoke`, `review:reset`) — reuse existing `QueryPort`; no new backends.
7. **`@container` histogram labels + `:has()` row states** (evidence rows, review rows) — pure CSS, SOC-popular, arch-registry-compliant.

**P2 — Polish (do only if requested):**
8. KPI FLIP transitions + histogram bar rise, gated by reduced-motion.
9. `prefers-contrast` variant token set.
10. Global density toggle (comfort/compact).

## 6. Verification plan
- Every item above lands with: TS unit test (core/store), view/DOM assertions in `test/ui-sweep.test.mjs`, and — for P0.1–P0.4 — the existing strict E2E journey re-run.
- **E-series (E1–E5)** are verified experientially, not just functionally: the ui-sweep gains two journey-level assertions — (a) at the decision moment, the verdict control is the topmost visible actionable element (E1), (b) toggling a decision does NOT re-render the sibling rows (E5) — plus screenshot diffs gated on reduced-motion.
- `@axe-core/playwright` now runs inside `ui-sweep` with WCAG 2 A/AA/2.2 AA tags across every route; this converts "we believe AA" into a measured gate and directly answers NN/g 2026's trust requirement *for our own interface*.
- No dashboard logic moves client-side; no new raw SQL; strict-TS and i18n bilingual maintained (all new copy gets `data-i18n` keys in both ja/en).

**Bottom line:** Nayose is already unusually aligned with NN/g's 2026 thesis (background AI, transparency–control–consistency, human verification) — and with its *own* declared philosophy (calm tech). The mechanical gaps (palette modal correctness, system-theme, real fonts, measured a11y gate — target-size floor already passes at 26–44px) are closed by P0–P2; the *felt* gaps — a verdict that isn't heroized, confidence that is decoded instead of felt, an implicit record model, flicker that breaks flow — are the E-series, and they are the ones that will actually differentiate the product in 2026.