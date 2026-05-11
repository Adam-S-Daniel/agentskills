---
name: rename-pdfs
description: >
  Rename already-searchable PDFs in a specified folder to descriptive, date-prefixed names,
  proposing each name from the PDF's own content and prompting for per-file
  confirmation or edit before applying. Use after running `ocr-pdfs` to clean up
  scanner-output filenames like "Scan from 2024-03-15.pdf", "QuickScan_001.pdf",
  or "Document(47).pdf" — or for any folder of PDFs that already have text layers
  but unhelpful filenames. Triggers: "rename my pdfs", "clean up pdf filenames",
  "rename searchable pdfs", "give my pdfs descriptive names",
  "rename scanned pdfs", "tidy pdf names".
tools:
  - Bash
  - Read
---

# Rename Searchable PDFs Interactively

Walk through PDFs in a folder, propose a meaningful filename from each document's content, and confirm or edit the proposal with the user before applying it.

This skill is the natural follow-up to `ocr-pdfs`: once a batch of scans is searchable, the filenames are usually still scanner-junk. It renames them in place.

## Naming convention

`YYYYMMDD-Type-Issuer-Title.pdf`

Four hyphen-separated fields, in this exact order, **no spaces around the hyphens, no hyphens inside the date**:

1. **Document date** — `YYYYMMDD` (compact, no internal hyphens). Pulled from the document body when possible (statement date, invoice date, letterhead date, "as of" date), then the filename, then the file's mtime as a last resort. Always tell the user when falling back to mtime.

   **Date range variant.** For documents that summarize a span of dates rather than a single moment — superbills, multi-month statements, range receipts — use `YYYYMMDD-YYYYMMDD` (earliest–latest). The internal hyphen is intentional; downstream parsing rules below handle it.

