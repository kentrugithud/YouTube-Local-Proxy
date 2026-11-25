from __future__ import annotations

import ctypes
import os
import sys
import threading
import webbrowser
import time
import winreg

from pathlib import Path
from typing import Optional

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    import pystray
except ImportError:
    pystray = None

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

try:
    from PIL import Image
except ImportError:
    Image = None

from proxy import __version__, coerce_domain_list

from utils.win32_theme import (
    is_windows_dark_theme, 
    apply_windows_dark_theme,
)
from utils.tray_common import (
    APP_NAME, DEFAULT_CONFIG, FIRST_RUN_MARKER, IS_FROZEN, LOG_FILE,
    acquire_lock, bootstrap, ctk_run_dialog,
    ensure_ctk_thread, ensure_dirs, load_config, load_icon, log,
    quit_ctk, release_lock, restart_proxy,
    save_config, start_proxy, stop_proxy,
)
from utils import update_check, win_system_proxy
from utils.windows_update import apply_update, asset_name, can_self_update
from ui.settings_form import Updater
from utils.tray_common import APP_DIR
from ui.ctk_tray_ui import (
    install_tray_config_buttons, install_tray_config_form,
    populate_first_run_window, tray_settings_scroll_and_footer,
)
from ui.ctk_theme import (
    CONFIG_DIALOG_FRAME_PAD, CONFIG_DIALOG_SIZE, FIRST_RUN_SIZE,
    create_ctk_toplevel, ctk_theme_for_platform, main_content_frame,
)
from ui.i18n import t
from ui.settings_dialog import SettingsDialog

_tray_icon: Optional[object] = None
_config: dict = {}
_exiting = False
_win_mutex_handle = None

_ERROR_ALREADY_EXISTS = 183


def _acquire_win_mutex() -> bool | None:
    global _win_mutex_handle
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, True, "Local\\YT-Local-Proxy_SingleInstance")
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        if not handle:
            return None
        _win_mutex_handle = handle
        return True
    except Exception:
        return None


def _release_win_mutex() -> None:
    global _win_mutex_handle
    if _win_mutex_handle:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.ReleaseMutex(ctypes.c_void_p(_win_mutex_handle))
            kernel32.CloseHandle(ctypes.c_void_p(_win_mutex_handle))
        except Exception:
            pass
        _win_mutex_handle = None

ICON_PATH = str(Path(__file__).parent / "icon.ico")

# win32 dialogs

_u32 = ctypes.windll.user32
_u32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
_u32.MessageBoxW.restype = ctypes.c_int

_MB_OK_ERR = 0x10
_MB_OK_INFO = 0x40
_MB_YESNO_Q = 0x24
_MB_YESNOCANCEL_Q = 0x23
_IDYES = 6
_IDNO = 7


def _show_error(text: str, title: Optional[str] = None) -> None:
    _u32.MessageBoxW(None, text, title or t("app.error_title"), _MB_OK_ERR)


def _show_info(text: str, title: Optional[str] = None) -> None:
    _u32.MessageBoxW(None, text, title or t("app.name"), _MB_OK_INFO)


def _ask_yes_no(text: str, title: Optional[str] = None) -> bool:
    return _u32.MessageBoxW(None, text, title or t("app.name"), _MB_YESNO_Q) == _IDYES


# autostart (registry)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _supports_autostart() -> bool:
    return IS_FROZEN


def _autostart_command() -> str:
    return f'"{sys.executable}"'


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
        return str(val).strip() == _autostart_command().strip()
    except (FileNotFoundError, OSError):
        return False


def set_autostart_enabled(enabled: bool) -> None:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        log.error("Failed to update autostart: %s", exc)
        _show_error(
            t("dialog.autostart_fail", error=exc)
        )


# tray callbacks

def _on_restart(icon=None, item=None) -> None:
    threading.Thread(
        target=lambda: restart_proxy(_config, _show_error), daemon=True
    ).start()


def _on_edit_config(icon=None, item=None) -> None:
    threading.Thread(target=_edit_config_dialog, daemon=True).start()


# web proxy (system proxy toggle)

_PROXY_BACKUP = APP_DIR / "system_proxy_backup.json"      # старый ручной прокси
_PAC_BACKUP = APP_DIR / "system_pac_backup.json"


def _web_server_addr() -> str:
    return f"{_config.get('host', '127.0.0.1')}:{int(_config.get('port', 8080))}"


def _pac_address() -> str:
    return win_system_proxy.pac_url(
        _config.get("host", "127.0.0.1"), int(_config.get("port", 8080)))


def _web_proxy_active() -> bool:
    return bool(_config.get("web_proxy_enabled")) and \
        win_system_proxy.is_pac_enabled_for(_pac_address())


def _worker_configured() -> bool:
    return bool(
        _config.get("cfproxy_worker_enabled")
        and coerce_domain_list(_config.get("cfproxy_worker_domain"))
    )


