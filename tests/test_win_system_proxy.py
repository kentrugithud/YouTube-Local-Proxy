import sys
import tempfile
import types
import unittest
from pathlib import Path


class FakeKey:
    def __init__(self, store): self.s = store
    def __enter__(self): return self
    def __exit__(self, *a): return False


def make_fake_winreg(store):
    m = types.ModuleType("winreg")
    m.HKEY_CURRENT_USER = 1; m.REG_SZ = 1; m.REG_DWORD = 4
    m.OpenKey = lambda root, path: FakeKey(store)
    m.CreateKey = lambda root, path: FakeKey(store)
    def q(k, name):
        if name not in store: raise OSError
        return (store[name], 0)
    m.QueryValueEx = q
    m.SetValueEx = lambda k, n, r, t, v: store.__setitem__(n, v)
    def d(k, n):
        if n not in store: raise OSError
        del store[n]
    m.DeleteValue = d
    return m


class SystemProxyTests(unittest.TestCase):
    def setUp(self):
        from utils import win_system_proxy as w
        self.w = w
        self.store = {"ProxyEnable": 1, "ProxyServer": "corp:3128"}
        w.winreg = make_fake_winreg(self.store)
        w._notify_system = lambda: None
        self.bak = Path(tempfile.mkdtemp()) / "bak.json"

    def test_enable_and_restore(self):
        w = self.w
        w.enable("127.0.0.1:8080", self.bak)
        self.assertEqual(self.store["ProxyServer"], "127.0.0.1:8080")
        self.assertEqual(self.store["ProxyEnable"], 1)
        self.assertTrue(w.is_enabled_for("127.0.0.1:8080"))
        w.disable(self.bak, only_if_server="127.0.0.1:8080")
        self.assertEqual(self.store["ProxyServer"], "corp:3128")
        self.assertEqual(self.store["ProxyEnable"], 1)

    def test_restore_to_off(self):
        w = self.w
        self.store.update(ProxyEnable=0); self.store.pop("ProxyServer")
        w.enable("127.0.0.1:8080", self.bak)
        w.disable(self.bak, only_if_server="127.0.0.1:8080")
        self.assertEqual(self.store["ProxyEnable"], 0)
        self.assertNotIn("ProxyServer", self.store)

    def test_double_enable_keeps_original_backup(self):
        w = self.w
        w.enable("127.0.0.1:8080", self.bak)
        w.enable("127.0.0.1:8080", self.bak)
        w.disable(self.bak, only_if_server="127.0.0.1:8080")
        self.assertEqual(self.store["ProxyServer"], "corp:3128")

    def test_foreign_proxy_untouched(self):
        self.w.disable(self.bak, only_if_server="127.0.0.1:8080")
        self.assertEqual(self.store["ProxyServer"], "corp:3128")

    def test_pac_enable_and_restore(self):
        w = self.w
        url = "http://127.0.0.1:8080/proxy.pac"
        w.enable_pac(url, self.bak)
        self.assertEqual(self.store["AutoConfigURL"], url)
        self.assertTrue(w.is_pac_enabled_for(url))
        # ручной прокси не затронут
        self.assertEqual(self.store["ProxyServer"], "corp:3128")
        w.disable_pac(self.bak, only_if_url=url)
        self.assertNotIn("AutoConfigURL", self.store)

    def test_pac_restores_previous_script(self):
        w = self.w
        self.store["AutoConfigURL"] = "http://corp/wpad.dat"
        url = "http://127.0.0.1:8080/proxy.pac"
        w.enable_pac(url, self.bak)
        w.disable_pac(self.bak, only_if_url=url)
        self.assertEqual(self.store["AutoConfigURL"], "http://corp/wpad.dat")

    def test_pac_foreign_untouched(self):
        self.store["AutoConfigURL"] = "http://corp/wpad.dat"
        self.w.disable_pac(self.bak, only_if_url="http://127.0.0.1:8080/proxy.pac")
        self.assertEqual(self.store["AutoConfigURL"], "http://corp/wpad.dat")


if __name__ == "__main__":
    unittest.main()
