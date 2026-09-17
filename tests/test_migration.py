"""Test workbook parsing without creating or modifying spreadsheet files."""
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import patch
import unittest

from inventory.migration import DEFAULT_PROFILE, prepare


CUTOFF = (date.today() - timedelta(days=2)).isoformat()
TODAY = date.today().isoformat()


class Sheet:
    def __init__(self, title, rows):
        self.title, self.rows = title, rows

    def iter_rows(self, **kwargs):
        return iter(self.rows)


class Book(dict):
    def close(self):
        pass


def workbooks(balance=8, ref="001.TEST", extra_rows=()):
    dashboard = Book({
        "Base Supply": Sheet("Base Supply", [
            ("REF ID", "Implant Type", "Item / Size", "Component", "Base Supply"),
            ("001.TEST", "Straumann BL NC", "4x10mm", "Body", balance)]),
        "Inventory Full Supply": Sheet("Inventory Full Supply", [
            ("REF ID", "Reorder Quantity"), ("001.TEST", 5)]),
    })
    log = Book({"Component Change Log": Sheet("Component Change Log", [
        ("Date", "REF ID", "Quantity", "Tooth #", "Placed / O Box", "Patient", "Periodontist", "Referring Dentist", "Assistant", "Notes"),
        (CUTOFF, ref, -5, "3", "placed", "Synthetic", "Test", "Test", "Staff", ""),
        (TODAY, ref, -2, "3", "placed", "Synthetic", "Test", "Test", "Staff", ""),
        (TODAY, ref, 1, "", "", "", "", "", "Staff", "Received"),
        *extra_rows])})
    return dashboard, log


class MigrationTests(unittest.TestCase):
    def prepare(self, books, profile=None):
        with patch("openpyxl.load_workbook", side_effect=books):
            return prepare("inventory.xlsm", "log.xlsm", CUTOFF, profile)

    def test_signed_log_cutoff_and_metadata(self):
        bundle, report = self.prepare(workbooks())
        self.assertTrue(report["ready"])
        self.assertEqual(report["projected_total"], 7)
        self.assertEqual(report["archived_entries"], 1)
        self.assertEqual(report["stock_changing_entries"], 2)
        self.assertEqual(bundle["components"][0]["manufacturer"], "Straumann")
        self.assertEqual(bundle["components"][0]["implant_type"], "BL NC")
        self.assertEqual(bundle["components"][0]["component_type"], "Implant Body")

    def test_negative_opening_blocks_import(self):
        _, report = self.prepare(workbooks(balance=-1))
        self.assertFalse(report["ready"])
        self.assertTrue(any("Balance row" in issue for issue in report["issues"]))

    def test_unknown_ref_requires_explicit_alias(self):
        _, report = self.prepare(workbooks(ref="1.TEST"))
        self.assertFalse(report["ready"])
        profile = deepcopy(DEFAULT_PROFILE)
        profile["ref_aliases"] = {"1.TEST": "001.TEST"}
        _, report = self.prepare(workbooks(ref="1.TEST"), profile)
        self.assertTrue(report["ready"])

    def test_ref_case_is_normalized(self):
        bundle, report = self.prepare(workbooks(ref="001.test"))
        self.assertTrue(report["ready"])
        self.assertEqual(bundle["events"][0]["ref_id"], "001.TEST")

    def test_duplicate_rows_preserved_with_stable_distinct_ids(self):
        duplicate = (TODAY, "001.TEST", -2, "3", "placed", "Synthetic", "Test", "Test", "Staff", "")
        bundle, report = self.prepare(workbooks(extra_rows=[duplicate]))
        self.assertEqual(len(bundle["events"]), 4)
        self.assertEqual(len({event["id"] for event in bundle["events"]}), 4)
        self.assertTrue(any("identical entry" in warning for warning in report["warnings"]))
        books = workbooks(extra_rows=[duplicate])
        sheet = books[1]["Component Change Log"]
        sheet.rows = sheet.rows[:1] + list(reversed(sheet.rows[1:]))
        reordered, _ = self.prepare(books)
        self.assertEqual({e["id"] for e in bundle["events"]}, {e["id"] for e in reordered["events"]})

    def test_zero_quantity_omitted_with_warning(self):
        zero = (TODAY, "001.TEST", 0, "", "", "", "", "", "", "")
        _, report = self.prepare(workbooks(extra_rows=[zero]))
        self.assertEqual(report["ignored_rows"], 1)
        self.assertTrue(report["ready"])


if __name__ == "__main__":
    unittest.main()
