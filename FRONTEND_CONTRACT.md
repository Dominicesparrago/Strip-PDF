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
| `images.get_thumbnail` | `{ imageId: string, retry?: boolean }` | Return a cached WebP data URI; `retry: true` regenerates it from the original source |
| `strips.generate` | `{ slotsPerStrip?: number, incomplete?: "empty"|"duplicate_last"|"repeat_from_start"|"exclude" }` | Regroup ordered images into strips |
| `strips.set_crop` | `{ stripId, slotIndex, crop }` | Set normalized crop/zoom state for an image slot |
| `templates.list` | `{}` | List built-in and custom templates |
| `templates.load` | `{ templateId: string }` | Load a template into the active design |
| `templates.save_as` | `{ name: string, stripId?: string }` | Save the selected strip's effective design (including text), without its photo assignments |
| `templates.duplicate` | `{ templateId: string, name?: string }` | Duplicate a custom or built-in template |
| `templates.rename` | `{ templateId: string, name: string }` | Rename a custom template |
| `templates.delete` | `{ templateId: string }` | Delete a custom template |
| `templates.reset` | `{}` | Reset active design to its template defaults |
| `design.get` | `{ stripId?: string }` | Return the structured design for the requested strip or shared baseline |
| `design.update` | `{ stripId?: string, changes: object, label?: string }` | Apply edits to the current strip override or shared baseline, duplicate/delete/reset an element, optionally update one slot crop atomically, and add one undo history entry |
| `design.add_element` | `{ stripId?: string, type?: string, elementType?: string, ...properties }` | Add a structured decoration, pattern, or text element to the current strip or shared baseline |
| `design.apply_to_all` | `{ stripId: string, elementId: string }` | Synchronize one logical element to every other strip while preserving photo slots/crops; returns affected/created/updated/skipped counts in one history step |
| `design.undo` / `design.redo` | `{}` | Move through bounded structured design history |
| `design.align` | `{ stripId?: string, elementIds: string[], alignment: string, snap?: boolean }` | Align selected elements using artboard/frame geometry |
| `design.set_snap` | `{ enabled: boolean, stripId?: string }` | Set snapping state for the selected strip design or shared baseline |
| `print.get_settings` | `{}` | Get physical page/strip settings and output options |
| `print.update_settings` | `{ settings: object }` | Update A4 orientation, Auto/manual copies, margins, gap, guides, output DPI, or explicitly select a strip preset/custom size; the project strip specification remains authoritative |
| `print.get_layout` | `{}` | Return per-page strip positions and fit calculation |
| `print.validate` | `{}` | Return structured PASS/WARNING/ERROR checks |
| `print.preview` | `{ page?: number, dpi?: number }` | Render one preview page from the shared millimeter layout; visual zoom is frontend-only |
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

