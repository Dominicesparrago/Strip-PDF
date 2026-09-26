$ErrorActionPreference = 'Stop'

python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
python -m PyInstaller --clean --noconfirm --windowed `
  --name StripPrintStudio `
  --add-data "frontend;frontend" `
  --add-data "templates;templates" `
  --collect-all webview `
  --collect-all PIL `
  --collect-all reportlab `
  app\main.py

Write-Host "Build complete: dist\StripPrintStudio\StripPrintStudio.exe"