2. **Document type** — a short noun for what the document _is_, in Title Case. Examples: `Statement`, `Invoice`, `Bill`, `Receipt`, `Letter`, `Contract`, `Form 1099`, `Form W-2`, `Closing Disclosure`, `Lease`, `Policy`, `Report`, `Tax Return`, `Superbill`. Keep it canonical so similar documents sort together.
3. **Document issuer** — the organization or person who produced the document. Title Case. Examples: `Bank of America`, `Verizon Wireless`, `Acme Corp`, `Fidelity`, `IRS`, `Dr Patel`, `Harry H Huang MD`. Use a recognizable short form (`BofA` is fine if that's what the user calls it; ask if unsure). Keep credential suffixes uppercase (`MD`, `DDS`, `CPA`, `LLC`).
4. **Document title / specifier** — a concise identifier that distinguishes this doc from other same-type/same-issuer docs. Examples: `Checking Account`, `Account 555 1234`, `Year End Summary`, `Invoice 4471`, `Sophia Daniel`, `Jonah Daniel Rx $284.68`. Use a space (not a hyphen) between words.

**Critical: the field separator is a single hyphen with no surrounding spaces.** The date is `YYYYMMDD` (or `YYYYMMDD-YYYYMMDD` for ranges); it is purely numeric, so it never accidentally captures a hyphen meant as a field separator. Inside the type/issuer/title fields, **never introduce a hyphen** — use a space, em dash (`—`), or word like "and" instead. E.g. write `Year End` or `Year—End`, not `Year-End`.

Parsing reference: split on `-`. The first token is the date. If the next token also looks numeric and 8 digits, the file uses the date-range variant and tokens 0 + 1 form the date field; otherwise tokens 1 / 2 / 3 are type / issuer / title. Anything past those is title overflow (rejoin with `-`).

**Filesystem hygiene:** strip `/ \ : * ? " < > |`. Apostrophes, parentheses, ampersands, commas, dollar signs, and em dashes are fine on Windows and macOS. Periods inside a field are fine but discouraged (`Dr. Patel` → `Dr Patel`).

**Examples (single date):**

```
20240315-Statement-Bank of America-Checking Account.pdf
20240401-Invoice-Acme Corp-Web Development Services.pdf
20241231-Form 1099 INT-Fidelity-Brokerage Year End Summary.pdf
20240630-Closing Disclosure-First American Title-123 Main St.pdf
20240214-Letter-Dr Patel-Lab Results Follow Up.pdf
20260130-Receipt-Millbrook Pharmacy-Jonah Daniel Rx $195.07.pdf
20260107-Statement-Harry H Huang MD-Statement For Jodi Daniel.pdf
```

**Examples (date range):**

```
20260114-20260119-Superbill-Center for Anxiety and Behavioral Change-Sophia Daniel.pdf
20260202-20260223-Superbill-Center for Anxiety and Behavioral Change-Sophia Daniel.pdf
```

If the document genuinely lacks one of the fields (e.g. a published report with no clear issuer), use `Unknown` for that slot and surface it to the user during the per-file confirmation so they can fill it in.

### When to use a date range vs. a single date

Use a **single** `YYYYMMDD` when one date dominates the document:

- a single statement/billing date
- a single invoice or receipt
- a letter or report dated on one day

Use a **range** `YYYYMMDD-YYYYMMDD` when the document _is fundamentally about a span_:

- a superbill listing multiple appointments
- a multi-month account statement where the period matters more than the issue date
- a travel receipt covering multiple nights

When in doubt, single date wins — the document-generation date is usually the right answer.

## What to skip

- **`*-needsocr.pdf`** — these are pre-OCR backups produced by the `ocr-pdfs` skill. They must stay paired with their searchable counterpart; rename them only if the user explicitly asks (and then to the same new base name plus the `-needsocr` suffix).
- **Image-only or inaccessible PDFs** — verdicts "Yes" or "Inaccessible" from `pdf-ocr-audit`. Content extraction isn't reliable. Tell the user and offer to run `ocr-pdfs` first.
- The rename log file itself (`pdf-rename-log-*.csv`).

## Workflow

1. **Confirm scope.**
   - Folder path.
   - Recurse into subdirectories? (default: no)
   - Naming convention: default or custom?
   - Dry-run first? (recommended for >20 files)

2. **Enumerate candidates.** Use `find` or `Glob` for `*.pdf`, case-insensitive, excluding `*-needsocr.pdf` and `pdf-rename-log-*.csv`. Print the count before starting.

3. **(Optional) Verify searchability.** If any file looks uncertain, run the `pdf-ocr-audit` script. Only proceed with files whose verdict is "No" (already searchable) or "Partial" with searchable first-page content.

4. **Per-file loop.** For each PDF:

   a. Run `scripts/extract_pdf_context.py` to get first-3-pages text + date candidates from content / filename / mtime.

   b. Propose a new filename following the agreed convention.

   c. Present to the user exactly like this:

   ```
   File 7 of 42
   Current:   Scan from 2024-03-15 (3).pdf
   Proposed:  20240315-Bill-Verizon Wireless-Account 555 1234.pdf
              ^date    ^type ^issuer         ^title
   First page: "Account Number 555-1234  Statement Date: March 15, 2024  Total Due: $84.27 ..."

   [Enter] accept · type a new name to edit · "s" skip · "q" quit · "auto" accept all remaining
   ```

   When proposing, label the four fields so the user can edit just one by typing e.g.
   `type=Statement` or `issuer=Verizon` and accepting the rest. Also accept a full
   replacement filename.

   d. Wait for the user's reply. Apply the rename, skip, or quit accordingly. If they type `auto`, switch to accept-all mode but still log each action and stop on any error.

5. **Collision handling.** Before renaming, check whether the target exists in the destination folder. If it does, append ` (2)`, ` (3)`, etc., and surface the collision to the user in the next message.

6. **Log everything.** Append each action to `pdf-rename-log-YYYY-MM-DD.csv` in the same folder (the log filename keeps the human-readable ISO date for at-a-glance scanning — only PDF filenames follow the compact `YYYYMMDD` rule). Columns: `timestamp, original_path, new_path, action, notes`. Actions: `renamed`, `skipped`, `collision-resolved`, `errored`, `dry-run-only`.

7. **Summary.** At the end, report: N renamed, N skipped, N errored. Offer to undo by replaying the log in reverse (read the CSV, swap original ↔ new, re-rename).

## Extracting PDF text + date hints

Use the helper:

```bash
python3 scripts/extract_pdf_context.py "/path/to/file.pdf" --pages 3
```

It outputs JSON with `text`, `dates_in_text`, `dates_in_filename`, and `mtime`. If `pypdf` is missing:

```bash
pip install pypdf --break-system-packages
```

For folders with mostly long PDFs, `--pages 2` is faster and usually enough.

## Date selection priority

1. **Document body** — `Statement Date`, `Invoice Date`, `Date:`, ISO dates, US dates, or "Month DD, YYYY". When multiple dates appear, prefer the most prominent (header/footer) or the latest one.
2. **Filename** — `YYYY-MM-DD`, `YYYYMMDD`, `MM-DD-YYYY` patterns.
3. **File mtime** — `stat -c %y "$file" | cut -d' ' -f1`. Last resort; tell the user.

## Safety

- **Never batch-rename without per-file confirmation** unless the user explicitly types `auto` mid-session.
- **Offer dry-run** for any folder over ~20 files: print all proposals first, ask for go-ahead, then apply.
- Renames are in-place (same folder). Moving into a structured archive is a separate task.
- Refuse to operate on files outside the user-specified folder, even if symlinks point elsewhere.

## Files

| File | Purpose |
|------|---------|
| `scripts/extract_pdf_context.py` | Prints first-N-pages text + detected date candidates as JSON, to inform a rename proposal. |

## Relationship to other skills

| Skill | How it fits |
|-------|-------------|
| `pdf-ocr-audit` | Run first to confirm a folder's PDFs are searchable. |
| `ocr-pdfs` | Run before this skill on any folder that contains scans. Leaves `-needsocr.pdf` backups, which this skill skips. |
| `rename-pdfs` (this) | Final pass — turn scanner-junk names into descriptive ones. |
