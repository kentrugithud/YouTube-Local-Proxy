from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any, Callable, List, Optional, Union

from proxy import __version__, coerce_domain_list
from ui.background_task import BackgroundTask
from ui.connectivity import run_cfworker_multi_test, show_multi_connectivity_results
from ui.ctk_controls import (
    create_entry, create_checkbox, create_label, create_labeled_entry,
    create_config_section, create_combobox,
)
from ui.ctk_theme import CtkTheme
from ui.ctk_tooltip import attach_ctk_tooltip, attach_tooltip_to_widgets
from proxy.web_proxy import DEFAULT_WEB_DOMAINS
from ui.settings import parse_domain_lines, validate_settings
from ui.i18n import (
    label_from_language, language_from_label, language_option_labels,
    set_language, t,
)

RELEASES_URL = "https://github.com/kentrugithud/YouTube-Local-Proxy/releases/"
WORKER_HELP_URL = "https://kentrugit.github.io/cfw-ytlp/"

_INNER_W = 396
_APPEARANCE_KEYS = ("auto", "light", "dark")
_APPEARANCE_TO_CTK = {"auto": "system", "light": "Light", "dark": "Dark"}


def _appearance_options() -> List[str]:
    return [t(f"appearance.{key}") for key in _APPEARANCE_KEYS]


def _appearance_from_cfg(value: str) -> str:
    if value in _APPEARANCE_KEYS:
        return t(f"appearance.{value}")
    return t("appearance.auto")


def _appearance_to_cfg(label: str) -> str:
    for key in _APPEARANCE_KEYS:
        if t(f"appearance.{key}") == label:
            return key
    return "auto"


@dataclass
class Updater:
    """Действия самообновления, их предоставляет windows.py."""
    check: Callable[[], dict]                      # запрос к GitHub -> статус
    asset: Callable[[], Optional[dict]]            # файл для автоустановки или None
    install: Callable[[dict], Optional[str]]       # текст ошибки; при успехе процесс завершается


@dataclass
class TrayConfigFormWidgets:
    host_var: Any
    port_var: Any
    autostart_var: Optional[Any]
    cfproxy_worker_enabled_var: Optional[Any] = None
    cfproxy_worker_domain_var: Optional[Any] = None
    appearance_var: Optional[Any] = None
    language_var: Optional[Any] = None
    check_updates_var: Optional[Any] = None
    connection_mode_var: Optional[Any] = None
    domains_text: Optional[Any] = None


def install_tray_config_form(
    ctk: Any,
    frame: Any,
    theme: CtkTheme,
    cfg: dict,
    default_config: dict,
    *,
    show_autostart: bool = False,
    autostart_value: bool = False,
    on_update_click: Optional[Callable[[], None]] = None,
    updater: Optional[Updater] = None,
) -> TrayConfigFormWidgets:
    set_language(cfg.get("language", default_config["language"]))

    _create_header(ctk, frame, theme)
    appearance_var, language_var = _create_interface(ctk, frame, theme, cfg, default_config)
    host_var, port_var = _create_connection(ctk, frame, theme, cfg, default_config)
    mode_var = _create_method(ctk, frame, theme, cfg)
    worker_enabled_var, worker_domain_var = _create_cfworker(
        ctk, frame, theme, cfg, default_config, bool(cfg.get("no_secure", False)),
        mode_var,
    )
    domains_text = _create_domains(ctk, frame, theme, cfg)
    check_updates_var = _create_updates(
        ctk, frame, theme, cfg, default_config, updater)
    autostart_var = _create_autostart(ctk, frame, theme, show_autostart, autostart_value)

    return TrayConfigFormWidgets(
        host_var=host_var, port_var=port_var,
        autostart_var=autostart_var,
        cfproxy_worker_enabled_var=worker_enabled_var,
        cfproxy_worker_domain_var=worker_domain_var,
        appearance_var=appearance_var,
        language_var=language_var,
        check_updates_var=check_updates_var,
        connection_mode_var=mode_var,
        domains_text=domains_text,
    )


def _create_header(ctk, frame, theme):
    header = ctk.CTkFrame(frame, fg_color="transparent")
    header.pack(fill="x", pady=(0, 2))
    ctk.CTkLabel(
        header, text=t("settings.title"),
        font=(theme.ui_font_family, 17, "bold"),
        text_color=theme.text_primary, anchor="w",
    ).pack(side="left")
    ctk.CTkLabel(
        header, text=f"v{__version__}",
        font=(theme.ui_font_family, 12),
        text_color=theme.text_secondary, anchor="e",
    ).pack(side="right", padx=(4, 0))


