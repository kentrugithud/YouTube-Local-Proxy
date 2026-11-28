"""
Значения по умолчанию для YT-Local-Proxy.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from typing import Any, Dict

from ui.i18n import detect_system_language

_DEFAULTS_COMMON: Dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8080,
    "verbose": False,
    "log_max_mb": 5,
    "connection_mode": "worker",
    "cfproxy_worker_enabled": False,
    "cfproxy_worker_domain": [],
    "no_secure": False,
    "web_proxy_enabled": False,  # включён ли системный прокси Windows
    "web_domains": [],           # пусто = домены YouTube по умолчанию
    "appearance": "auto",
    "check_updates": True,
    "last_notified_version": "",
}


def default_tray_config() -> Dict[str, Any]:
    cfg = deepcopy(_DEFAULTS_COMMON)
    cfg["language"] = detect_system_language().value
    if sys.platform == "win32":
        cfg["autostart"] = False
    return cfg
