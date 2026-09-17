import unittest

from inventory.desktop import component_name, matching_refs


COMPONENTS = {
    "021.2510": {"manufacturer": "Straumann", "implant_type": "BL NC", "size": "3.3x10mm"},
    "021.3510": {"manufacturer": "Straumann", "implant_type": "BLT NC", "size": "3.3x10mm"},
    "035.8010S": {"manufacturer": "Straumann", "implant_type": "BLC RB", "size": "3.3x10mm"},
}


class RefLookupTests(unittest.TestCase):
    def test_search_is_case_insensitive_and_matches_inside_ref(self):
        self.assertEqual(matching_refs(COMPONENTS, "s"), ["035.8010S"])
        self.assertEqual(matching_refs(COMPONENTS, "351"), ["021.3510"])

    def test_prefix_matches_come_before_contains_matches(self):
        refs = {"210.A": {}, "A.210": {}, "210.B": {}}
        self.assertEqual(matching_refs(refs, "210"), ["210.A", "210.B", "A.210"])

    def test_empty_search_is_sorted_and_bounded(self):
        refs = {f"R{i:03d}": {} for i in range(80)}
        self.assertEqual(len(matching_refs(refs, "")), 50)
        self.assertEqual(matching_refs(refs, "")[:2], ["R000", "R001"])

    def test_component_name_uses_requested_fields(self):
        self.assertEqual(component_name(COMPONENTS["021.3510"]), "Straumann BLT NC 3.3x10mm")
        self.assertEqual(component_name({"manufacturer": "Nobel", "implant_type": "Active RP"}), "Nobel Active RP")


if __name__ == "__main__":
    unittest.main()
