# Strip Print Studio Architecture

## Runtime

- Python owns application state, image processing, physical layout, persistence, validation, and PDF output.
- PyWebView hosts a local HTML/CSS/JavaScript frontend and exposes a single structured bridge entry point: `window.pywebview.api.invoke(method, params)`. A static server bound to loopback serves the frontend modules, including `.mjs` files with a JavaScript MIME type; no remote service is needed.
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

- `project.stripSpec` is the canonical strip geometry: preset, physical millimeters, design width/height, nominal DPI, orientation, and frame count. Template load and explicit preset changes update this object. Print settings expose derived physical strip dimensions for older API clients.
- Strip design uses the specification's logical artboard. Editor and PDF preview zoom are presentation state and never affect physical output size. New preset designs have the same aspect ratio in logical and physical space.
- Raster composition maps logical artboard coordinates into the physical strip raster. In Original mode, each populated image frame resolves from its assigned source ratio; fixed ratio modes keep their target frame geometry and apply cover/contain inside it.
- Image import records EXIF-corrected dimensions, aspect ratio, orientation, stable internal identity, original display name, and contain-fit preview guidance. Thumbnails and strip frames preserve source proportions; cover crops excess, contain shows the full image, and neither changes physical strip dimensions.
- Shared/template design remains in `project.design`; edits scoped to a strip are full structured `designOverride` records on that strip. Elements use stable `syncKey` values so Apply to All can update/create a logical counterpart without changing slot assignments or crop records. The complete propagation is one history transaction.
- Text is a structured design element. Canvas and raster strip/PDF renderers use the controlled Arial, Georgia, and Courier New families, with wrapping, clipping, alignment, color, opacity, letter spacing, and line height stored in design state. Custom templates persist text without photo assignments.
- Image crop rectangles are normalized to each source image; design elements use artboard coordinates and stable IDs/sync keys.
- Print layout uses millimeters from A4 landscape `297 × 210` or portrait `210 × 297`; PDF points are derived only at the PDF boundary (`72 / 25.4` points per mm).
- The same `print_layout` result is consumed by the PDF preview and PDF writer. Auto count uses safe upright capacity; excess manual requests report a fit error and safe page count. Strip boxes never shrink.
- The PDF places each rendered raster into the exact configured millimeter box. Raster integer-pixel rounding must not silently reduce the measured strip body.
- PDF output preserves vertical strip orientation and uses a partial final page without resizing strips.

## Initial engineering decisions

- Python 3.11+; PyWebView for the Windows desktop host.
- Pillow for image metadata, thumbnails, and raster composition; ReportLab for deterministic PDF placement and page dimensions.
- Windows uses PyWebView's Edge Chromium renderer and requires the Microsoft Edge WebView2 Runtime.
- JSON for versioned template/project/preset data. Imported originals are referenced rather than duplicated by default.
- The source DCapture reference used a 1200 × 1600 logical sheet with a warm off-white background and four photo areas. Saved legacy projects retain those coordinates and their original physical size. Explicit preset selection maps their element rectangles through normalized coordinates.
- New projects and the built-in `DCapture Clean` template use the `Photobooth 2 × 6 in` specification: 600 × 1800 logical units, 50.8 × 152.4 mm, four equal locked placeholder frames that adapt per populated source. The optional 54 × 180 mm and custom specifications keep user choice explicit. Default incomplete group behavior is `empty`; print page defaults to A4 landscape, Auto copies, 5 mm margins, 3 mm gap, and cut guides.

## Integration flow

`PyWebView → api.invoke → allowlisted service method → structured result → frontend state update`. Frontend hooks and event/state conventions are specified in [FRONTEND_CONTRACT.md](FRONTEND_CONTRACT.md). UI styling and layout remain independent of backend services.
