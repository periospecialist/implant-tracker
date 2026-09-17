"""GUI smoke checks run on Windows or on Linux with a display server."""
from datetime import date, timedelta
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from inventory.core import Database
from inventory.desktop import App
from inventory.settings import default_config, save_config


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Desktop display required")
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        config = default_config()
        config["exchange_folder"] = str(self.path / "exchange")
        save_config(self.path / "client.json", config)
        self.sync_patch = patch.object(App, "start_sync", return_value=None)
        self.sync_patch.start()
        self.app = App(self.path / "client.json")
        self.app.withdraw()
        self.db = Database(self.path / "server.db")
        self.db.save_component({"ref_id": "001.TEST", "opening_quantity": 20,
            "opening_date": (date.today() - timedelta(days=2)).isoformat(), "reorder_level": 5})
        self.app.store.install_snapshot(self.db.snapshot())
        self.app.refresh()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.close()
        self.sync_patch.stop()
        self.temp.cleanup()

    def test_form_save_and_refresh(self):
        form = self.app.forms["usage"]
        for key, value in {"ref_id": "001.TEST", "quantity": "2", "patient": "Synthetic",
                           "periodontist": "Test", "assistant": "Staff", "placement": "Placed"}.items():
            form.variables[key].set(value)
        form.save()
        self.assertEqual(len(self.app.store.entries()), 1)
        self.assertEqual(self.app.inventory.rows[0]["estimated_quantity"], 18)
        self.assertEqual(form.variables["patient"].get(), "")

    def test_numeric_sort_and_combined_filters(self):
        table = self.app.inventory
        table.set_rows([{"ref_id": "A", "estimated_quantity": 10, "manufacturer": "Test", "reorder": "No"},
                        {"ref_id": "B", "estimated_quantity": 2, "manufacturer": "Test", "reorder": "Yes"},
                        {"ref_id": "C", "estimated_quantity": 1, "manufacturer": "Other", "reorder": "Yes"}])
        table.sort("estimated_quantity")
        self.assertEqual([row["ref_id"] for row in table.visible], ["C", "B", "A"])
        table.filters.update(manufacturer="Test", reorder="Yes")
        table.render()
        self.assertEqual([row["ref_id"] for row in table.visible], ["B"])

    def test_seven_tabs_present(self):
        labels = [self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()]
        self.assertEqual(labels, ["Usage", "Stocking", "Inventory", "Usage history", "Stock history", "Sync queue", "Setup"])
        self.assertEqual(list(self.app.connection_vars), ["exchange_folder"])

    def test_placement_is_exactly_two_read_only_choices(self):
        placement = self.app.forms["usage"].widgets["placement"]
        self.assertEqual(str(placement.cget("state")), "readonly")
        self.assertEqual(tuple(placement.cget("values")), ("Placed", "O Box"))

    def test_inventory_reorder_alignment_after_scroll_sort_and_filter(self):
        table = self.app.inventory
        rows = [{"ref_id": f"R{i:03}", "estimated_quantity": i,
                 "manufacturer": "Straumann" if i % 2 else "Nobel",
                 "component_type": "Implant Body", "implant_type": "BLT",
                 "reorder": "Yes" if i < 30 else "No"} for i in range(100)]
        self.app.inventory_filters.update_rows(rows)
        self.app.deiconify()
        self.app.notebook.select(table.master)
        self.app.update()
        table._yview("moveto", 0.5)
        self.app.update()
        self.assertEqual(table.tree.yview(), table.reorder_tree.yview())
        for item in table.tree.get_children():
            bbox = table.tree.bbox(item)
            if bbox:
                self.assertEqual(bbox[1:2], table.reorder_tree.bbox(item)[1:2])
        table.reorder_tree.selection_set("55")
        self.app.update()
        self.assertEqual(table.selected()["ref_id"], "R055")
        table.sort("estimated_quantity")
        table.sort("estimated_quantity")
        table.filters["reorder"] = "Yes"
        table.render()
        self.app.update()
        self.assertEqual(len(table.visible), 30)
        self.assertEqual(len(table.reorder_tree.get_children()), 30)
        self.assertTrue(all(table.reorder_tree.item(i, "values") == ("YES",)
                            for i in table.reorder_tree.get_children()))

    def test_manufacturer_and_component_use_toggle_buttons(self):
        from tkinter import ttk
        filters = self.app.inventory_filters
        rows = [{"manufacturer": m, "component_type": c, "implant_type": "BLT"}
                for m in ("Nobel", "Straumann")
                for c in ("Implant Body", "Cover Screw", "Healing Abutment", "Abutment", "Other")]
        filters.update_rows(rows)
        for column, count in (("manufacturer", 2), ("component_type", 5)):
            buttons = [w for w in filters.groups[column].winfo_children() if isinstance(w, ttk.Checkbutton)]
            self.assertEqual(len(buttons), count)
            buttons[0].invoke()
            self.assertEqual(len(filters._selected(column)), 1)
        self.assertEqual(filters.labels["manufacturer"].grid_info()["row"], 0)
        self.assertEqual(filters.labels["component_type"].grid_info()["row"], 0)
        self.assertEqual(filters.labels["implant_type"].grid_info()["row"], 1)
        self.assertEqual(filters.export_button.grid_info()["row"], 1)
        dropdown = filters.buttons["implant_type"]
        # A transient Toplevel cannot become visible on Windows while its parent
        # is withdrawn. Show the test root before asserting native popup state.
        self.app.deiconify()
        self.app.notebook.select(filters.master)
        self.app.update()
        dropdown.show()
        self.app.update()
        implant_choice = next(widget for widget in dropdown.panel.winfo_children()
                              if isinstance(widget, ttk.Checkbutton))
        implant_choice.invoke()
        self.app.update()
        self.assertTrue(dropdown.visible)
        self.assertEqual(str(dropdown.popup.state()), "normal")
        self.assertEqual(len(self.app.inventory.visible), 1)

    def test_theme_switch_preserves_entries_and_inventory(self):
        form = self.app.forms["usage"]
        form.variables["patient"].set("Unsaved patient")
        expected = list(self.app.inventory.rows)
        for mode in ("Dark", "Light"):
            self.app.appearance_choice.set(mode)
            self.app.change_appearance()
            self.app.update_idletasks()
            self.assertEqual(self.app.appearance.mode, mode)
            self.assertEqual(form.variables["patient"].get(), "Unsaved patient")
            self.assertEqual(self.app.inventory.rows, expected)
            self.assertEqual(self.app.cget("background"), self.app.appearance.colors["background"])

    def test_inventory_rows_survive_filter_choice(self):
        filters = self.app.inventory_filters
        values = filters.variables["manufacturer"]
        next(iter(values.values())).set(True)
        filters._parents_changed()
        self.assertEqual(len(self.app.inventory.visible), 1)


if __name__ == "__main__":
    unittest.main()
