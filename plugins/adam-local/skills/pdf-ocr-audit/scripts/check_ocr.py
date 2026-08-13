#!/usr/bin/env python3
"""
check_ocr.py — Determine whether PDF files have embedded text layers.

Usage:
    python check_ocr.py /path/to/folder [--timeout SECONDS]
    python check_ocr.py file1.pdf file2.pdf [--timeout SECONDS]

Output: CSV to stdout with columns:
    path, total_pages, text_pages, verdict, notes

Verdicts:
    No       — All pages have embedded text (fully searchable)
    Yes      — No pages have embedded text (image scan, needs OCR)
    Partial  — Some pages have text, some don't
    Error    — File could not be read
"""

import sys
import os
import csv
import argparse
import signal
import subprocess
from pathlib import Path


def ensure_pypdf():
    try:
        import pypdf  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pypdf", "--break-system-packages", "-q"]
        )


ensure_pypdf()

import warnings
warnings.filterwarnings("ignore")

from pypdf import PdfReader  # noqa: E402


class TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise TimeoutError("Timed out")


def check_pdf(path: str, timeout: int = 0):
    """
    Returns (total_pages, text_pages, verdict, notes).
    verdict is one of: 'No', 'Yes', 'Partial', 'Error'
    """
    if timeout > 0 and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout)

    try:
        reader = PdfReader(path)
        total_pages = len(reader.pages)
        text_pages = 0
        for page in reader.pages:
            try:
                text = page.extract_text()
                if text and text.strip():
                    text_pages += 1
            except Exception:
                pass  # count as no text on this page

        if timeout > 0 and hasattr(signal, "SIGALRM"):
            signal.alarm(0)

        if total_pages == 0:
            return 0, 0, "Error", "No pages found"

        if text_pages == total_pages:
            verdict = "No"
            notes = f"{text_pages}/{total_pages} pages have embedded text"
        elif text_pages == 0:
            verdict = "Yes"
            notes = f"0/{total_pages} pages have embedded text — image-only scan"
        else:
            verdict = "Partial"
            image_pages = total_pages - text_pages
            notes = f"{text_pages}/{total_pages} pages have text; {image_pages} page(s) are image-only"

        return total_pages, text_pages, verdict, notes

    except TimeoutError:
        return None, None, "Error", f"Timed out after {timeout}s"
    except Exception as e:
        err_msg = str(e)
        if "Input/output error" in err_msg or "Errno 5" in err_msg:
            notes = "I/O error — file may not be locally synced (OneDrive cloud-only?)"
        elif "Invalid argument" in err_msg or "Errno 22" in err_msg:
            notes = "Invalid argument error — file may not be locally cached"
        elif "password" in err_msg.lower() or "encrypt" in err_msg.lower():
            notes = "Password-protected or encrypted PDF"
        else:
            notes = f"Error: {err_msg[:120]}"
        return None, None, "Error", notes
    finally:
        if timeout > 0 and hasattr(signal, "SIGALRM"):
            signal.alarm(0)


def collect_pdfs(paths):
    """Expand directories and return list of PDF file paths."""
    result = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for pdf in sorted(path.rglob("*.pdf")):
                result.append(str(pdf))
            for pdf in sorted(path.rglob("*.PDF")):
                result.append(str(pdf))
        elif path.is_file():
            result.append(str(path))
        else:
            print(f"# Warning: {p} not found", file=sys.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(description="Audit PDFs for OCR readiness")
    parser.add_argument("paths", nargs="+", help="Files or directories to scan")
    parser.add_argument("--timeout", type=int, default=30,
                        help="Seconds before giving up on a single file (0=no limit, default=30)")
    args = parser.parse_args()

    pdfs = collect_pdfs(args.paths)
    if not pdfs:
        print("# No PDF files found", file=sys.stderr)
        sys.exit(1)

    print(f"# Scanning {len(pdfs)} PDF(s)...", file=sys.stderr)

    writer = csv.writer(sys.stdout)
    writer.writerow(["path", "total_pages", "text_pages", "verdict", "notes"])

    counts = {"No": 0, "Yes": 0, "Partial": 0, "Error": 0}

    for i, pdf_path in enumerate(pdfs, 1):
        if i % 50 == 0:
            print(f"# Progress: {i}/{len(pdfs)}", file=sys.stderr)
        total, text, verdict, notes = check_pdf(pdf_path, timeout=args.timeout)
        counts[verdict] = counts.get(verdict, 0) + 1
        writer.writerow([pdf_path, total or "", text or "", verdict, notes])
        sys.stdout.flush()

    print(
        f"# Done. No OCR needed: {counts['No']}, Needs OCR: {counts['Yes']}, "
        f"Partial: {counts['Partial']}, Errors: {counts['Error']}",
        file=sys.stderr
    )


if __name__ == "__main__":
    main()