def _create_interface(ctk, frame, theme, cfg, default_config):
    lang_cfg = cfg.get("language", default_config["language"])
    appearance_var = ctk.StringVar(
        master=frame, value=_appearance_from_cfg(cfg.get("appearance", "auto"))
    )

    def _on_appearance_change(choice: str) -> None:
        cfg_val = _appearance_to_cfg(choice)
        ctk.set_appearance_mode(_APPEARANCE_TO_CTK[cfg_val])

    ui_inner = create_config_section(ctk, frame, theme, t("section.interface"))
    ui_row = ctk.CTkFrame(ui_inner, fg_color="transparent")
    ui_row.pack(fill="x")

    lang_col = ctk.CTkFrame(ui_row, fg_color="transparent")
    lang_col.pack(side="left", fill="x", expand=True, padx=(0, 8))

    theme_col = ctk.CTkFrame(ui_row, fg_color="transparent")
    theme_col.pack(side="left", fill="x", expand=True, padx=(8, 0))

    language_var = ctk.StringVar(master=frame, value=label_from_language(lang_cfg))
    create_label(ctk, lang_col, theme, t("settings.language"), size=11).pack(
        anchor="w", pady=(0, 2)
    )
    create_combobox(
        ctk, lang_col, theme, variable=language_var,
        values=[label for _, label in language_option_labels()],
    ).pack(fill="x")
    create_label(ctk, theme_col, theme, t("settings.theme"), size=11).pack(
        anchor="w", pady=(0, 2)
    )
    create_combobox(
        ctk, theme_col, theme, variable=appearance_var,
        values=_appearance_options(), command=_on_appearance_change,
    ).pack(fill="x")
    return appearance_var, language_var


def _create_connection(ctk, frame, theme, cfg, default_config):
    conn = create_config_section(ctk, frame, theme, t("section.connection"))

    host_row = ctk.CTkFrame(conn, fg_color="transparent")
    host_row.pack(fill="x")

    host_col, host_var = create_labeled_entry(
        ctk, host_row, theme, t("label.host"),
        cfg.get("host", default_config["host"]), width=160, pack_fill=True,
    )
    host_col.pack(side="left", fill="x", expand=True, padx=(0, 10))

    port_col, port_var = create_labeled_entry(
        ctk, host_row, theme, t("label.port"),
        cfg.get("port", default_config["port"]), width=100,
    )
    port_col.pack(side="left")

    return host_var, port_var


def _create_method(ctk, frame, theme, cfg):
    """Две карточки: Cloudflare Worker / Напрямую (обход DPI)."""
    mode_var = ctk.StringVar(
        master=frame,
        value="direct" if cfg.get("connection_mode") == "direct" else "worker",
    )
    inner = create_config_section(ctk, frame, theme, t("section.method"))
    cards = {}

    def refresh(*_):
        for key, card in cards.items():
            card.configure(
                border_color=theme.accent if mode_var.get() == key else theme.field_border)

    def add_card(key, glyph, title, sub, tip=None):
        card = ctk.CTkFrame(
            inner, fg_color=theme.bg, corner_radius=10,
            border_width=2, border_color=theme.field_border,
        )
        card.pack(fill="x", pady=(0, 6))
        tile = ctk.CTkFrame(card, width=44, height=44, corner_radius=10,
                            fg_color=("#fee2e2", "#3b1212"))
        tile.pack(side="left", padx=(10, 12), pady=10)
        tile.pack_propagate(False)
        glyph_lbl = ctk.CTkLabel(tile, text=glyph, font=(theme.ui_font_family, 20),
                                 text_color=theme.accent)
        glyph_lbl.pack(expand=True)
        text_col = ctk.CTkFrame(card, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True, pady=8)
        title_lbl = ctk.CTkLabel(text_col, text=title, anchor="w",
                                 font=(theme.ui_font_family, 14, "bold"),
                                 text_color=theme.text_primary)
        title_lbl.pack(fill="x")
        sub_lbl = ctk.CTkLabel(text_col, text=sub, anchor="w", justify="left",
                               font=(theme.ui_font_family, 11),
                               text_color=theme.text_secondary, wraplength=300)
        sub_lbl.pack(fill="x")
        widgets = (card, tile, glyph_lbl, text_col, title_lbl, sub_lbl)
        for w in widgets:
            w.bind("<Button-1>", lambda _e, k=key: mode_var.set(k))
        if tip:
            attach_tooltip_to_widgets(list(widgets), tip)
        cards[key] = card

    add_card("worker", "\u2601", t("method.worker.title"), t("method.worker.sub"),
             t("tip.cfworker_domain"))
    add_card("direct", "\u21c4", t("method.direct.title"), t("method.direct.sub"),
             t("tip.method_direct"))
    mode_var.trace_add("write", refresh)
    refresh()
    return mode_var


