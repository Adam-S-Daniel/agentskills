"""Unit tests for find_dates() in extract_pdf_context.

Run:
    python3 -m pytest scripts/test_extract_pdf_context.py -v
or:
    python3 scripts/test_extract_pdf_context.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from extract_pdf_context import find_dates  # noqa: E402


class TestFindDates(unittest.TestCase):
    def test_iso_dash(self):
        self.assertEqual(find_dates("Statement Date: 2024-03-15"), ["2024-03-15"])

    def test_iso_slash(self):
        self.assertEqual(find_dates("dated 2024/03/15 issued"), ["2024-03-15"])

    def test_compact(self):
        self.assertEqual(find_dates("Scan_20240315_001.pdf"), ["2024-03-15"])

    def test_us_slash(self):
        self.assertEqual(find_dates("Invoice Date 03/15/2024"), ["2024-03-15"])

    def test_us_dash_single_digit(self):
        self.assertEqual(find_dates("Issued 3-5-2024"), ["2024-03-05"])

    def test_long_full_month(self):
        self.assertEqual(find_dates("on March 15, 2024 we"), ["2024-03-15"])

    def test_long_abbrev_no_comma(self):
        self.assertEqual(find_dates("Mar 15 2024"), ["2024-03-15"])

    def test_long_with_period(self):
        self.assertEqual(find_dates("Sept. 1, 2024"), ["2024-09-01"])

    def test_dedupe_and_order_preserved(self):
        # Two different dates, oldest first; should appear in encounter order, deduped.
        result = find_dates("date 2024-03-15 then later 03/15/2024 and 2024-04-01")
        self.assertEqual(result, ["2024-03-15", "2024-04-01"])

    def test_no_match(self):
        self.assertEqual(find_dates("no dates here at all"), [])

    def test_rejects_invalid_day(self):
        # 2024-02-30 isn't a real date and should be dropped.
        self.assertEqual(find_dates("bogus 2024-02-30"), [])

    def test_ignores_too_old_year(self):
        # Patterns only match 20xx, so 1999 should be ignored.
        self.assertEqual(find_dates("legacy date 1999-12-31"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
