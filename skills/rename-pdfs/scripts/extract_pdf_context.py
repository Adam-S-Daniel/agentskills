#!/usr/bin/env python3
"""
Extract first-N-pages text + date candidates from a PDF, to inform a rename proposal.

Usage:
    python3 extract_pdf_context.py /path/to/file.pdf [--pages 3] [--max-chars 4000]

Output (stdout, JSON):
    {
      "path": "...",
      "filename": "...",
      "text": "<first N pages, truncated>",
      "dates_in_text": ["2024-03-15", ...],
      "dates_in_filename": ["2024-03-15", ...],
      "mtime": "2024-03-16"
    }
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

try:
    import pypdf
except ImportError:
    sys.stderr.write(
        "Missing pypdf. Install with: pip install pypdf --break-system-packages\n"
    )
    sys.exit(2)


# Each pattern returns (year, month, day) from match.groups(); 'long' uses the
# month-name first group, handled specially below.
DATE_PATTERNS = [
    # ISO-ish: 2024-03-15 or 2024/03/15
    (re.compile(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b"), "iso"),
    # Compact: 20240315 — digit-boundary so it matches inside `Scan_20240315_001.pdf`
    # (underscores are word chars, so \b doesn't fire across them).
    (re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"), "compact"),
    # US: 03/15/2024 or 3-15-2024
    (
        re.compile(r"\b(0?[1-9]|1[0-2])[/\-](0?[1-9]|[12]\d|3[01])[/\-](20\d{2})\b"),
        "us",
    ),
    # Long: March 15, 2024 / Mar 15 2024 / Sept. 1, 2024
    (
        re.compile(
            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
            r"Dec(?:ember)?)\.?\s+(0?[1-9]|[12]\d|3[01]),?\s+(20\d{2})\b",
            re.IGNORECASE,
        ),
        "long",
    ),
]

MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        1,
    )
}


def _normalize(match, kind):
    g = match.groups()
    try:
        if kind == "iso":
            y, m, d = int(g[0]), int(g[1]), int(g[2])
        elif kind == "compact":
            y, m, d = int(g[0]), int(g[1]), int(g[2])
        elif kind == "us":
            m, d, y = int(g[0]), int(g[1]), int(g[2])
        elif kind == "long":
            mname = g[0][:3].lower()
            m = MONTHS.get(mname)
            if not m:
                return None
            d, y = int(g[1]), int(g[2])
        else:
            return None
        datetime(year=y, month=m, day=d)
        return "{:04d}-{:02d}-{:02d}".format(y, m, d)
    except (ValueError, IndexError):
        return None


def find_dates(text):
    seen = []
    for pat, kind in DATE_PATTERNS:
        for m in pat.finditer(text):
            iso = _normalize(m, kind)
            if iso and iso not in seen:
                seen.append(iso)
    return seen


def extract_text(path, max_pages):
    try:
        r = pypdf.PdfReader(path)
        pages = r.pages[:max_pages]
        return "\n---\n".join((p.extract_text() or "") for p in pages)
    except Exception as e:
        sys.stderr.write("pypdf error reading {}: {}\n".format(path, e))
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--max-chars", type=int, default=4000)
    args = ap.parse_args()

    path = args.pdf
    text = extract_text(path, args.pages)
    if len(text) > args.max_chars:
        text = text[: args.max_chars] + "\n[truncated]"

    fname = os.path.basename(path)
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    except OSError:
        mtime = None

    out = {
        "path": path,
        "filename": fname,
        "text": text.strip(),
        "dates_in_text": find_dates(text),
        "dates_in_filename": find_dates(fname),
        "mtime": mtime,
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
