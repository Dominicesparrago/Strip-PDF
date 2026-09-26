# Strip Print Studio

Windows desktop application for importing photos, designing reusable photobooth strips, arranging strips on A4 landscape pages, and exporting print-ready PDFs.

## Development

Requires Python 3.11+ and the packages in `requirements.txt`.
On Windows, install the Microsoft Edge WebView2 Runtime before launch; PyWebView uses it for its Chromium renderer.

```powershell
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
python -m app.main
```

Run automated checks with `python -m pytest`. The initial physical strip default is 54 × 72 mm (3:4), because the DCapture reference provides a 1200 × 1600 px result but no physical print measurement. Change strip size in the print settings when the intended stock measurement is known.
