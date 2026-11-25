"""
Включение/выключение системного прокси Windows (то же, что
Параметры -> Прокси-сервер и Свойства обозревателя -> Настройка сети).
Прежние настройки сохраняются в файл и возвращаются при выключении.
"""
from __future__ import annotations

import ctypes
import json
import logging
from pathlib import Path
from typing import Optional

try:
    import winreg
except ImportError:  # не Windows
    winreg = None  # type: ignore

log = logging.getLogger("tg-ws-tray")

_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
PROXY_OVERRIDE = "localhost;127.*;10.*;192.168.*;<local>"


def _notify_system() -> None:
    try:
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)  # SETTINGS_CHANGED
        wininet.InternetSetOptionW(0, 37, 0, 0)  # REFRESH
    except Exception as exc:
        log.debug("InternetSetOption failed: %r", exc)


def _read(name: str, default=None):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return default


def current_state() -> dict:
    return {
        "enable": int(_read("ProxyEnable", 0) or 0),
        "server": _read("ProxyServer", None),
        "override": _read("ProxyOverride", None),
    }


def is_enabled_for(server: str) -> bool:
    st = current_state()
    return bool(st["enable"]) and st["server"] == server


def enable(server: str, backup_file: Path) -> None:
    """Включить системный прокси server ('127.0.0.1:8080')."""
    if winreg is None:
        raise RuntimeError("Доступно только в Windows")
    st = current_state()
    # не затираем резервную копию, если наш прокси уже включён
    if not (st["enable"] and st["server"] == server) and not backup_file.exists():
        backup_file.write_text(json.dumps(st), encoding="utf-8")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _KEY) as k:
        winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
        winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, PROXY_OVERRIDE)
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
    _notify_system()
    log.info("System proxy enabled: %s", server)


def disable(backup_file: Path, only_if_server: Optional[str] = None) -> None:
    """Вернуть прежние настройки (или просто выключить прокси)."""
    if winreg is None:
        return
    st = current_state()
    if only_if_server is not None and st["server"] != only_if_server:
        # системный прокси сменил кто-то другой — не трогаем
        if backup_file.exists():
            backup_file.unlink()
        return
    prev = {"enable": 0, "server": None, "override": None}
    if backup_file.exists():
        try:
            prev.update(json.loads(backup_file.read_text(encoding="utf-8")))
        except Exception:
            pass
        try:
            backup_file.unlink()
        except OSError:
            pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _KEY) as k:
        if prev.get("server"):
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, prev["server"])
        else:
            try:
                winreg.DeleteValue(k, "ProxyServer")
            except OSError:
                pass
        if prev.get("override"):
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, prev["override"])
        else:
            try:
                winreg.DeleteValue(k, "ProxyOverride")
            except OSError:
                pass
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD,
                          1 if prev.get("enable") else 0)
    _notify_system()
    log.info("System proxy restored")


# ---------------------------------------------------------------------------
# Автонастройка (PAC): «Использовать сценарий настройки» в параметрах прокси.
# Если приложение не запущено, браузер сразу идёт напрямую.
# ---------------------------------------------------------------------------

def pac_url(host: str, port: int) -> str:
    host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    return f"http://{host}:{port}/proxy.pac"


def is_pac_enabled_for(url: str) -> bool:
    return _read("AutoConfigURL", None) == url


def enable_pac(url: str, backup_file: Path) -> None:
    """Прописать PAC-адрес, прежний сценарий запомнить."""
    if winreg is None:
        raise RuntimeError("Доступно только в Windows")
    prev = _read("AutoConfigURL", None)
    if prev != url and not backup_file.exists():
        backup_file.write_text(json.dumps({"autoconfig": prev}), encoding="utf-8")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _KEY) as k:
        winreg.SetValueEx(k, "AutoConfigURL", 0, winreg.REG_SZ, url)
    _notify_system()
    log.info("PAC enabled: %s", url)


def disable_pac(backup_file: Path, only_if_url: Optional[str] = None) -> None:
    """Вернуть прежний сценарий настройки (или убрать наш)."""
    if winreg is None:
        return
    if only_if_url is not None and _read("AutoConfigURL", None) != only_if_url:
        if backup_file.exists():
            backup_file.unlink()
        return
    prev = None
    if backup_file.exists():
        try:
            prev = json.loads(backup_file.read_text(encoding="utf-8")).get("autoconfig")
        except Exception:
            pass
        try:
            backup_file.unlink()
        except OSError:
            pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _KEY) as k:
        if prev:
            winreg.SetValueEx(k, "AutoConfigURL", 0, winreg.REG_SZ, prev)
        else:
            try:
                winreg.DeleteValue(k, "AutoConfigURL")
            except OSError:
                pass
    _notify_system()
    log.info("PAC restored")
