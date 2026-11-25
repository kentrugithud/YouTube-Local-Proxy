from __future__ import annotations

import math
import socket
from copy import deepcopy
from dataclasses import dataclass
from typing import Union

from proxy import coerce_domain_list
from ui.i18n import t

LIVE_SETTINGS_KEYS = frozenset({
    "appearance", "autostart", "check_updates", "language", "verbose"
})


@dataclass(frozen=True)
class SettingsChange:
    config: dict
    changed_keys: frozenset

    @property
    def requires_restart(self) -> bool:
        return bool(self.changed_keys - LIVE_SETTINGS_KEYS)


def prepare_settings(current: dict, values: dict, defaults: dict) -> SettingsChange:
    config = deepcopy(defaults)
    config.update(deepcopy(current))
    changed_keys = frozenset(
        key for key, value in values.items() if value != config.get(key)
    )
    config.update(deepcopy(values))
    return SettingsChange(config, changed_keys)


def validate_settings(values: dict, defaults: dict) -> Union[dict, str]:
    config = dict(values)

    host = values["host"].strip()
    try:
        socket.inet_aton(host)
    except OSError:
        return t("validation.bad_host")

    try:
        port = int(values["port"].strip())
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        return t("validation.bad_port")

    config.update(host=host, port=port)

    if "cfproxy_worker_domain" in config:
        config["cfproxy_worker_domain"] = coerce_domain_list(
            config["cfproxy_worker_domain"])
    if config.get("cfproxy_worker_enabled") and not config.get("cfproxy_worker_domain"):
        return t("validation.worker_empty")

    return config
