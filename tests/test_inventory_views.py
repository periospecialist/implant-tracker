import unittest

from inventory.desktop import (available_implant_types, inventory_cell_display,
                               inventory_filter_matches, natural_label_key)


ROWS = [
    {"ref_id": "S-BLT-NC", "manufacturer": "Straumann", "implant_type": "BLT NC", "component_type": "Implant Body"},
    {"ref_id": "S-BLT-SC", "manufacturer": "Straumann", "implant_type": "BLT SC", "component_type": "Implant Body"},
    {"ref_id": "S-BL-NC", "manufacturer": "Straumann", "implant_type": "BL NC", "component_type": "Cover Screw"},
    {"ref_id": "N-ACT-NP", "manufacturer": "Nobel", "implant_type": "Active NP", "component_type": "Implant Body"},
    {"ref_id": "N-PAR-RP", "manufacturer": "Nobel", "implant_type": "Parallel RP", "component_type": "Healing Abutment"},
]


class InventoryViewTests(unittest.TestCase):
    def test_implant_types_follow_current_manufacturer_and_component_types(self):
        self.assertEqual(available_implant_types(ROWS, {"Straumann"}, {"Implant Body"}), ["BLT NC", "BLT SC"])
        self.assertEqual(available_implant_types(ROWS, {"Nobel"}, {"Healing Abutment"}), ["Parallel RP"])
        self.assertEqual(len(available_implant_types(ROWS, set(), set())), 5)

    def test_groups_are_inclusive_within_and_across_selections(self):
        visible = {row["ref_id"] for row in ROWS if inventory_filter_matches(
            row, {"Straumann", "Nobel"}, {"Implant Body"}, {"BLT NC", "BLT SC", "Active NP"})}
        self.assertEqual(visible, {"S-BLT-NC", "S-BLT-SC", "N-ACT-NP"})
        self.assertTrue(inventory_filter_matches(ROWS[0], set(), {"Implant Body"}, {"BLT NC"}))
        self.assertTrue(all(inventory_filter_matches(row, set(), set(), set()) for row in ROWS))

    def test_implant_labels_sort_numbers_naturally(self):
        self.assertEqual(sorted(["BLT 12", "BLT 2", "BLT 1"], key=natural_label_key),
                         ["BLT 1", "BLT 2", "BLT 12"])

    def test_only_target_cells_receive_color_indicators(self):
        row = {"estimated_quantity": -2, "reorder": "Yes"}
        self.assertEqual(inventory_cell_display("estimated_quantity", -2, row), "🟨 ERROR: -2")
        self.assertEqual(inventory_cell_display("reorder", "Yes", row), "🟥 YES")
        self.assertEqual(inventory_cell_display("manufacturer", "Straumann", row), "Straumann")
        self.assertEqual(inventory_cell_display("reorder", "No", row), "🟩 No")


if __name__ == "__main__":
    unittest.main()