- Image: `{ id, internalId, path, name, originalName, displayName, extension, width, height, aspectRatio, orientation, orderIndex, previewFit, warning?, modifiedAt?, status, thumbnail? }`. `id`/`internalId` remain stable internal identity; original/display filenames stay separate.
- Supported imports: `.jpg`, `.jpeg`, `.jpe` (JPEG), `.png`, and `.webp`; native image selection and backend validation use the same set.
- Strip: `{ id, slots: [{ imageId|null, crop }], templateId, designOverride: Design|null }`. `project.design` is the shared template baseline; edits carrying `stripId` create/update only that strip's override. Runtime project snapshots also publish derived `resolvedFrameLayouts`, keyed by strip ID plus `__shared__`, from the same source-aware layout used by rendering.
- Crop: `{ mode: "cover"|"contain", ratio: "1:1"|"4:3"|"16:9"|"original", zoom, offsetX, offsetY }` with normalized offsets.
- Design: `{ schemaVersion, templateId, artboard: { width, height, widthMm, heightMm }, elements: [{ id, syncKey, type, x, y, width, height, visible, locked, ... }], imageLayout?, pattern, snap }`. `imageLayout` carries structural padding/gap/alignment and an optional explicit `frameWidth` after a Designer resize. `project.frameLayoutMode` is `adaptive` for new source-aware layouts and `legacy` for saved designs that have not opted in.
- Text element: `{ id, syncKey, type: "text", text, content, x, y, width, height, fontFamily, fontSize, fontWeight, fontStyle, textAlign, verticalAlign, fill, opacity, letterSpacing, lineHeight, visible, locked }`. Controlled families: Arial, Georgia, Courier New. Font size/letter spacing/geometry use artboard units; lineHeight is a multiplier.
- Image previews use `previewFit: "contain"`; dimensions/orientation/aspect ratio describe the EXIF-corrected source. `aspectRatio: "original"` makes each populated frame use its assigned source ratio and starts with `contain` at zoom 1; `1:1`, `4:3`, and `16:9` resolve fixed frame ratios and retain explicit cover/contain behavior without changing physical strip size.
- Canonical strip specification is `project.stripSpec`: `{ presetId, widthMm, heightMm, dpi, designWidth, designHeight, orientation, frameCount }`. The built-in default is `photobooth-2x6` at 50.8 × 152.4 mm and 600 × 1800 logical units. `printSettings.stripWidthMm/stripHeightMm` are derived compatibility readouts.
- Print settings: `{ paper: "A4", orientation: "landscape"|"portrait", stripsPerPage: "auto"|positiveInteger, marginMm, gapMm, cutGuides, resolutionDpi }`. `print.update_settings` accepts `stripPresetId` (`photobooth-2x6`, `photobooth-54x180`, or `custom` with `customWidthMm/customHeightMm`) as an explicit size change. Layout results include `fit.maximumCount`, `fit.effectiveCount`, and exact per-page millimeter placements.
- Layout: `{ pageWidthMm, pageHeightMm, fit: { valid, requestedCount, maximumCount, availableWidthMm, requiredWidthMm, overflowMm }, pages: [{ index, placements: [{ stripId, xMm, yMm, widthMm, heightMm }] }] }`.
- Validation: `{ status: "PASS"|"WARNING"|"ERROR", checks: [{ id, status, message, detail? }] }`.
- Theme state: `{ mode, effective }`; root presentation hook is `data-theme="light|dark"`.

## UI hooks, events, and theme

- Use stable semantic selectors such as `[data-action="import-images"]`, `[data-action="export-pdf"]`, `[data-panel="image-library"]`, `[data-panel="strip-designer"]`, `[data-panel="print-layout"]`, `[data-bind="image-count"]`, `[data-bind="page-count"]`, and `[data-bind="validation-status"]`.
- GLM may reorganize containers and add visual-specific hooks. Functional JS should query semantic hooks rather than depend on visual nesting or CSS class names.
- Functional events dispatched on `document`: `studio:state-change`, `studio:job-progress`, `studio:validation-updated`, `studio:preview-updated`, `studio:preview-scale-change`, `studio:snap-guides`, `studio:element-selected`, `studio:design-applied-all`, `studio:project-changed`, `studio:theme-change`, and `studio:files-dropped`. Event `detail` contains structured data. Snap guide detail has `{ x, y }` artboard coordinates, with `null` for inactive axes. Element selection detail has `{ id: string|null, stripId?: string }`; `null` clears selection. `studio:files-dropped` has `{ paths: string[] }` and is dispatched by the PyWebView DOM bridge.

## Designer functional hooks

Designer edits use `design.update({ stripId, changes, label })` unless the row below names another method. Element patches have `{ id, ...properties }`; `changes.duplicateElementId`, `changes.deleteElementId`, and `changes.resetElementId` take an element ID. `changes.crop` takes `{ stripId, slotIndex, crop }` and may be combined with element patches for one atomic undo step. Reset restores the active-template element with the same ID; added elements without a template counterpart reset to neutral type defaults. Shared design properties live under `project.design`; selected-strip edits are full structured designs under `project.strips[].designOverride`; photo crop state remains under `project.strips[].slots[].crop`.

