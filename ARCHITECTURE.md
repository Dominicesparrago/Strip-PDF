# Strip Print Studio Architecture

## Runtime

- Python owns application state, image processing, physical layout, persistence, validation, and PDF output.
- PyWebView hosts a local HTML/CSS/JavaScript frontend and exposes a single structured bridge entry point: `window.pywebview.api.invoke(method, params)`. A static server bound to loopback serves the frontend modules; no remote service is needed.
- PyWebView's Python DOM drop handler extracts `pywebviewFullPath` values and dispatches `studio:files-dropped`; the existing import API handles validation and background processing.
- Frontend code owns interaction and visualization. It does not calculate physical print placement or read/write project files directly.
- Expensive image/PDF tasks run in a worker pool. The UI queries job state through the bridge; all bridge results use `{ "ok": true, "data": ... }` or `{ "ok": false, "error": ..., "detail": ... }`.

## Source layout

```text
app/
  main.py                 Process entry point
  desktop_shell.py        PyWebView window setup and lifecycle
  backend/
    api.py                Allowlisted bridge dispatcher
    models.py             Typed project, image, strip, template, and print data
    image_service.py      Import validation and thumbnail cache
    sorting_service.py    Natural, filename, date, number, and manual order
    strip_service.py      Strip grouping and crop state
    strip_renderer.py    Lossless, high-resolution strip composition
    template_service.py   Built-in/custom templates and design history
    project_service.py    Project lifecycle and recovery
    persistence.py        Versioned JSON and atomic writes
    print_layout.py       Millimeter-based A4 layout and fit calculations
    validation_service.py Print/readiness checks and DPI estimates
    pdf_service.py        Shared preview/export geometry and PDF rendering
frontend/
  index.html              GLM-owned presentation shell
  css/                    GLM-owned presentation styles
  js/
    app.js                LUNA-owned functional UI/API bindings
    api.js, state.js      LUNA-owned bridge and frontend state
    visual.js             GLM-owned presentation-only visual/event adapter
templates/builtin/        Read-only built-in template definitions and assets
templates/custom/         Per-user custom templates (user data directory)
projects/                 Optional workspace-local project examples only
exports/                  Optional workspace-local exports only
tests/                    Calculation, persistence, and integration tests
```

Runtime user data belongs under `%LOCALAPPDATA%/StripPrintStudio` (projects, custom templates, thumbnail cache, recovery, and presets). Project files reference original image paths; missing paths remain visible as recoverable validation errors. Writes use temporary files followed by atomic replacement where supported.

Application theme mode is stored separately from project contents so Light/Dark/System selection survives opening or starting projects. Recovery snapshots are retained for unsaved changes and cleared after a successful project save/open or a clean shutdown.

## Data and coordinate rules

- Strip design uses a fixed logical artboard with a physical size in millimeters stored in template metadata. Editor zoom is presentation state and never affects physical output size.
- Raster composition fits the artboard inside the configured physical strip raster, preserving its proportions if a custom physical width/height has a different ratio.
- Image crop rectangles are normalized to each source image; design elements use artboard coordinates and stable IDs.
- Print layout uses millimeters from A4 landscape dimensions `297 × 210`; PDF points are derived only at the PDF boundary (`72 / 25.4` points per mm).
- The same `print_layout` result is consumed by the PDF preview and PDF writer. Strip count is validated against page margins, physical strip width, and gaps; invalid counts are not auto-scaled.
- The PDF places each rendered raster into the exact configured millimeter box. Raster integer-pixel rounding must not silently reduce the measured strip body.
- PDF output preserves vertical strip orientation and uses a partial final page without resizing strips.

## Initial engineering decisions

- Python 3.11+; PyWebView for the Windows desktop host.
- Pillow for image metadata, thumbnails, and raster composition; ReportLab for deterministic PDF placement and page dimensions.
- Windows uses PyWebView's Edge Chromium renderer and requires the Microsoft Edge WebView2 Runtime.
- JSON for versioned template/project/preset data. Imported originals are referenced rather than duplicated by default.
- The sibling DCapture result implementation creates a 1200 × 1600 px (3:4) warm off-white sheet (`#f6f3eb`) with four vertically stacked photo areas and 18 px outer/inter-slot gaps. It contains no text/decorative overlays in the current result renderer. The built-in `DCapture Clean` reproduces that structure as editable vector/design data, without embedding user photographs.
- DCapture defines pixels/proportions but no physical print measurement. The configurable initial physical default is therefore 54 × 72 mm (the exact 3:4 reference ratio); this assumption is visible in built-in template metadata and print settings. Default incomplete group behavior is `empty`; default print page is A4 landscape, five strips per page, with editable physical strip width/margins/gaps.

## Integration flow

`PyWebView → api.invoke → allowlisted service method → structured result → frontend state update`. Frontend hooks and event/state conventions are specified in [FRONTEND_CONTRACT.md](FRONTEND_CONTRACT.md). UI styling and layout remain independent of backend services.
