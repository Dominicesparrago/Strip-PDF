# Frontend Contract

This contract lets GLM build/rearrange the interface independently from functional services. The DOM can change freely if the semantic actions and bindings remain discoverable or are remapped in the shared entry-point wiring.

## Bridge

All frontend calls use:

```js
const result = await window.pywebview.api.invoke("method.name", params ?? {});
```

Success is `{ ok: true, data: ... }`; failure is `{ ok: false, error: "Human-readable message", detail?: ... }`. No Python exception text is shown directly. Methods take one params object. Long operations return a `jobId`; call `jobs.get({jobId})` to read progress and completion.

## Frontend-callable methods

| Method | Parameters | Purpose |
|---|---|---|
| `app.get_state` | `{}` | Initial project, image, strip, template, print, theme, and recovery state |
| `app.get_info` | `{}` | App version, data directory, capabilities |
| `session.get_recovery` | `{}` | Whether a recoverable autosave exists and its summary |
| `session.restore` | `{}` | Restore the recovered session |
| `session.start_new` | `{}` | Discard recovery state and start a new project |
| `images.pick` | `{}` | Open multi-image file picker and import selected paths |
| `images.import_paths` | `{ paths: string[] }` | Import dropped/selected filesystem paths |
| `images.remove` | `{ imageIds: string[] }` | Remove images from the project |
| `images.sort` | `{ mode: "smart"|"number"|"name_asc"|"name_desc"|"date"|"manual" }` | Apply an ordering mode |
| `images.reorder` | `{ imageIds: string[] }` | Set manual order |
| `images.get_thumbnail` | `{ imageId: string }` | Return cached thumbnail URI/data |
| `strips.generate` | `{ slotsPerStrip?: number, incomplete?: "empty"|"duplicate_last"|"repeat_from_start"|"exclude" }` | Regroup ordered images into strips |
| `strips.set_crop` | `{ stripId, slotIndex, crop }` | Set normalized crop/zoom state for an image slot |
| `templates.list` | `{}` | List built-in and custom templates |
| `templates.load` | `{ templateId: string }` | Load a template into the active design |
| `templates.save_as` | `{ name: string }` | Save current design as a custom template |
| `templates.duplicate` | `{ templateId: string, name?: string }` | Duplicate a custom or built-in template |
| `templates.rename` | `{ templateId: string, name: string }` | Rename a custom template |
| `templates.delete` | `{ templateId: string }` | Delete a custom template |
| `templates.reset` | `{}` | Reset active design to its template defaults |
| `design.get` | `{}` | Return active structured design state |
| `design.update` | `{ changes: object, label?: string }` | Apply property/element/crop edits and add one undo history entry |
| `design.add_element` | `{ type: "shape"|"decoration"|"accent"|"border"|"pattern", ...properties }` | Add a structured decoration or pattern element to the design history |
| `design.undo` / `design.redo` | `{}` | Move through bounded structured design history |
| `design.align` | `{ elementIds: string[], alignment: string, snap?: boolean }` | Align selected elements using artboard/frame geometry |
| `design.set_snap` | `{ enabled: boolean }` | Set snapping state |
| `print.get_settings` | `{}` | Get physical page/strip settings and output options |
| `print.update_settings` | `{ settings: object }` | Update physical print settings |
| `print.get_layout` | `{}` | Return per-page strip positions and fit calculation |
| `print.validate` | `{}` | Return structured PASS/WARNING/ERROR checks |
| `print.preview` | `{ page?: number, scale?: "fit"|100 }` | Render one preview page from the shared layout model; scale is a frontend display mode |
| `print.export` | `{ path?: string }` | Validate and write a print-ready PDF; returns a job ID or export summary |
| `print.presets.list` | `{}` | List saved print presets |
| `print.presets.save` | `{ name: string, settings: object }` | Save a reusable physical print preset |
| `project.new` | `{}` | Start a new project |
| `project.open` | `{ path?: string }` | Open project picker or the given project path |
| `project.save` | `{}` | Save current project atomically |
| `project.save_as` | `{ path?: string }` | Save to a selected project path |
| `theme.set` | `{ mode: "light"|"dark"|"system" }` | Persist theme mode and resolve the effective theme |
| `jobs.get` | `{ jobId: string }` | Query background job progress/result |

Method names are stable; optional parameters may be added without breaking callers. An unsupported method returns a structured error.

## State shapes

- Image: `{ id, path, name, extension, width, height, modifiedAt?, status, thumbnail? }`.
- Strip: `{ id, slots: [{ imageId|null, crop }], templateId, designOverride? }`.
- Crop: `{ mode: "cover"|"contain", ratio: "1:1"|"4:3"|"16:9"|"original", zoom, offsetX, offsetY }` with normalized offsets.
- Design: `{ schemaVersion, templateId, artboard: { widthMm, heightMm }, elements: [{ id, type, x, y, width, height, visible, locked, ... }] }`.
- Print settings: `{ paper: "A4", orientation: "landscape", stripsPerPage, stripWidthMm, stripHeightMm, marginMm, gapMm, cutGuides, resolutionDpi }`.
- Layout: `{ pageWidthMm, pageHeightMm, fit: { valid, requestedCount, maximumCount, availableWidthMm, requiredWidthMm, overflowMm }, pages: [{ index, placements: [{ stripId, xMm, yMm, widthMm, heightMm }] }] }`.
- Validation: `{ status: "PASS"|"WARNING"|"ERROR", checks: [{ id, status, message, detail? }] }`.
- Theme state: `{ mode, effective }`; root presentation hook is `data-theme="light|dark"`.

