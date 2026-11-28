"""Windows build selection and executable replacement with rollback."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import sysconfig
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Callable, Optional

from utils.update_check import download_asset, relaunch_env


ASSET_NAME = "YT-Local-Proxy.exe"


def asset_name() -> Optional[str]:
    """Имя файла в релизе GitHub, который скачивает самообновление."""
    if sysconfig.get_platform() in ("win-amd64", "win32", "win-arm64"):
        return ASSET_NAME
    return None


def can_self_update() -> bool:
    """Whether the executable's folder is writable.

    Neither os.access nor tempfile.mkstemp can be used here: on Windows the
    former ignores ACLs and the latter keeps retrying on PermissionError, so
    in a folder without write access it hangs instead of failing.
    """
    probe = Path(sys.executable).parent / f"YT-Local-Proxy_probe_{uuid.uuid4().hex}"
    try:
        os.close(os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        return False
    with suppress(OSError):
        probe.unlink()
    return True


def cleanup_old_files(exe: Path, attempts: int = 30, delay: float = 1.0) -> bool:
    """Удалить старую версию после обновления и недокачанные файлы.

    Старый exe может быть занят ещё несколько секунд после перезапуска,
    поэтому удаление повторяется. Возвращает True, если ничего не осталось."""
    import time

    old = exe.with_name(exe.stem + "_old.exe")
    for _ in range(max(1, attempts)):
        leftovers = [old, *exe.parent.glob(exe.stem + "_tmp_*")]
        remaining = []
        for path in leftovers:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                remaining.append(path)
        if not remaining:
            return True
        time.sleep(delay)
    return False


def apply_update(asset: dict, before_restart: Callable[[], None]) -> None:
    """Verify, replace and launch; keep the old executable if any step fails."""
    exe = Path(sys.executable)
    backup = exe.with_name(exe.stem + "_old.exe")
    if not can_self_update():
        raise PermissionError(f"No write access to {exe.parent}")
    downloaded = download_asset(asset["url"], exe.parent, digest=asset["digest"])
    try:
        backup.unlink(missing_ok=True)
        exe.rename(backup)
        try:
            downloaded.replace(exe)
            before_restart()
            bundle = getattr(sys, "_MEIPASS", None)
            if bundle:
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                subprocess.Popen(
                    [str(exe), *sys.argv[1:]], env=relaunch_env(),
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            finally:
                if bundle:
                    ctypes.windll.kernel32.SetDllDirectoryW(ctypes.c_wchar_p(bundle))
        except BaseException:
            backup.replace(exe)
            raise
    finally:
        with suppress(OSError):
            downloaded.unlink(missing_ok=True)