def _toggle_web_proxy_worker() -> None:
    try:
        if _web_proxy_active():
            win_system_proxy.disable_pac(_PAC_BACKUP, only_if_url=_pac_address())
            _config["web_proxy_enabled"] = False
            save_config(_config)
        else:
            if not _worker_configured():
                _show_info(t("dialog.web_need_worker"))
                return
            win_system_proxy.enable_pac(_pac_address(), _PAC_BACKUP)
            _config["web_proxy_enabled"] = True
            save_config(_config)
    except Exception as exc:
        log.error("Web proxy toggle failed: %r", exc)
        _show_error(t("dialog.web_proxy_fail", error=exc))
    finally:
        if _tray_icon is not None:
            try:
                _tray_icon.update_menu()
            except Exception:
                pass


def _on_toggle_web_proxy(icon=None, item=None) -> None:
    threading.Thread(target=_toggle_web_proxy_worker, daemon=True,
                     name="web-proxy-toggle").start()


def _sync_system_proxy_on_start() -> None:
    """При запуске: применить сохранённое состояние, не оставлять 'висящих' настроек."""
    try:
        # остатки старого режима (фиксированный прокси 127.0.0.1:порт)
        win_system_proxy.disable(_PROXY_BACKUP, only_if_server=_web_server_addr())
        if _config.get("web_proxy_enabled") and _worker_configured():
            win_system_proxy.enable_pac(_pac_address(), _PAC_BACKUP)
        else:
            win_system_proxy.disable_pac(_PAC_BACKUP, only_if_url=_pac_address())
            _config["web_proxy_enabled"] = False
    except Exception as exc:
        log.warning("System proxy sync failed: %r", exc)


def _release_system_proxy_on_exit() -> None:
    # пока приложение закрыто, наших настроек в Windows быть не должно
    try:
        win_system_proxy.disable_pac(_PAC_BACKUP, only_if_url=_pac_address())
        win_system_proxy.disable(_PROXY_BACKUP, only_if_server=_web_server_addr())
    except Exception as exc:
        log.warning("System proxy release failed: %r", exc)


def _update_asset() -> Optional[dict]:
    """Файл релиза для автоустановки (только у собранного exe с правом записи)."""
    if not IS_FROZEN or not can_self_update():
        return None
    name = asset_name()
    return update_check.find_asset(name) if name else None


def _perform_update(asset: dict) -> Optional[str]:
    """Скачать (с проверкой SHA-256), заменить exe и перезапуститься.
    Возвращает текст ошибки; при успехе процесс завершается."""
    restarting = False

    def _before_restart() -> None:
        nonlocal restarting
        restarting = True
        log.info("Update: restarting")
        _release_system_proxy_on_exit()
        stop_proxy()
        release_lock()
        _release_win_mutex()

    log.info("Update: downloading")
    try:
        apply_update(asset, _before_restart)
    except Exception as exc:
        log.exception("Update failed")
        if restarting:  # откат: старый exe возвращён, поднимаем его заново
            mutex = _acquire_win_mutex()
            if mutex is True or mutex is None and acquire_lock():
                start_proxy(_config, _show_error)
                _sync_system_proxy_on_start()
        return str(exc) or type(exc).__name__
    if _tray_icon is not None:
        try:
            _tray_icon.stop()
        except Exception:
            pass
    os._exit(0)


def _make_updater() -> Updater:
    def _check() -> dict:
        update_check.run_check(__version__)
        return update_check.get_status()

    return Updater(check=_check, asset=_update_asset, install=_perform_update)


def _prompt_update(st: dict) -> None:
    ver = st.get("latest") or "?"
    asset = _update_asset()
    if asset is not None:
        if _ask_yes_no(t("update.available_install", version=ver, current=__version__),
                       t("update.title")):
            err = _perform_update(asset)
            if err:
                _show_error(t("update.error", msg=err))
    elif _ask_yes_no(t("update.manual", version=ver, current=__version__),
                     t("update.title")):
        webbrowser.open(st.get("html_url") or update_check.RELEASES_PAGE_URL)


def _check_updates_async() -> None:
    """Один запрос к GitHub при запуске; сообщение — один раз на каждую новую версию."""
    if not _config.get("check_updates", True):
        return

    def worker() -> None:
        try:
            update_check.run_check(__version__)
            st = update_check.get_status()
            latest = st.get("latest")
            if (not st.get("has_update") or _exiting
                    or latest == _config.get("last_notified_version")):
                return
            _config["last_notified_version"] = latest
            save_config(_config)
            _prompt_update(st)
        except Exception as exc:
            log.warning("Update check failed: %r", exc)

    threading.Thread(target=worker, daemon=True, name="update-check").start()


def _on_open_logs(icon=None, item=None) -> None:
    log.info("Opening log file: %s", LOG_FILE)
    if LOG_FILE.exists():
        try:
            os.startfile(str(LOG_FILE))
        except Exception as exc:
            log.error("Failed to open log file: %s", exc)
            _show_error(t("dialog.log_open_fail", error=exc))
    else:
        _show_info(t("dialog.log_not_found"))