## UI hooks, events, and theme

- Use stable semantic selectors such as `[data-action="import-images"]`, `[data-action="export-pdf"]`, `[data-panel="image-library"]`, `[data-panel="strip-designer"]`, `[data-panel="print-layout"]`, `[data-bind="image-count"]`, `[data-bind="page-count"]`, and `[data-bind="validation-status"]`.
- GLM may reorganize containers and add visual-specific hooks. Functional JS should query semantic hooks rather than depend on visual nesting or CSS class names.
- Functional events dispatched on `document`: `studio:state-change`, `studio:job-progress`, `studio:validation-updated`, `studio:preview-updated`, `studio:preview-scale-change`, `studio:snap-guides`, `studio:project-changed`, `studio:theme-change`, and `studio:files-dropped`. Event `detail` contains the relevant structured data. Snap guide detail has `{ x, y }` artboard coordinates, with `null` for inactive axes. `studio:files-dropped` has `{ paths: string[] }` and is dispatched by the PyWebView DOM bridge.
- Theme mode is persisted by `theme.set`. Set root `data-theme` to the effective light/dark mode; for `system`, resolve `prefers-color-scheme` and follow changes where supported.
- The desktop shell subscribes to PyWebView's DOM `drop` event and forwards its `pywebviewFullPath` values as `studio:files-dropped`. Functional JavaScript passes those paths to `images.import_paths`; the file picker remains available through `images.pick`.

## Presentation layer hooks (visual shell)

The visual shell (`frontend/index.html`, `frontend/css/`, presentation-only `frontend/js/visual.js`) adds the following hooks. `visual.js` is GLM-owned; Luna may change it only for a narrowly scoped compatibility fix. Functional code may use the hooks but must not depend on CSS classes.

### View routing
- Workspaces live in `<section data-view="images|designer|print|preview|templates|settings">` inside `main[data-panel="studio"]`. Only the active section has `.is-active`.
- Sidebar buttons `[data-nav="…"]` switch views (presentation-only; no application state).
- `body[data-active-view]` mirrors the active view name for presentation CSS.

### Mirrored read-only bindings
`visual.js` keeps these in sync from the functional bindings, so Luna only writes the sources:
- `[data-bind="mirror-image-count"]`, `[data-bind="mirror-strip-count"]`, `[data-bind="mirror-page-count"]` ← copies of `image-count` / `strip-count` / `page-count`.
- `[data-bind="mirror-validation-status"]` (topbar chip) ← copy of `validation-status`; PASS/WARNING/ERROR drives icon and color.
- `[data-print-size-value]` ← derived from the `strip-width-mm` / `strip-height-mm` inputs.
- `[data-empty="library|strips|checks|preview"]` ← toggled `hidden` from the same sources.

### Layers panel
`[data-layers]` mirrors `[data-bind="design-element-list"]` options (labels parsed as `id · type`). Selecting a row sets the functional select and dispatches `change`; eye/lock buttons click the functional `element-toggle` / `element-lock` actions. `app.js` publishes the active design as `window.__studio_design_snapshot`; visibility/lock state and fill swatches render from it and refresh after property changes.

### Parked functional controls
The sort select `[data-action="sort-images"]`, theme select `[data-action="theme-set"]`, and element list `[data-bind="design-element-list"]` render inside a visually hidden `.parked` container. The segmented sort/theme controls and layers panel forward values into them and dispatch `change`; functional listeners must stay attached to the parked nodes.

### Events consumed by the presentation layer
`studio:state-change`, `studio:validation-updated`, `studio:preview-updated`, `studio:theme-change`, `studio:job-progress` (reads optional `detail.message` and `detail.progress` as 0–1 or percent), and `studio:ready` (hides the boot splash early). `studio:preview-scale-change` and `studio:snap-guides` are reserved for future canvas/preview presentation. All are optional; the shell degrades gracefully without them.

Preview toolbar hooks `[data-vaction="pz-fit|pz-100|pz-in|pz-out"]` control only the displayed preview image scale; they never alter physical print dimensions or PDF rendering. Fit and 100% are explicit modes, and zoom in/out adjusts display scale in bounded steps.

### Boot splash
`#boot-splash` covers the UI until `body.is-booted` (set on window `load`, on `studio:ready`, or after a 1.5 s failsafe). Functional code does not manage it.

### Notes for functional integration
- `data-panel="app-header"` moved to the topbar `<header>`; project buttons and `project-name` live there.
- `[data-bind="app-status"]` remains the single functional status line and now sits in the status bar.
- `export-pdf`, `print-validate`, `preview-*`, and all designer/template actions exist exactly once with unchanged names.
- Designer property changes read numeric fields only when their corresponding semantic control is present and valid. The GLM inspector may omit advanced numeric controls (currently border radius and padding); absent fields are omitted from `design.update`, preserving the model's stored values.
- Design and crop mutations are serialized through the functional adapter; rapid property edits send only the changed property so later controls do not restore stale values.
- Print form edits are saved in order. Validation, preview navigation, and PDF export flush the latest physical settings before using the layout.
- `showPreview` assigns the shared preview image source and clears its initial `hidden` state when a rendered page is available.
- Saving an untouched `Untitled Project` with Save As uses the selected filename as the project name; named projects retain their current name.
- Template Save As, Duplicate, Rename, Delete, and Reset are implemented by the documented `templates.*` bridge methods and bind through their existing `data-action` hooks.
- The A4 sheet schematic (`[data-sheet-strips]`) is presentation-only; the authoritative preview is `print.preview`. If `print.get_layout` placements become available at the UI, `visual.js` can consume them behind the same hook.
