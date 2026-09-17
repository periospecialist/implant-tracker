"""Desktop action tests that do not require a graphical display."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from inventory.desktop import App


class DesktopActionTests(unittest.TestCase):
    def test_reorder_level_edit_posts_revision_checked_component(self):
        row = {"ref_id": "REF-1", "manufacturer": "Test", "implant_type": "Type",
               "size": "4x10", "component_type": "Implant Body", "other_parts": "",
               "reorder_level": 2, "revision": 4}
        transport = MagicMock()
        fake = SimpleNamespace(status=MagicMock(), engine=SimpleNamespace(transport=transport),
                               start_sync=MagicMock())
        fake.background = lambda work, success, failure=None: success(work())

        App.update_reorder_level(fake, row, "7")

        transport.post.assert_called_once_with("/components", {
            "ref_id": "REF-1", "manufacturer": "Test", "implant_type": "Type",
            "size": "4x10", "component_type": "Implant Body", "other_parts": "",
            "reorder_level": 7, "revision": 4,
        })
        fake.start_sync.assert_called_once_with()

    def test_remove_usage_uses_engine_transport_and_installs_snapshot(self):
        row = {"id": "entry-1", "ref_id": "REF-1", "quantity": 2}
        snapshot = {"inventory": [], "logs": {"usage": [], "addition": []},
                    "receipts": [], "as_of": "now", "totals": {"usage": 0, "addition": 0}}
        transport = MagicMock()
        transport.post.return_value = {"deleted": True, "snapshot": snapshot}
        fake = SimpleNamespace(
            log_tables={"usage": SimpleNamespace(selected=lambda: row)},
            status=MagicMock(), engine=SimpleNamespace(transport=transport),
            store=MagicMock(), online=False, refresh=MagicMock())
        def background(work, success, failure=None):
            success(work())
        fake.background = background

        with patch("inventory.desktop.messagebox.askyesno", return_value=True):
            App.remove_usage_entry(fake)

        transport.post.assert_called_once_with("/delete_usage", {"id": "entry-1"})
        fake.store.install_snapshot.assert_called_once_with(snapshot)
        fake.refresh.assert_called_once_with()
        self.assertTrue(fake.online)


if __name__ == "__main__":
    unittest.main()