| Capability | API and state | Event | Semantic hook |
|---|---|---|---|
| Select element / clear selection | No backend call; `selectedElementId` in frontend state, `null` means nothing selected | `studio:state-change` detail includes the authoritative `selectedElementId`; `studio:element-selected` detail `{ id: string|null }` | `[data-bind="design-element-list"]` (empty value clears), `[data-bind="designer-canvas"]`; layer rows use `[data-layers]` / `[data-layer-id]` |
| Move, X/Y, width/height | `design.update`; `project.design.elements[].{x,y,width,height}` | `studio:state-change`; drag emits `studio:snap-guides` | `[data-bind="element-x|element-y|element-width|element-height"]`; drag on `[data-bind="designer-canvas"]` |
| Uniform scale | `design.update` with both dimensions multiplied by the positive scale factor | `studio:state-change` | `[data-bind="element-scale"]` + `[data-action="element-apply-scale"]` |
| Aspect-ratio lock | Frontend-only `aspectLockEnabled`; width/height edits update the paired dimension | `studio:state-change` after geometry update | `[data-action="design-aspect-lock"]` checkbox |
| Fill, border color/width, radius, opacity | `design.update`; `project.design.elements[].{fill,borderColor,borderWidth,borderRadius,opacity}` | `studio:state-change` | `[data-bind="element-fill|element-border-color|element-border-width|element-border-radius|element-opacity"]` |
| Pattern preset, tint, scale, spacing, opacity, X/Y | `design.update`; global `project.design.pattern.{type,color,scale,spacing,opacity,offsetX,offsetY}` or selected pattern element fields | `studio:state-change` | `[data-bind="pattern-type|pattern-color|pattern-scale|pattern-spacing|pattern-opacity|pattern-offset-x|pattern-offset-y"]` |
| Image ratio and cover/contain | `design.update`; selected image frame `aspectRatio` (`original`, `1:1`, `4:3`, `16:9`) and `cropMode` (`cover`, `contain`). Original frame boxes use the assigned source metadata; the response publishes the canonical `resolvedFrameLayout`. | `studio:state-change` | `[data-bind="image-ratio"]`, `[data-bind="crop-mode"]` |
| Crop reposition and zoom | `strips.set_crop({ stripId, slotIndex, elementId?, crop })`; slot `crop.offsetX`, `crop.offsetY`, `crop.zoom`. Cover offsets pan the source crop; contain offsets reposition the fitted image within its free space or pan it when zoomed beyond the frame. Contain zoom is rendered and clipped to the frame. | `studio:state-change` | Drag a populated, unlocked image frame on `[data-bind="designer-canvas"]`; `[data-bind="crop-zoom"]` |
| Crop reset | One `design.update` restores frame ratio/mode and (when populated) slot mode, ratio, zoom, and offsets through `changes.crop` | `studio:state-change` | `[data-action="crop-reset"]` |
| Align | `design.align({ stripId, elementIds, alignment })`; values `left`, `center` (horizontal), `right`, `top`, `middle` (vertical), `bottom` | `studio:state-change` | `[data-action="element-align"]` with `data-alignment="left|center|right|top|middle|bottom"` |
| Snap | `design.set_snap({ enabled, stripId? })`; active design's `.snap` | `studio:state-change`; drag guides use `studio:snap-guides` | `[data-action="design-snap"]` checkbox |
| Lock/unlock and visibility | `design.update`; element `locked` and `visible` | `studio:state-change` | `[data-action="element-lock"]`, `[data-action="element-toggle"]`; Layers eye/lock buttons forward to these actions |
| Duplicate, delete, reset selected | `design.update` changes `duplicateElementId`, `deleteElementId`, or `resetElementId`; each is undoable. Reset restores the matching active-template element, or neutral type defaults for an added element. | `studio:state-change` | `[data-action="element-duplicate"]`, `[data-action="element-delete"]`, `[data-action="element-reset"]` |
| Add element | `design.add_element({ stripId, elementType })`; supported types: `shape`, `decoration`, `accent`, `border`, `pattern`, `text`; new text is centered and immediately selected | `studio:state-change` | `[data-action="element-add"]` with `data-element-type="text"` or `[data-action="element-add-text"]` |
| Edit text | `design.update({ stripId, changes: { elements: [{ id, text, fontFamily, fontSize, fontWeight, fontStyle, textAlign, verticalAlign, fill, opacity, letterSpacing, lineHeight, ... }] } })`; canvas and strip/PDF renderers wrap and clip text to its box | `studio:state-change`; double-click text on canvas opens the edit prompt; `studio:text-edit` from presentation text controls is forwarded to `design.update` | `[data-bind="text-content|text-font-family|text-font-size|text-font-weight|text-font-style|text-color|text-align|text-vertical-align|text-letter-spacing|text-line-height"]`; `[data-text-align]` buttons; optional `[data-action="element-edit-text"]` |
| Apply selected element to all strips | `design.apply_to_all({ stripId, elementId })`; matches stable `syncKey`, updates matching unlocked elements or creates counterparts, skips locked/invalid targets, preserves slot assignments and crop records; response includes `affectedStrips`, `created`, `updated`, `skippedLocked`, `skippedInvalid` | `studio:state-change`; `studio:design-applied-all` carries the structured response; `studio:applyall-result` carries `{ ok, message, ...result }` for presentation feedback | `[data-action="design-apply-all"]`, `[data-bind="apply-all-status"]` / `[data-applyall-result]` |
| Undo/redo | `design.undo` / `design.redo`; history covers design and crop state. `app.get_state` and the bridge response envelope publish `history.canUndo`, `history.canRedo`; frontend state and every `studio:state-change` detail expose both as top-level booleans too. | `studio:state-change` | `[data-action="design-undo"]`, `[data-action="design-redo"]`; disabled state follows `canUndo` / `canRedo` |
| Pointer resize | One `design.update({ changes: { elements: [{ id, x, y, width, height }] }, label: "Resize element" })` is committed on pointerup; live pointermove previews stay in frontend structured project state and do not create history steps. Minimum width/height is 16 artboard units. | `studio:state-change`; `studio:snap-guides` during resize | `[data-bind="designer-canvas"]`; GLM's click-through `#designer-overlay .overlay-handle[data-dir="nw|n|ne|w|e|sw|s|se"]` positions are hit-tested in screen space |
| Pointer coordinates | Map client points/deltas through the canvas's current `getBoundingClientRect()`, backing canvas dimensions, and artboard letterbox scale. The down-event rectangle anchors the gesture so CSS zoom and stage scroll before interaction do not affect conversion. | `studio:state-change` after committed movement/crop/resize | `[data-bind="designer-canvas"]`; current canvas rectangle is the coordinate source |
| Layers synchronization | No separate API; list values mirror active-strip effective design IDs and `window.__studio_design_snapshot` mirrors the selected override or shared baseline | `studio:state-change`; layer selection also dispatches `studio:element-selected` with `{ id, stripId }` | `[data-layers]`, `[data-bind="design-element-list"]`; select a row, or click its `[data-action="element-toggle"]` / `[data-action="element-lock"]` action |

