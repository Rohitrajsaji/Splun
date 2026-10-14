The current UI in `api/static/index.html` looks like unprofessional "AI slop" (cluttered with emojis, inconsistent spacing, visual discrepancies across tabs, and a broken layout in Safari where the left and right panels squeeze the center chat out of view at 100% zoom).

We are doing a complete, professional UI overhaul guided by `VoltAgent/awesome-design-md` (Vercel / Cursor / VoltAgent dark precision), `alchaincyf/huashu-design` (strict anti-AI-slop rules), `cathrynlavery/diagram-design` ("No shadows. Self-contained HTML + SVG"), and `shadcn/ui` zinc design tokens.

Follow GEMINI.md and use `context-mode` (`ctx_execute`) to complete the following steps while preserving all backend API routes, DOM IDs, JS handlers, and automated test compatibility.

### Step 1: Create `DESIGN.md` in the Project Root
Define our strict design system:
1. **Anti-AI-Slop Mandate**:
   - **ZERO EMOJIS ALLOWED**: Never use emojis (`🚀`, `🧠`, `🤖`, `📥`, `💬`, `✅`, `🟡`, `🔴`, `⚠️`, `❓`, `📋`, `🔍`, `📦`, etc.) anywhere in HTML, buttons, headers, badges, empty states, or JS-rendered strings. Use crisp `14px`–`16px` inline `<svg>` stroke icons (`stroke="currentColor" stroke-width="1.5" fill="none"`) and `6px` geometric status dots (`border-radius: 50%`).
   - **No Drop Shadows or Neon Gradients**: Zero `box-shadow` on cards, zero glowing borders, zero purple/blue gradient buttons. Separation is achieved strictly through surface luminance steps and `1px solid` hairline borders.
2. **Color Tokens (Vercel / Shadcn Zinc Dark)**:
   - `--bg-canvas`: `#09090B` (primary app background)
   - `--bg-panel`: `#0F0F12` (sidebars and header bar)
   - `--bg-surface`: `#18181B` (cards, inputs, table rows)
   - `--bg-elevated`: `#222226` (hover states, active pills)
   - `--border-subtle`: `#27272A` (`1px solid` structural hairlines)
   - `--border-focus`: `#52525B`
   - `--text-primary`: `#FAFAFA`
   - `--text-secondary`: `#A1A1AA`
   - `--text-muted`: `#71717A`
   - `--accent-primary`: `#FAFAFA` (high-contrast white primary buttons with `#09090B` text, Vercel/Linear style)
   - Semantic status tokens (muted background + crisp 1px border + high-legibility text):
     * Verified / Fact (`--status-verified`): dot `#10B981`, text `#34D399`, bg `rgba(16, 185, 129, 0.10)`, border `rgba(16, 185, 129, 0.25)`
     * Reported / Claim (`--status-reported`): dot `#F59E0B`, text `#FBBF24`, bg `rgba(245, 158, 11, 0.10)`, border `rgba(245, 158, 11, 0.25)`
     * Blocked / Discrepancy (`--status-blocked`): dot `#F43F5E`, text `#FB7185`, bg `rgba(244, 63, 94, 0.10)`, border `rgba(244, 63, 94, 0.25)`
     * Approved / Info (`--status-approved`): dot `#3B82F6`, text `#60A5FA`, bg `rgba(59, 130, 246, 0.10)`, border `rgba(59, 130, 246, 0.25)`
3. **Typography & Density**:
   - Sans UI font: `'Inter', -apple-system, BlinkMacSystemFont, sans-serif` (`13px` body, `12px` labels, `11px` uppercase tracking headers `letter-spacing: 0.05em`).
   - Monospace font for all IDs (`SC-001`, `REQ-001`, `DEC-001`), file paths, commits, and evidence refs: `'JetBrains Mono', 'SF Mono', ui-monospace, monospace` with `font-variant-numeric: tabular-nums`.
   - Compact border-radius: `6px` for cards/containers, `4px` for badges/buttons/inputs.

### Step 2: Completely Rebuild `api/static/index.html` to Follow `DESIGN.md` & Fix Safari Layout
Rewrite `api/static/index.html` from top to bottom so it looks like Linear / Vercel / Cursor and fixes the Safari viewport defect:

