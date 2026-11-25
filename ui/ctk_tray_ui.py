from __future__ import annotations

from typing import Any, Callable, Tuple

from ui.ctk_controls import create_checkbox
from ui.ctk_theme import FIRST_RUN_FRAME_PAD, CtkTheme, main_content_frame
from ui.ctk_tooltip import attach_ctk_tooltip
from ui.i18n import t
from ui.settings_form import (
    TrayConfigFormWidgets as TrayConfigFormWidgets,
    install_tray_config_form as install_tray_config_form,
    validate_config_form as validate_config_form,
)


def tray_settings_scroll_and_footer(
    ctk: Any,
    content_parent: Any,
    theme: CtkTheme,
) -> Tuple[Any, Any]:
    footer = ctk.CTkFrame(content_parent, fg_color=theme.bg)
    footer.pack(side="bottom", fill="x")
    scroll = ctk.CTkScrollableFrame(
        content_parent,
        fg_color=theme.bg,
        corner_radius=0,
        scrollbar_button_color=theme.field_border,
        scrollbar_button_hover_color=theme.text_secondary,
    )
    scroll.pack(fill="both", expand=True)
    try:
        scroll._parent_canvas.configure(yscrollincrement=4)
    except Exception:
        pass
    return scroll, footer


def install_tray_config_buttons(
    ctk: Any,
    frame: Any,
    theme: CtkTheme,
    *,
    on_save: Callable[[], None],
    on_cancel: Callable[[], None],
) -> None:
    ctk.CTkFrame(
        frame,
        fg_color=theme.field_border,
        height=1,
        corner_radius=0,
    ).pack(fill="x", pady=(4, 10))
    btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
    btn_frame.pack(fill="x", pady=(0, 0))
    save_btn = ctk.CTkButton(
        btn_frame, text=t("button.save"), height=38,
        font=(theme.ui_font_family, 14, "bold"), corner_radius=10,
        fg_color=theme.accent, hover_color=theme.accent_hover,
        text_color="#ffffff",
        command=on_save)
    save_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
    attach_ctk_tooltip(save_btn, t("tip.save"))
    cancel_btn = ctk.CTkButton(
        btn_frame, text=t("button.cancel"), height=38,
        font=(theme.ui_font_family, 14), corner_radius=10,
        fg_color=theme.field_bg, hover_color=theme.field_border,
        text_color=theme.text_primary, border_width=1,
        border_color=theme.field_border,
        command=on_cancel)
    cancel_btn.pack(side="right", fill="x", expand=True)
    attach_ctk_tooltip(cancel_btn, t("tip.cancel"))


def populate_first_run_window(
    ctk: Any,
    root: Any,
    theme: CtkTheme,
    *,
    host: str,
    port: int,
    on_done: Callable[[], None],
) -> None:
    fpx, fpy = FIRST_RUN_FRAME_PAD
    frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)

    title_frame = ctk.CTkFrame(frame, fg_color="transparent")
    title_frame.pack(anchor="w", pady=(0, 16), fill="x")
    ctk.CTkFrame(title_frame, fg_color=theme.accent,
                 width=4, height=32, corner_radius=2).pack(side="left", padx=(0, 12))
    ctk.CTkLabel(title_frame, text=t("first_run.title"),
                 font=(theme.ui_font_family, 17, "bold"),
                 text_color=theme.text_primary).pack(side="left")

    textbox = ctk.CTkTextbox(
        frame, font=(theme.ui_font_family, 13), fg_color=theme.bg,
        border_width=0, text_color=theme.text_primary,
        activate_scrollbars=False, wrap="word", height=200,
    )
    textbox._textbox.tag_configure("bold", font=(theme.ui_font_family, 13, "bold"))
    textbox._textbox.configure(spacing1=1, spacing3=1)
    textbox.insert("end", t("first_run.how_to") + "\n", "bold")
    for key in ("first_run.step1", "first_run.step2", "first_run.step3"):
        textbox.insert("end", t(key, host=host, port=port) + "\n")
    textbox.configure(state="disabled")
    textbox.pack(anchor="w", fill="x")

    ctk.CTkFrame(frame, fg_color="transparent", height=16).pack()
    ctk.CTkFrame(frame, fg_color=theme.field_border, height=1,
                 corner_radius=0).pack(fill="x", pady=(0, 16))

    ctk.CTkButton(frame, text=t("button.start"), width=180, height=42,
                  font=(theme.ui_font_family, 15, "bold"), corner_radius=10,
                  fg_color=theme.accent, hover_color=theme.accent_hover,
                  text_color="#ffffff", command=on_done).pack()

    root.protocol("WM_DELETE_WINDOW", on_done)