Canvas drag and resize apply snapping only when the active design's `snap` is enabled. Resize snaps the active edge to strip edges/center and visible peer edges/centers, and sends guides through the existing `studio:snap-guides` event. Numeric X/Y edits are direct and do not snap. Dragging a populated, visible, unlocked image frame repositions its crop; Alt-drag moves the frame itself. A true blank-canvas click clears `selectedElementId` and dispatches `studio:element-selected` with `id: null`; the inspector and Layers selection mirror the cleared state. Locked elements remain selectable but reject geometry, style, crop, align, duplicate, delete, and reset mutations until unlocked. Hidden elements are excluded from pointer hit testing and resize hit testing. Aspect lock starts enabled for the current page session and is not persisted; render the checkbox as checked initially. GLM may surface any documented hook without changing functional method names.

The functional layer advertises `{ design: { addElementType: ["text"], applyToAll: true, liveSync: false } }` through `window.__studio_capabilities` for presentation capability gates. Live sync is not enabled. Text controls may dispatch `studio:text-edit` with `{ id, changes, label }`; app.js validates the selected structured text element and commits it through `design.update`.

## Image Library functional hooks

- Each image carries stable `id`/`internalId`, `originalName`/`displayName`, EXIF-corrected `width`, `height`, numeric `aspectRatio`, `orientation` (`portrait|landscape|square|unknown`), `orderIndex`, `previewFit: "contain"`, `status`, and optional `warning`.
- Image rows expose `data-image-id`, `data-order-index`, `data-orientation`, `data-status`, and `data-thumbnail-state` (`loading|ready|failed`). Reorder/remove actions use `[data-action="image-move-up"]`, `[data-action="image-move-down"]`, `[data-action="image-remove"]` with `data-image-id` or resolve it from the row. Failed cards expose `[data-action="image-thumbnail-retry"]`; `studio:thumbnail-state` carries `{ imageId, state }` so the presentation card can mirror asynchronous changes.
- `images.reorder({ imageIds })` remains the ordering API. OS file drag/drop imports are supported; drag-reordering rows is not currently implemented.
- `studio:state-change` includes `selectedStripId`. Explicit Apply to All is implemented; automatic live sync is not enabled or implemented.
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