def _create_cfworker(ctk, frame, theme, cfg, default_config, no_secure, mode_var):
    inner = create_config_section(ctk, frame, theme, t("section.cfworker"))

    row = ctk.CTkFrame(inner, fg_color="transparent")
    row.pack(fill="x", pady=(0, 4))
    lbl = create_label(ctk, row, theme, t("label.cfworker_domains"), size=11)
    lbl.pack(side="left", anchor="w", pady=(0, 2))

    input_row = ctk.CTkFrame(inner, fg_color="transparent")
    input_row.pack(fill="x")

    saved_domains = coerce_domain_list(
        cfg.get("cfproxy_worker_domain", default_config.get("cfproxy_worker_domain", ""))
    )
    enabled_var = ctk.BooleanVar(
        master=frame, value=cfg.get("cfproxy_worker_enabled", bool(saved_domains))
    )
    cb = create_checkbox(ctk, input_row, theme, t("label.cf_custom_domain"), enabled_var)
    cb.pack(side="left", padx=(0, 10))
    attach_ctk_tooltip(cb, t("tip.cfworker_domain"))

    domain_var = ctk.StringVar(master=frame, value=", ".join(saved_domains))
    entry = create_entry(ctk, input_row, theme, var=domain_var, height=32, radius=8)
    entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
    attach_tooltip_to_widgets([lbl, entry], t("tip.cfworker_domain"))

    test_task = BackgroundTask(frame.winfo_toplevel())

    def _sync_test_button(*_):
        enabled = (
            not test_task.running and mode_var.get() == "worker"
            and enabled_var.get() and bool(coerce_domain_list(domain_var.get()))
        )
        test_btn.configure(state="normal" if enabled else "disabled")

    def _on_test():
        domains = coerce_domain_list(domain_var.get())
        if test_task.running or not enabled_var.get() or not domains:
            return
        test_btn.configure(text=t("button.test_loading"), state="disabled")

        def finish():
            test_btn.configure(text=t("button.test"))
            _sync_test_button()

        test_task.start(
            lambda: run_cfworker_multi_test(domains, secure=not no_secure),
            lambda per: show_multi_connectivity_results(
                per, parent=frame.winfo_toplevel()),
            finish,
        )

    ctk.CTkButton(
        input_row, text="?", width=28, height=32,
        font=(theme.ui_font_family, 14), corner_radius=8,
        fg_color=theme.accent, hover_color=theme.accent_hover,
        text_color="#ffffff", border_width=1, border_color=theme.field_border,
        command=lambda: webbrowser.open(WORKER_HELP_URL),
    ).pack(side="right")

    test_btn = ctk.CTkButton(
        row, text=t("button.test"), width=56, height=28,
        font=(theme.ui_font_family, 13), corner_radius=8,
        fg_color=theme.accent, hover_color=theme.accent_hover,
        text_color="#ffffff", border_width=1, border_color=theme.field_border,
        command=_on_test,
    )
    test_btn.pack(side="right")

    def _sync_entry(*_):
        worker_on = mode_var.get() == "worker"
        cb.configure(state="normal" if worker_on else "disabled")
        entry.configure(
            state="normal" if (worker_on and enabled_var.get()) else "disabled")
        _sync_test_button()

    enabled_var.trace_add("write", _sync_entry)
    mode_var.trace_add("write", _sync_entry)
    domain_var.trace_add("write", _sync_test_button)
    _sync_entry()
    return enabled_var, domain_var


def _create_domains(ctk, frame, theme, cfg):
    """Список доменов, которые идут через обход (по одному на строку)."""
    inner = create_config_section(ctk, frame, theme, t("section.domains"))
    create_label(ctk, inner, theme, t("label.domains_hint"), size=11).pack(
        anchor="w", pady=(0, 6))
    box = ctk.CTkTextbox(
        inner, height=120, font=(theme.mono_font_family, 12),
        fg_color=theme.bg, border_width=1, border_color=theme.field_border,
        corner_radius=8, text_color=theme.text_primary,
    )
    box.pack(fill="x")
    domains = list(cfg.get("web_domains") or DEFAULT_WEB_DOMAINS)
    box.insert("1.0", "\n".join(domains))
    return box


