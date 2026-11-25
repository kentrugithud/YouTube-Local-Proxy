import hashlib
import unittest
from unittest import mock

from utils import update_check
from utils.windows_update import ASSET_NAME


def _release(tag, digest=True):
    return {
        "tag_name": tag, "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": [{"name": ASSET_NAME,
                    "browser_download_url": "https://example.invalid/YT-Local-Proxy.exe",
                    **({"digest": "sha256:" + hashlib.sha256(b"x").hexdigest()} if digest else {})}],
    }


class NewerVersionTests(unittest.TestCase):
    def _has_update(self, current, tag):
        with mock.patch.object(update_check, "fetch_latest_release", return_value=_release(tag)):
            update_check.run_check(current)
        return update_check.get_status()["has_update"]

    def test_any_numerically_newer_tag_notifies(self):
        for tag in ("v1.4", "v1.3.1", "v2.0", "v1.10", "v1.35", "1.4"):
            with self.subTest(tag=tag):
                self.assertTrue(self._has_update("1.3", tag))

    def test_same_or_older_does_not_notify(self):
        for tag in ("v1.3", "v1.3.0", "v1.2", "v1.0"):
            with self.subTest(tag=tag):
                self.assertFalse(self._has_update("1.3", tag))

    def test_asset_for_auto_install_found_with_digest(self):
        with mock.patch.object(update_check, "fetch_latest_release", return_value=_release("v1.4")):
            update_check.run_check("1.3")
        self.assertIsNotNone(update_check.find_asset(ASSET_NAME))

    def test_without_digest_auto_install_is_not_offered(self):
        with mock.patch.object(update_check, "fetch_latest_release",
                               return_value=_release("v1.4", digest=False)):
            update_check.run_check("1.3")
        self.assertTrue(update_check.get_status()["has_update"])
        self.assertIsNone(update_check.find_asset(ASSET_NAME))


if __name__ == "__main__":
    unittest.main()
