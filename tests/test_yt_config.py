import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from proxy import proxy_config
from ui.settings import validate_settings
from utils import tray_common


class LoadConfigTests(unittest.TestCase):
    def _load(self, data):
        d = Path(tempfile.mkdtemp())
        f = d / "config.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        with mock.patch.object(tray_common, "CONFIG_FILE", f), \
                mock.patch.object(tray_common, "ensure_dirs"):
            return tray_common.load_config()

    def test_old_telegram_config_is_migrated(self):
        cfg = self._load({
            "port": 1443, "secret": "x" * 32, "dc_ip": ["2:1.1.1.1"],
            "cfproxy_worker_enabled": True,
            "cfproxy_worker_domain": ["w.example.workers.dev"],
        })
        self.assertEqual(cfg["port"], 8080)
        for gone in ("secret", "dc_ip", "cfproxy", "h2"):
            self.assertNotIn(gone, cfg)
        self.assertEqual(cfg["cfproxy_worker_domain"], ["w.example.workers.dev"])

    def test_custom_port_is_kept(self):
        self.assertEqual(self._load({"port": 9999})["port"], 9999)

    def test_apply_proxy_config(self):
        tray_common.apply_proxy_config({
            "host": "127.0.0.1", "port": 8123,
            "cfproxy_worker_enabled": True,
            "cfproxy_worker_domain": ["a.workers.dev"],
        })
        self.assertEqual(proxy_config.port, 8123)
        self.assertEqual(proxy_config.cfproxy_worker_domains, ["a.workers.dev"])
        tray_common.apply_proxy_config({
            "cfproxy_worker_enabled": False,
            "cfproxy_worker_domain": ["a.workers.dev"],
        })
        self.assertEqual(proxy_config.cfproxy_worker_domains, [])


class ValidateTests(unittest.TestCase):
    base = {"host": "127.0.0.1", "port": "8080"}

    def test_ok(self):
        r = validate_settings({**self.base, "cfproxy_worker_enabled": True,
                               "cfproxy_worker_domain": "a.workers.dev, b.workers.dev"}, {})
        self.assertEqual(r["port"], 8080)
        self.assertEqual(r["cfproxy_worker_domain"], ["a.workers.dev", "b.workers.dev"])

    def test_enabled_worker_needs_domain(self):
        r = validate_settings({**self.base, "cfproxy_worker_enabled": True,
                               "cfproxy_worker_domain": ""}, {})
        self.assertIsInstance(r, str)

    def test_bad_port(self):
        self.assertIsInstance(validate_settings({**self.base, "port": "0"}, {}), str)


if __name__ == "__main__":
    unittest.main()


class DomainLinesTests(unittest.TestCase):
    D = ["youtube.com", "googlevideo.com"]

    def parse(self, text):
        from ui.settings import parse_domain_lines
        return parse_domain_lines(text, self.D)

    def test_lines_and_cleanup(self):
        self.assertEqual(
            self.parse("YouTube.com\nhttps://www.example.org/path\n*.cdn.test.io:443\n"),
            ["youtube.com", "www.example.org", "cdn.test.io"])

    def test_duplicates_removed(self):
        self.assertEqual(self.parse("a.com\nA.com\n, a.com"), ["a.com"])

    def test_empty_or_default_means_defaults(self):
        self.assertEqual(self.parse(""), [])
        self.assertEqual(self.parse("   \n"), [])
        self.assertEqual(self.parse("youtube.com\ngooglevideo.com"), [])

    def test_bad_domain_gives_message(self):
        for bad in ("nodot", "bad domain!.com", "-a.com", "a..com"):
            with self.subTest(bad=bad):
                self.assertIsInstance(self.parse(bad), str)