### Designer editor overlay (presentation-only)
`#designer-overlay` lives inside the stage content wrapper (`.canvas-content`) and hosts hover bounds, selection bounds, resize handles, badges, and snap guides. It is interface-only and must never be exported, printed, or rasterized. Geometry derives from `window.__studio_design_snapshot` (artboard coordinates) mapped into stage pixel space, so the presentation layer needs no extra functional events.

- Canvas pointerdown selection, movement, crop, and resize belong to `app.js`. `visual.js` may track hover and mirror `studio:element-selected`, but must not select a separate topmost canvas hit on pointerdown; that would override the active Layers selection when elements overlap.
- Selection source of truth is frontend `selectedElementId` mirrored by the parked `[data-bind="design-element-list"]` value. Canvas clicks and Layers rows update it; `studio:element-selected` uses `{ id: string|null }` and `null` clears selection.
- Selection feedback: 1.5px primary-blue bounds plus screen-space handles on all eight corners/edges. Handles are click-through; functional pointer handling hit-tests their screen rectangles and commits resize state through `design.update`.
- Locked elements render dashed amber bounds, a Locked badge, no handles, and an amber `Locked` selection chip. Hidden elements render dashed neutral bounds with a Hidden badge.
- Snap guides consume `studio:snap-guides` (`{ x, y }` artboard coordinates, `null` when inactive) and render as overlay lines; they clear when the Designer view is left.
- `data-selection-chip` / `data-selection-label` in the canvas toolbar mirror the selection name and locked state.
- Inspector action buttons (toggle/lock/duplicate/reset/delete) are disabled for template-structural elements where applicable; the Layers panel hides the eye/lock buttons for locked elements (`is-locked` hides `.layer-actions`).
- Undo/redo refresh: after `design.undo`/`design.redo`, functional code dispatches an `undo` event on the clicked button (see the design-undo/design-redo case); the presentation overlay re-syncs from it.

### Image Library cards (presentation mirror)
`renderImages()` renders functional `<li data-image-id data-orientation data-status data-thumbnail-state>` rows (thumbnail img + `span[data-display-name]` + three text buttons). `visual.js` mirrors each row into a card grid inside `[data-bind="image-list"]`; the functional nodes stay in a hidden `[data-functional-image-list]` bucket so app.js re-renders and the thumbnail observer keep working untouched.

