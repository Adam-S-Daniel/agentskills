---
name: compare-pdfpairs
description: >
  Compare pairs of PDFs (name.pdf + name<suffix>.pdf in the same folder) to
  determine whether they would produce identical printouts and whether their
  embedded text differs — e.g. to safely delete redundant "-signed" or
  "-needsocr" duplicates. Recursively finds every pair under a directory,
  rasterizes pages and compares hashes, and diffs extracted text. Triggers:
  "compare pdf pairs", "find duplicate pdfs", "are these pdfs identical",
  "which suffixed pdfs can I delete", "dedupe scanned pdfs".
compatibility: Requires PowerShell 7+ and poppler-utils (pdftoppm, pdftotext) on PATH
---

# Compare PDF Pairs

Determine, for every `name.pdf` + `name<suffix>.pdf` pair under a directory,
whether the two files print identically and how their text layers differ.
"Identical printout" means byte-identical 150-DPI rasters of every page
(pdftoppm output is deterministic); visually-similar-but-not-identical PDFs
report false.

## Preflight

The script fails fast with a clear error if `pdftoppm` or `pdftotext` are not
on PATH. Verify before running if unsure:

```bash
command -v pdftoppm pdftotext || echo "install poppler-utils first"
pwsh -v   # needs PowerShell 7+
```

## Usage

```powershell
# Report all pairs and their comparison results
$results = . ./scripts/Compare-PdfPairs.ps1 -Directory <root> -Suffix '-needsocr'

# Show pairs that are NOT redundant (differ in printout or text)
$results | ? { -not $_.IdenticalPrintout -or -not $_.IdenticalText } |
    Select Original, Suffixed, PageCountA, PageCountB, IdenticalPrintout,
           TextLengthA, TextLengthB, IdenticalText, TextDiffLines

# Preview deleting suffixed files that are safe to remove (identical printout,
# and identical text OR the suffixed copy has no text layer at all)
$results | ? { $_.IdenticalPrintout -and ($_.IdenticalText -or ($_.TextLengthA -gt 0 -and $_.TextLengthB -eq 0)) } |
    Select -ExpandProperty Suffixed | Remove-Item -WhatIf
```

Parameters: `-Directory` (root, searched recursively), `-Suffix` (the string
before `.pdf` on one file of each pair, e.g. `-signed`), `-ThrottleLimit`
(parallel comparisons, defaults to processor count).

## Rules

- Never delete files without the user's explicit confirmation — always show
  the `-WhatIf` preview (or the result table) first.
- Related: use `rename-pdfs` afterwards to give surviving PDFs descriptive
  names.