def _on_exit(icon=None, item=None) -> None:
    global _exiting
    if _exiting:
        os._exit(0)
        return
    _exiting = True
    log.info("User requested exit")
    _release_system_proxy_on_exit()
    quit_ctk()
    threading.Thread(target=lambda: (time.sleep(3), os._exit(0)), daemon=True, name="force-exit").start()
    if icon:
        icon.stop()


# settings dialog

def _edit_config_dialog() -> None:
    if not ensure_ctk_thread(ctk, _config.get("appearance", "auto")):
        _show_error(t("dialog.ctk_missing"))
        return

    cfg = dict(_config)
    cfg["autostart"] = is_autostart_enabled()

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        w, h = CONFIG_DIALOG_SIZE
        if _supports_autostart():
            h += 100

        root = create_ctk_toplevel(
            ctk, title=t("app.settings_title"), width=w, height=h, theme=theme,
            topmost=False, after_create=lambda r: r.iconbitmap(ICON_PATH),
        )
        fpx, fpy = CONFIG_DIALOG_FRAME_PAD
        frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)
        scroll, footer = tray_settings_scroll_and_footer(ctk, frame, theme)

        def _refresh_tray_menu() -> None:
            if _tray_icon is not None:
                _tray_icon.menu = _build_menu()

        widgets = install_tray_config_form(
            ctk, scroll, theme, cfg, DEFAULT_CONFIG,
            show_autostart=_supports_autostart(),
            autostart_value=cfg.get("autostart", False),
            updater=_make_updater(),
        )

        def _finish() -> None:
            root.destroy()
            done.set()

        dialog = SettingsDialog(
            ctk=ctk, root=root, widgets=widgets, config=_config,
            defaults=DEFAULT_CONFIG, persist=save_config,
            refresh_menu=_refresh_tray_menu, finish=_finish,
            restart=lambda config: restart_proxy(config, _show_error),
            include_autostart=_supports_autostart(),
            apply_autostart=set_autostart_enabled if _supports_autostart() else None,
            read_autostart=is_autostart_enabled if _supports_autostart() else None,
        )

        root.protocol("WM_DELETE_WINDOW", dialog.cancel)
        install_tray_config_buttons(ctk, footer, theme, on_save=dialog.save, on_cancel=dialog.cancel)

    ctk_run_dialog(_build)


# first run

def _show_first_run() -> None:
    ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        return
    if not ensure_ctk_thread(ctk, _config.get("appearance", "auto")):
        FIRST_RUN_MARKER.touch()
        return

    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        w, h = FIRST_RUN_SIZE
        root = create_ctk_toplevel(
            ctk, title=t("app.name"), width=w, height=h, theme=theme,
            topmost=False, after_create=lambda r: r.iconbitmap(ICON_PATH),
        )

        def on_done() -> None:
            FIRST_RUN_MARKER.touch()
            root.destroy()
            done.set()

        populate_first_run_window(ctk, root, theme, host=host, port=port, on_done=on_done)

    ctk_run_dialog(_build)


# tray menu

def _build_menu():
    if pystray is None:
        return None
    return pystray.Menu(
        pystray.MenuItem(t("tray.web_proxy"), _on_toggle_web_proxy,
                         checked=lambda item: _web_proxy_active(), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.restart"), _on_restart),
        pystray.MenuItem(t("tray.settings"), _on_edit_config),
        pystray.MenuItem(t("tray.logs"), _on_open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.exit"), _on_exit),
    )


# entry point

def run_tray() -> None:
    global _tray_icon, _config

    _config = load_config()

    if is_windows_dark_theme():
        apply_windows_dark_theme()

    bootstrap(_config)

    if pystray is None or Image is None or ctk is None:
        log.error("pystray, Pillow or customtkinter not installed; running in console mode")
        start_proxy(_config, _show_error)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_proxy()
        return

    start_proxy(_config, _show_error)
    _sync_system_proxy_on_start()
    _check_updates_async()
    _show_first_run()

    _tray_icon = pystray.Icon(APP_NAME, load_icon(), t("app.name"), menu=_build_menu())
    log.info("Tray icon running")
    _tray_icon.run()

    _release_system_proxy_on_exit()
    stop_proxy()
    log.info("Tray app exited")


def main() -> None:
    if (mutex_result := _acquire_win_mutex()) is False or mutex_result is None and not acquire_lock():
        _show_info(t("dialog.already_running"), os.path.basename(sys.argv[0]))
        return

    if IS_FROZEN:
        def _cleanup_old_exes():
            time.sleep(3)
            exe = Path(sys.executable)
            backup = exe.with_name(exe.stem + "_old.exe")
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass
        threading.Thread(target=_cleanup_old_exes, daemon=True, name="cleanup-old").start()

    try:
        run_tray()
    finally:
        release_lock()
        _release_win_mutex()


if __name__ == "__main__":
    main()
