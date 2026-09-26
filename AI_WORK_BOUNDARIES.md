# AI Work Boundaries

This application is being built in parallel with GLM's visual presentation work. These boundaries keep functional implementation independent from visual design.

## LUNA owns

- Python backend and PyWebView shell/bridge
- Data models and application state
- Image importing, validation, thumbnail generation, and natural sorting
- Strip grouping and incomplete-strip handling
- Template loading, persistence, built-in asset extraction, and project persistence
- Canvas/design data model, editor behavior, and design property logic
- Undo/redo, crop behavior, snap/alignment calculations
- PDF geometry, A4 layout calculations, print validation, preview rendering, and PDF generation
- File export, recovery, and autosave
- Functional JavaScript modules needed to bind the UI to the API
- Backend/integration tests and packaging/build configuration

## GLM owns

- Final visual layout and hierarchy
- Typography, colors, light/dark theme presentation, and spacing
- Component appearance, icons, panels, sidebar/cards/controls, and visual states
- Animations, micro-interactions, and visual polish
- All frontend aesthetic decisions
- `frontend/js/visual.js`, the presentation-only event and visual-state adapter

## Shared contract area

The frontend/backend contract is documented in [FRONTEND_CONTRACT.md](FRONTEND_CONTRACT.md). Stable DOM IDs, `data-action`/`data-panel` hooks, API method names, event names, state bindings, and component containers belong to this shared area. GLM may reorganize the DOM and CSS freely while retaining or remapping the documented hooks. LUNA will adapt functional bindings to visual changes and will not replace GLM's visual work.

## File ownership

- LUNA: `app/`, `tests/`, `templates/builtin/`, `frontend/js/api.js`, `frontend/js/state.js`, and functional modules under `frontend/js/`.
- GLM: `frontend/index.html`, `frontend/css/`, and `frontend/js/visual.js` (presentation/layout/theme and visual-only event handling).
- Shared: `AI_WORK_BOUNDARIES.md`, `FRONTEND_CONTRACT.md`, `ARCHITECTURE.md`, semantic DOM hooks, and `frontend/js/app.js` entry-point wiring. Coordinate before changing shared hooks.

LUNA must not rewrite `frontend/js/visual.js`. A genuine compatibility defect may be fixed there only with the smallest targeted change, while preserving its presentation behavior and documenting the hook in `FRONTEND_CONTRACT.md`.

The repository was initially empty. The sibling `../DCapture-Photobooth` is a read-only reference; do not modify it.