1. **Bulletproof Top Bar + 3-Pane Workspace Geometry**:
   - **Global Top Command Bar (`height: 44px`)**:
     * Left: Left-panel collapse toggle button (SVG sidebar icon), brand wordmark `SPLUN // CONSULTANT`, active Project selector (`#project-select`), `+ New Project` button, and `Load Warehouse 72 Demo` button (`#btn-load-demo`, styled as a sleek secondary outline button with an SVG database/play icon—NO emoji).
     * Right: Live project metrics pill (`80 Scenarios · 3 Verified · 1 Blocked`), `Export .ai-memory` button (SVG download/package icon), and Right-panel (`Project Brain`) collapse toggle button.
   - **3-Column Main Grid (`height: calc(100dvh - 44px)`, `width: 100vw`, `overflow: hidden`)**:
     * Use explicit CSS flex/grid with `min-width: 0` on ALL columns and children:
     * **Left Column (Workspace & Ingestion)**: `width: 240px; flex: 0 0 240px;` (collapses cleanly to `0px` when toggled, or becomes a slide-over drawer at `< 1100px`). Contains compact action rows with SVG icons for `Import Excel`, `Import BRD`, `Scan Code AST`, `Verify Git Diff`, plus the `Consultation Threads` list and `+ New Thread` button.
     * **Center Column (Consultant Stream)**: `flex: 1 1 0%; min-width: 460px;` on desktop so Safari NEVER squeezes the chat column! Center messages are constrained to a readable `max-width: 760px; margin: 0 auto; width: 100%;` with clean editorial typography:
       - Header shows active thread title, a crisp segmented pill toggle for `Project Mode` vs `Lightweight Mode`, and panel toggles when sidebars are collapsed.
       - Assistant messages render structured paragraphs, inline monospace evidence badges, and a sleek **Memory Mutation Bar** (an inline SVG git-commit icon + `"N project state mutations recorded"` + collapsible diff list + a crisp `[Revert]` / `[Undo]` button calling `undoMessage()`).
       - Composer sits in a clean bordered dock at the bottom with horizontal quick-action pills (`Project Status`, `Next Scenarios`, `NSI Clarification Agenda`, `Generate Handoff Spec`) that never clip.
     * **Right Column (Project Brain Inspector)**: `width: 380px; flex: 0 0 380px; min-width: 0;` (collapsible via top bar, and automatically acts as a slide-over drawer on viewports `< 1180px` so the center chat always stays wide and readable!).
2. **Eliminate Visual Discrepancies Across the 5 Project Brain Tabs**:
   - Use a compact, single-line segmented tab bar (`overflow-x: auto; scrollbar-width: none;`) with crisp short labels (`Scenarios`, `Decisions`, `Agenda`, `Handoff`, `Coordinator`) while keeping the full pane titles (`Scenarios Tracker`, `Decisions`, `Agenda & Discrepancies`, `Agent Handoff Spec`, `Coordinator Plan`) inside the panes or `aria-label`/`title` attributes so all existing tests pass.
   - **Tab 1 (`Scenarios Tracker`)**: Add a live status filter bar (`All`, `Verified`, `Reported`, `Blocked`, `Ready`) + search input, and render scenarios as compact, scannable Linear-style rows (`SC-001` in monospace, title, linked `REQ-XXX` pill, and a status dot badge).
   - **Tab 2 (`Decisions`)**: Render active and superseded bi-temporal decisions in a clean timeline ledger showing `subject_entity` in monospace, epistemic badge (`DOCUMENTED_FACT`, `APPROVED_DECISION`, `REPORTED_CLAIM`, `VERIFIED_OUTCOME`), verbatim `source_reference`, and a muted `Superseded by DEC-XXX` badge when historical.
   - **Tab 3 (`Agenda & Discrepancies`)**: Render structured discrepancy cards comparing `Specification (BRD)` vs `Observed State (Code AST / Claim)` side-by-side with the recommended stakeholder question.
   - **Tab 4 (`Agent Handoff Spec`)**: Clean monospace preview box with scenario filter input and a one-click `Copy Markdown` button.
   - **Tab 5 (`Coordinator Plan`)**: Structured summary bar (`Ready Batches`, `Escalated`, `Verified`) followed by bordered batch cards and red-accented escalation cards.

### Step 3: Verify in Real Headless Safari (WebKit) + Pytest + Live Walkthrough
1. Check that **zero emojis** remain anywhere in `api/static/index.html` (run a Python regex check scanning `api/static/index.html` for emoji Unicode ranges `[\U0001F300-\U0001FAFF\u2600-\u27BF]` and assert count == 0).
2. Using Playwright **WebKit** (Safari's engine) against the live server `http://127.0.0.1:8000` at `1440x900`, `1280x800`, `1024x768`, and `390x844`:
   - Measure `getBoundingClientRect()` on the Left panel, Center chat column, and Right panel.
   - Assert that at `1280x800` and `1440x900` (100% zoom), the Center chat column width is `>= 460px`, `document.documentElement.scrollWidth <= window.innerWidth`, all 5 Project Brain tabs click and render cleanly, and collapsing/expanding both side panels works smoothly.
3. Run `.venv/bin/python scripts/live_walkthrough.py` and `.venv/bin/pytest -v` to confirm all 46 automated tests and all 6 live walkthrough checks pass with exit code 0.
4. Report:
   - Confirmation that `DESIGN.md` was created and the emoji scan count in `api/static/index.html` is `0`.
   - Real WebKit (Safari) pixel measurements (`getBoundingClientRect().width`) across viewports.
   - Pytest & live walkthrough results, leaving `http://127.0.0.1:8000` running so I can refresh Safari immediately.