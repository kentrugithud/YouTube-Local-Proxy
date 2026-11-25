from __future__ import annotations

import sys
import tkinter
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

_tk_variable_del_guard_installed = False


def install_tkinter_variable_del_guard() -> None:
    global _tk_variable_del_guard_installed
    if _tk_variable_del_guard_installed:
        return
    _orig = tkinter.Variable.__del__

    def _safe_variable_del(self: Any, _orig: Any = _orig) -> None:
        try:
            _orig(self)
        except (RuntimeError, tkinter.TclError):
            pass

    tkinter.Variable.__del__ = _safe_variable_del  # type: ignore[assignment]
    _tk_variable_del_guard_installed = True

CONFIG_DIALOG_SIZE: Tuple[int, int] = (460, 560)
CONFIG_DIALOG_FRAME_PAD: Tuple[int, int] = (20, 14)
FIRST_RUN_SIZE: Tuple[int, int] = (520, 400)
FIRST_RUN_FRAME_PAD: Tuple[int, int] = (28, 24)


@dataclass(frozen=True)
class CtkTheme:
    accent: tuple = ("#dc2626", "#dc2626")
    accent_hover: tuple = ("#b91c1c", "#b91c1c")

    bg: tuple = ("#ffffff", "#1e1e1e")
    field_bg: tuple = ("#f0f2f5", "#2b2b2b")
    field_border: tuple = ("#d6d9dc", "#3a3a3a")

    text_primary: tuple = ("#000000", "#ffffff")
    text_secondary: tuple = ("#707579", "#aaaaaa")

    ui_font_family: str = "Sans"
    mono_font_family: str = "Monospace"


def ctk_theme_for_platform() -> CtkTheme:
    if sys.platform == "win32":
        return CtkTheme(ui_font_family="Segoe UI", mono_font_family="Consolas")
    return CtkTheme()


_APPEARANCE_MODE_MAP = {"auto": "system", "light": "Light", "dark": "Dark"}


_BLUE_TO_RED = {
    "#3B8ED0": "#dc2626", "#1F6AA5": "#dc2626",
    "#36719F": "#b91c1c", "#144870": "#b91c1c",
    "#325882": "#b91c1c", "#14375E": "#991b1b",
    "#203A4F": "#7f1d1d", "#27577D": "#991b1b",
}


def _red_theme_path(ctk: Any) -> str:
    """Встроенная синяя тема customtkinter, перекрашенная в красный."""
    import os
    import tempfile

    src = os.path.join(os.path.dirname(ctk.__file__), "assets", "themes", "blue.json")
    with open(src, encoding="utf-8") as f:
        text = f.read()
    for blue, red in _BLUE_TO_RED.items():
        text = text.replace(blue, red).replace(blue.lower(), red)
    path = os.path.join(tempfile.gettempdir(), "yt_local_proxy_red_theme.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def apply_ctk_appearance(ctk: Any, mode: str = "auto") -> None:
    ctk.set_appearance_mode(_APPEARANCE_MODE_MAP.get(mode, "system"))
    try:
        ctk.set_default_color_theme(_red_theme_path(ctk))
    except Exception:
        ctk.set_default_color_theme("blue")

def center_ctk_geometry(root: Any, width: int, height: int) -> None:
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 2}")


def create_ctk_toplevel(
    ctk: Any,
    *,
    title: str,
    width: int,
    height: int,
    theme: CtkTheme,
    topmost: bool = True,
    after_create: Optional[Callable[[Any], None]] = None,
) -> Any:
    root = ctk.CTkToplevel()

    if sys.platform == "win32":
        def handle_ctrl_shortcut(event: Any) -> Optional[str]:
            shortcuts = {65: ("a", "<<SelectAll>>"), 67: ("c", "<<Copy>>"), 86: ("v", "<<Paste>>")}
            shortcut = shortcuts.get(event.keycode)
            if shortcut and event.keysym.lower() != shortcut[0] and not event.state & 0x20000:
                event.widget.event_generate(shortcut[1])
                return "break"
            return None

        root.bind("<Control-KeyPress>", handle_ctrl_shortcut, add="+")
        
    root.title(title)
    root.resizable(False, False)
    center_ctk_geometry(root, width, height)
    root.configure(fg_color=theme.bg)
    if topmost:
        root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    if after_create:
        _after_id = root.after(300, lambda: after_create(root))
        _orig_destroy = root.destroy

        def _safe_destroy():
            try:
                root.after_cancel(_after_id)
            except Exception:
                pass
            _orig_destroy()

        root.destroy = _safe_destroy
    return root


def main_content_frame(
    ctk: Any,
    root: Any,
    theme: CtkTheme,
    *,
    padx: int,
    pady: int,
) -> Any:
    frame = ctk.CTkFrame(root, fg_color=theme.bg, corner_radius=0)
    frame.pack(fill="both", expand=True, padx=padx, pady=pady)
    return frame