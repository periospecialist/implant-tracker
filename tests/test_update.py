import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from inventory import update


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class UpdateTests(unittest.TestCase):
    def test_latest_release_selects_named_desktop_asset(self):
        payload = json.dumps({
            "tag_name": "build-10-abcdef0",
            "name": "Automatic build",
            "html_url": "https://example.test/release",
            "assets": [
                {"name": "InventoryServer-Windows.zip", "browser_download_url": "https://example.test/server"},
                {"name": update.ASSET_NAME, "browser_download_url": "https://example.test/client"},
            ],
        }).encode()
        with patch("inventory.update.urlopen", return_value=Response(payload)):
            release = update.latest_release()
        self.assertEqual(release["url"], "https://example.test/client")
        self.assertEqual(release["tag"], "build-10-abcdef0")
        self.assertTrue(release["available"])

    def test_safe_extract_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../outside.txt", "unsafe")
            with self.assertRaisesRegex(RuntimeError, "unsafe path"):
                update._safe_extract(archive, Path(folder) / "output")
            self.assertFalse((Path(folder).parent / "outside.txt").exists())

    def test_safe_extract_accepts_expected_package(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "good.zip"
            output = Path(folder) / "output"
            output.mkdir()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("ImplantInventory.exe", "exe")
                bundle.writestr("Update.ps1", "script")
                bundle.writestr("_internal/library.zip", "data")
            update._safe_extract(archive, output)
            self.assertTrue((output / "ImplantInventory.exe").is_file())
            self.assertTrue((output / "_internal" / "library.zip").is_file())


if __name__ == "__main__":
    unittest.main()
