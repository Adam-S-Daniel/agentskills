---
name: ocr-pdfs
description: Batch-OCR scanned PDFs flagged as needing OCR, then visually review results with a WPF side-by-side comparison tool.
metadata:
  version: "1.0.0"
  tools: "Bash, Read, Write, WebSearch"
  triggers: "ocr my pdfs; run OCR on scanned PDFs; batch OCR pipeline; process scanned documents; make PDFs searchable; ocr-pdfs"
---

# OCR PDF Batch Pipeline

This skill runs OCR on scanned PDFs using **OCRmyPDF** (backed by Tesseract) and provides a **WPF PowerShell tool** for visually reviewing before/after results.

## Quick Start

### 1. Generate an audit CSV (if not already done)
Use the `pdf-ocr-audit` skill to produce `ocr-audit-results-full-YYYY-MM-DD.csv` with columns:
`path, total_pages, text_pages, verdict, notes`

### 2. Install OCRmyPDF (Linux/WSL)
```bash
# Python 3.11+ → latest version
pip install ocrmypdf --break-system-packages

# Python 3.10 (Ubuntu 22.04 default in VM)
pip install ocrmypdf==16.13.0 --break-system-packages

# Verify
ocrmypdf --version
```

### 3. Run the batch OCR script
```bash
python3 ocr_pdfs.py \
  --csv  /path/to/ocr-audit-results-full-YYYY-MM-DD.csv \
  --log  /path/to/ocr-progress.log \
  --workers 2

# Dry-run first to preview
python3 ocr_pdfs.py --dry-run

# Resume from file N (0-based)
python3 ocr_pdfs.py --start-at 150
```

**What the script does per file:**
1. Renames `document.pdf` → `document-needsocr.pdf`
2. Runs `ocrmypdf --skip-text` on the backup → outputs `document.pdf`
3. Logs result to `ocr-progress.log`
4. Skips files where `-needsocr.pdf` + OCR output already exist
5. On failure: restores original, logs error, continues

### 4. Review results with the WPF comparison tool (Windows)

**Prerequisites:**
```powershell
winget install oschwartz10612.poppler   # provides pdftoppm
```

**Run the reviewer:**
```powershell
.\Compare-OcrPdfs.ps1 `
  -FolderPath "C:\Users\passp\OneDrive\OurOneDriveStuff" `
  -FrameDelay 500 `
  -StartAt 0
```

**Key bindings:**

| Key | Action |
|-----|--------|
| `K` | Keep — leave backup, advance to next |
| `D` | Delete backup (`-needsocr.pdf`), advance |
| `←` / `→` | Step through pages manually |
| `Space` | Pause / resume animation |
| `Q` | Quit reviewer |

## Files

| File | Purpose |
|------|---------|
| `ocr_pdfs.py` | Batch OCR runner (Python 3.10+) |
| `Compare-OcrPdfs.ps1` | WPF side-by-side review tool (Windows PowerShell 5+) |

## Notes

- **Version cool-off rule:** Do not install any OCRmyPDF version released within the last 7 days. If needed, pin to the previous stable release.
- **Path remapping:** The script auto-translates session-scoped VM paths (e.g. `/sessions/old-session/mnt/...`) to the current mount point.
- **Parallelism:** Default `--workers 2` is conservative. On a machine with 8+ cores and fast storage, `--workers 4` is safe.
- **Tesseract language packs:** English is installed by default (`tesseract-ocr-eng`). Add more: `sudo apt install tesseract-ocr-fra` etc.
- **Already-processed check:** A file is skipped if both `document-needsocr.pdf` (backup) and `document.pdf` (OCR output) exist and are non-empty.

## Dependencies

| Tool | Install |
|------|---------|
| `tesseract-ocr` | `sudo apt install tesseract-ocr` |
| `ocrmypdf` | `pip install ocrmypdf==16.13.0 --break-system-packages` |
| `pdftoppm` (Poppler) | `winget install oschwartz10612.poppler` (Windows) |
| Python 3.10+ | pre-installed in VM |
| PowerShell 5+ | pre-installed on Windows 10/11 |
