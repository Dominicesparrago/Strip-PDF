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

Run automated checks with `python -m pytest`. New projects use a 2 × 6 in (50.8 × 152.4 mm) vertical strip with four adaptive photo frames. Legacy projects preserve their saved geometry until a different strip preset is selected.