def _create_updates(ctk, frame, theme, cfg, default_config, updater):
    inner = create_config_section(ctk, frame, theme, t("section.updates"))
    check_var = ctk.BooleanVar(
        master=frame, value=bool(cfg.get("check_updates", default_config["check_updates"])))
    create_checkbox(ctk, inner, theme, t("label.check_updates"), check_var).pack(
        anchor="w", pady=(0, 8))

    if updater is not None:
        top = frame.winfo_toplevel()
        check_task = BackgroundTask(top)
        install_task = BackgroundTask(top)
        title = t("update.title")

        update_btn = ctk.CTkButton(
            inner, text=t("button.update"), height=32,
            font=(theme.ui_font_family, 13), corner_radius=8,
            fg_color=theme.accent, hover_color=theme.accent_hover,
            text_color="#ffffff",
        )
        update_btn.pack(fill="x", pady=(0, 6))

        def _reset():
            update_btn.configure(text=t("button.update"), state="normal")

        def _after_install(error):
            if error:
                messagebox.showerror(title, t("update.error", msg=error), parent=top)

        def _after_check(st):
            if st.get("error"):
                messagebox.showerror(
                    title, t("update.check_failed", error=st["error"]), parent=top)
                return
            if not st.get("has_update"):
                messagebox.showinfo(
                    title, t("update.up_to_date", version=__version__), parent=top)
                return
            ver = st.get("latest") or "?"
            asset = updater.asset()
            if asset is None:
                if messagebox.askyesno(
                        title, t("update.manual", version=ver, current=__version__),
                        parent=top):
                    webbrowser.open(st.get("html_url") or RELEASES_URL)
                return
            if messagebox.askyesno(
                    title, t("update.available_install", version=ver, current=__version__),
                    parent=top):
                update_btn.configure(text=t("update.downloading"), state="disabled")
                install_task.start(lambda: updater.install(asset), _after_install, _reset)

        def _on_update():
            if check_task.running or install_task.running:
                return
            update_btn.configure(text=t("update.checking"), state="disabled")
            check_task.start(updater.check, _after_check,
                             lambda: None if install_task.running else _reset())

        update_btn.configure(command=_on_update)

    ctk.CTkButton(
        inner, text=t("button.open_release"), height=32,
        font=(theme.ui_font_family, 13), corner_radius=8,
        fg_color=theme.field_bg, hover_color=theme.field_border,
        text_color=theme.text_primary, border_width=1,
        border_color=theme.field_border,
        command=lambda: webbrowser.open(RELEASES_URL),
    ).pack(fill="x")
    return check_var


def _create_autostart(ctk, frame, theme, show_autostart, autostart_value):
    autostart_var = None
    if show_autostart:
        sys_inner = create_config_section(ctk, frame, theme, t("section.windows_startup"), bottom_spacer=4)
        autostart_var = ctk.BooleanVar(master=frame, value=autostart_value)
        as_cb = create_checkbox(ctk, sys_inner, theme, t("label.autostart"), autostart_var)
        as_cb.pack(anchor="w", pady=(0, 4))
        as_hint = create_label(
            ctk, sys_inner, theme,
            t("label.autostart_hint"),
            size=11, justify="left", wraplength=_INNER_W,
        )
        as_hint.pack(anchor="w")
        attach_tooltip_to_widgets([as_cb, as_hint], t("tip.autostart"))
    return autostart_var


def validate_config_form(
    widgets: TrayConfigFormWidgets,
    default_config: dict,
    *,
    include_autostart: bool,
) -> Union[dict, str]:
    values = {
        "host": widgets.host_var.get(),
        "port": widgets.port_var.get(),
    }
    if widgets.cfproxy_worker_enabled_var is not None:
        values["cfproxy_worker_enabled"] = bool(widgets.cfproxy_worker_enabled_var.get())
    if widgets.cfproxy_worker_domain_var is not None:
        values["cfproxy_worker_domain"] = widgets.cfproxy_worker_domain_var.get()
    if include_autostart:
        values["autostart"] = (
            bool(widgets.autostart_var.get())
            if widgets.autostart_var is not None else False
        )
    if widgets.appearance_var is not None:
        values["appearance"] = _appearance_to_cfg(widgets.appearance_var.get())
    if widgets.connection_mode_var is not None:
        values["connection_mode"] = (
            "direct" if widgets.connection_mode_var.get() == "direct" else "worker")
    if widgets.check_updates_var is not None:
        values["check_updates"] = bool(widgets.check_updates_var.get())
    if widgets.language_var is not None:
        values["language"] = language_from_label(widgets.language_var.get()).value
    if widgets.domains_text is not None:
        domains = parse_domain_lines(
            widgets.domains_text.get("1.0", "end"), DEFAULT_WEB_DOMAINS)
        if isinstance(domains, str):
            return domains
        values["web_domains"] = domains
    return validate_settings(values, default_config)
