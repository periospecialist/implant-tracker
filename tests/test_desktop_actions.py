"""Desktop action tests that do not require a graphical display."""
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from inventory.desktop import App


class DesktopActionTests(unittest.TestCase):
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