- Cards use the existing `.image-item` classes; thumbnails are a fixed 150px region with `object-fit: contain` (no stretching), filename is single-line ellipsis with `title`, dimensions render on their own secondary line.
- Icon actions reuse the functional buttons' `data-action` (`image-move-up` / `image-move-down` / `image-remove`) and `data-image-id`, and copy their aria-labels; Remove gets an `is-danger` class that only colors on hover/focus.
- The grid never rebuilds when the functional list is unchanged (content stamp), and harvested thumbnail nodes survive rebuilds so async-loaded images do not flash.
- The unloaded `<img>` keeps a layout box so `IntersectionObserver` can start its lazy fetch. The presentation mirror watches direct child changes on the image list, since functional rows may arrive after `studio:state-change` during startup.
- A thumbnail is ready only after its data URI decodes. Failed or timed-out loads show `Thumbnail failed` and a Retry action. Photo originals remain in image `path`; the WebP cache serves only display thumbnails.
- Manual sort shows a grip cue (`.library-stage.is-manual`); the selected-strip-style blue outline + check badge marks the first card as selected presentation (multi-select does not exist yet).
- Once images exist, `.library-stage.has-images` turns the drop hint into a compact strip instead of a large drop area.

### Text element presentation
Inspector sections carry `data-text-only` and reveal only while `fieldset[data-panel="design-properties"]` has `.is-text-element` (set by `visual.js` from the selected element's type). Functional text binds (`text-content`, `text-font-family`, `text-font-size`, `text-font-weight`, `text-color`, `text-align`, `text-vertical-align`, `text-font-style`, `text-letter-spacing`, `text-line-height`) are owned by app.js and change events flow through one `design.update` path. Text alignment buttons (`[data-text-align="left|center|right"]`) are visual mirrors of the parked `[data-bind="text-align"]` select. Canvas double-click editing requests the same in-app dialog contract described below; confirmation calls the existing `design.update` once, while cancel or unchanged text creates no history entry.

### Apply to All Strips (presentation)
The inspector bar `[data-applyall]` holds the exact-label button `[data-action="design-apply-all"]`, a scope row (`Scope: current strip` + `[data-applyall-count]` "N other strips"), and `[data-bind="apply-all-status"]` for Luna's result text. app.js owns confirmation, the `design.apply_to_all` call, and status text; `visual.js` only controls visibility (hidden unless >1 strip), pending state, and success/error coloring from `studio:design-applied-all`. Confirmation uses `window.__studio_dialog.request`; no confirm interception is required. There is no Keep-synced control — automatic live sync is not implemented.

### Strip indicator and selection mirror
`[data-strip-indicator]` in the Designer toolbar renders "Strip N of M" from the functional `[data-bind="strip-list"]` buttons. Functional strip buttons carry no active styling, so `visual.js` marks the selected one (`is-active`) from `studio:state-change` detail via the read-only `window.__studio_state_mirror()` helper (fallback: first strip).

### Async in-app dialog contract
The reusable presentation host is `[data-dialog-root]`, with `aria-modal="true"` and a `role="dialog"` that upgrades to `role="alertdialog"` for destructive, warning, and decision-required specs, title `#studio-dialog-title`, description `#studio-dialog-text`, optional detail `[data-dialog-detail]`, and optional labelled input `[data-dialog-input]`. `visual.js` owns presentation and publishes `window.__studio_dialog.request(spec)`, which returns a Promise. Requests are serialized FIFO so modal backdrops never stack. No raw HTML is accepted: title, message, detail, labels, and input values are assigned as text.

Request shape:
```js
window.__studio_dialog.request({
  kind: 'confirmation' | 'warning' | 'destructive' | 'informational' | 'recovery',
  title: '...', message: '...', detail?: '...',
  confirmLabel?: 'Continue', cancelLabel?: 'Cancel', secondaryLabel?: '...',
  destructive?: true, decisionRequired?: true,
  input?: { type: 'text' | 'search', label: '...', value?: '...', placeholder?: '...',
            required?: true, requiredMessage?: '...', maxLength?: 100, selectOnFocus?: true }
})
```
It resolves to `{ confirmed: boolean, value: string | null, action: 'primary' | 'secondary' | 'cancel' }`. Primary input submission validates required non-whitespace content. Enter submits; Escape and backdrop cancel when allowed. `decisionRequired: true` disables Escape, backdrop, and the cancel button. Focus moves to the input (and optionally selects its value) or primary action, Tab is trapped, and focus returns to the trigger. Failed required validation marks the input `aria-invalid="true"` and renders `[data-dialog-error]` as an inline alert with a warning icon — never a toast. Recovery specs render their `Name · N photos` detail as a structured detail block with the project name emphasized. The dialog controller rejects if its presentation host is unavailable. Functional code must show an application status error and must never fall back to `window.alert`, `window.confirm`, or `window.prompt`.

`app.js` owns workflow continuation. Recovery is decision-required: **Restore Session** invokes `session.restore`; **Start New** invokes `session.start_new`. Template Save As, Duplicate, and Rename gather a trimmed, non-empty name in the input dialog, then call the existing template APIs. Rename pre-fills and selects the existing name. Designer canvas double-click gathers text in the same input dialog; confirm makes one `design.update`, while cancel and unchanged input do not create history. Inspector text controls remain on their existing single change-handler path. Native PyWebView filesystem Open/Save pickers remain native OS dialogs and are not browser dialogs.

The former visual `window.confirm` swizzle has been removed; no trace of it remains in `visual.js`. No presentation or functional code calls a browser dialog API, and no fallback exists — the dialog controller and its queue are the only path.

### Project dirty state
`project.isDirty` is a runtime-only boolean included in project snapshots returned by the backend and in `studio:state-change` detail under `detail.project.isDirty`. It is true after persistent project mutations and recovery restore; autosave/recovery does not mark the explicitly opened project saved. Successful Save, Save As, Open, and new-project initialization publish `false`. The project status chip mirrors this property and shows **Saved** or **Unsaved changes**. `isDirty` is stripped from project documents before persistence. Desktop close is not intercepted for a browser confirmation; the PyWebView closing handler writes recovery when dirty and clears recovery when clean.

### Workflow states and next-step guidance (presentation)
Rail items carry `data-workflow-step` + `[data-rail-state]`; states derive ONLY from published state: images > 0 → Photos done; strips > 0 → Designer done; `validation-status` PASS → Print done / ERROR → attention; preview `src` present → Preview done. Done/attention render an icon (check/warn — not color-only); the current page holds `aria-current="page"`. Next-step cards (`[data-next-step="photos|print"]`) retitle from the same state (e.g. "4 photos imported. Generate a strip to continue."); buttons navigate via the existing `data-vaction` semantics. The Photos right panel groups Strip Setup / Status / Next step; the project chip carries `[data-state="saved|unsaved"]` + a `[data-project-state]` label driven solely by `detail.project.isDirty` (Saved: ok-tinted text and check-green dot; Unsaved changes: muted text and an accent dot — never danger red). A visually hidden `[data-live-region]` announces validation changes, Apply-to-All results, and preview updates.

### Tooltips
Tooltips render into a body-level `#tooltip-host` (z token `--z-tooltip`) so ancestor `overflow: hidden` can never clip them. `[data-tooltip]` remains the only required hook; the legacy CSS pseudo-element rendering is removed. [data-tooltip-side="right"] is currently unused by the shell.

### Z-index tokens
All stacking uses `tokens.css` tokens (`--z-base` … `--z-critical`). New floating layers must slot into that hierarchy instead of raw numbers.

### Events consumed by the presentation layer
`studio:state-change`, `studio:validation-updated`, `studio:preview-updated`, `studio:theme-change`, `studio:job-progress` (reads optional `detail.message` and `detail.progress` as 0–1 or percent), `studio:element-selected` (`detail.id` is the selected element ID), `studio:design-applied-all` (structured Apply-to-All response for result coloring), and `studio:ready` (hides the boot splash early). `studio:preview-scale-change` and `studio:snap-guides` are available for canvas/preview presentation. All are optional; the shell degrades gracefully without them.

Preview toolbar hooks `[data-vaction="pz-fit|pz-100|pz-in|pz-out"]` control only the displayed preview image scale; they never alter physical print dimensions or PDF rendering. Fit and 100% are explicit modes, and zoom in/out adjusts display scale in bounded steps.

### Boot splash
`#boot-splash` covers the UI until `body.is-booted` (set on window `load`, on `studio:ready`, or after a 1.5 s failsafe). Functional code does not manage it.

### Requests for Luna (backend gaps found during the UI audit)

Resolved after functional support landed in `app/backend/api.py` during this pass:

1. **Element delete** — `design.update` accepts `deleteElementId`; the inspector exposes it and the presentation layer disables it for template-structural elements (background/border, `image_frame N`, and the global `pattern` element id).
2. **Per-element reset** — `resetElementId` restores template defaults; exposed next to Delete with the same structural restrictions.

All previously requested items are implemented and verified (final visual pass, all four viewport/theme combos):

3. **Pointer resize** — implemented in `app.js` using the existing click-through overlay handle positions, with all eight directions, minimum dimensions, aspect lock, single undo commit, pointer cancel restoration, and shared snap guides.
4. **Zoom-aware pointer mapping** — implemented in `designer_geometry.mjs`; points and pointer deltas use the live canvas rectangle, backing dimensions, and letterbox mapping at CSS width zoom and after stage scrolling.
5. **Undo/redo availability** — implemented in frontend state as stable `canUndo` / `canRedo` booleans and published in `studio:state-change.detail`; the toolbar buttons disable from these values.
6. **Contain crop consistency** — implemented with normalized free-space offsets and zoom in both the Designer photo draw and shared strip/PDF renderer. Contain image pixels clip to the rounded frame viewport.

Previous gap now closed: crop changes committed through `design.update` (`changes.crop`) are undoable alongside design edits.

### Final verification notes (presentation pass)

- **Print/PDF "zero-size" report**: a hidden-view measurement artifact, not a layout bug. Views use `display:none` until `.is-active`, so rects measured from an inactive workspace return 0x0 (including `[data-action="export-pdf"]`, which lives in the PDF Preview panel). Measured through the real rail navigation flow, the Print sheet renders at full A4-landscape schematic size and the Export button is visible, non-disabled, and hit-testable at its center in every viewport/theme combo.
- `.stage--preview` switched from an auto-tracked grid to a flex column (`flex:1; min-height:0`) so `.preview-page.is-fit`'s `max-height:100%` resolves against a definite height; fit-mode pages no longer clip against `.preview-main` at short viewport heights.
- Overlay handles render after the selection box attaches and compensate for its computed border width, so handle centers sit exactly on the outline anchors (verified across 50%-150% zoom); `updateOverlay` also mirrors synchronously on `studio:element-selected` so locked elements drop their handles in the same tick.

## Notes for functional integration
- `data-panel="app-header"` moved to the topbar `<header>`; project buttons and `project-name` live there.
- `[data-bind="app-status"]` remains the single functional status line and now sits in the status bar.
- `export-pdf`, `print-validate`, `preview-*`, and all designer/template actions exist exactly once with unchanged names.
- Designer property changes read numeric fields only when their corresponding semantic control is present and valid. The GLM inspector may omit advanced numeric controls (currently padding); absent fields are omitted from `design.update`, preserving the model's stored values. Border radius is exposed in the inspector and flows through `design.update`.
- Design and crop mutations are serialized through the functional adapter; rapid property edits send only the changed property so later controls do not restore stale values.
- Print form edits are saved in order. Validation, preview navigation, and PDF export flush the latest physical settings before using the layout.
- `showPreview` assigns the shared preview image source and clears its initial `hidden` state when a rendered page is available.
- Saving an untouched `Untitled Project` with Save As uses the selected filename as the project name; named projects retain their current name.
- Template Save As, Duplicate, Rename, Delete, and Reset are implemented by the documented `templates.*` bridge methods and bind through their existing `data-action` hooks.
- The A4 sheet schematic (`[data-sheet-strips]`) consumes the backend layout placements from `studio:print-layout-updated`; `visual.js` only maps those millimeters to display percentages. `[data-bind="strip-preset"]`, `[data-bind="paper-orientation"]`, `[data-bind="copies-mode"]`, and `[data-bind="strips-per-page"]` drive print settings. `[data-vaction="pz-fit|pz-width|pz-actual|pz-100|pz-in|pz-out"]` change screen viewing only.
