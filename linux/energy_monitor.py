#!/usr/bin/python3
"""Elementary OS desktop client for the existing, independently running collector."""
import json
import os
from pathlib import Path
import sys
import time
import threading
from urllib.parse import quote, urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Soup", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo, WebKit, Soup

from client_core import (DEFAULT_ORIGIN, TEMPERATURE_TREND_CACHE_KEY, cache_key, display_state, private_json,
                         read_json, validate_origin, validate_summary, validate_temperature_history,
                         load_temperature_trend_cache, load_ui_state, OutageNotifier, sidebar_footer, series_stats)

from chart import Chart
from native_auth import load_credentials, session_cookie
from web_theme import build_css as web_css

STATE = Path(GLib.get_user_state_dir()) / "energy-monitor"
CONFIG = Path(GLib.get_user_config_dir()) / "energy-monitor/client.json"
DATA = Path(GLib.get_user_data_dir()) / "energy-monitor"
UI_STATE = CONFIG.with_name("ui.json")
NARROW_WIDTH = 760  # below this the sidebar collapses by itself


def shortcuts_xml(entries):
    """GtkShortcutsWindow definition: every accelerator the app registers, grouped for the help window."""
    from xml.sax.saxutils import escape
    def group(title, rows):
        body = "".join('<child><object class="GtkShortcutsShortcut"><property name="accelerator">%s</property>'
                       '<property name="title">%s</property></object></child>' % (escape(a), escape(t)) for a, t in rows)
        return ('<child><object class="GtkShortcutsGroup"><property name="title">%s</property>%s</object></child>'
                % (escape(title), body))
    pages = [("<Control>%d" % (i + 1), "Open " + label) for i, (label, _) in enumerate(entries[:9])]
    return ('<interface><object class="GtkShortcutsWindow" id="shortcuts"><property name="modal">1</property>'
            '<child><object class="GtkShortcutsSection"><property name="section-name">shortcuts</property>'
            '<property name="max-height">14</property>' +
            group("Pages", pages) +
            group("General", [("<Control>k", "Go to page…"), ("F9", "Show or hide the sidebar"),
                              ("<Control>r F5", "Refresh"), ("F1", "About Energy Monitor"),
                              ("<Control>comma", "Preferences"),
                              ("<Control>question", "Keyboard shortcuts"), ("<Control>q", "Quit")]) +
            '</object></child></object></interface>')


# (label, collector path, symbolic icon); path None opens the native temperature window.
NAVIGATION = (
    ("Monitor", (("Dashboard", "/", "go-home-symbolic"),
                 ("Reports", "/reports", "document-properties-symbolic"),
                 ("Trends", "/trends", "view-list-symbolic"),
                 ("Collector Log", "/log", "utilities-terminal-symbolic"))),
    ("Home", (("Temperatures", None, "weather-clear-symbolic"),
              ("Home 3D", "/home-3d", "find-location-symbolic"),
              ("House · Lab", "/house", "applications-science-symbolic"),
              ("Radon", "/radon", "dialog-information-symbolic"))),
    ("Devices", (("Aqara", "/aqara", "network-wireless-symbolic"),
                 ("HVAC", "/mitsubishi", "weather-clear-night-symbolic"),
                 ("Kasa", "/kasa", "preferences-system-network-symbolic"))),
    ("App", (("Guide", "/guide", "help-contents-symbolic"),
             ("Settings", "/settings", "preferences-system-symbolic"))),
)


class EnergyMonitor(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.techmore.EnergyMonitor",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.web = None
        self.connected = False
        self.record = None
        self.pending = False
        self.auth_pending = False
        self.last_auth_attempt = 0
        self.last_auth_success = 0
        self.transport_failed = False
        self.history_windows = {}
        self.ui = load_ui_state(UI_STATE)
        self.pending_path = self.ui['page']
        self.notifier = OutageNotifier()
        self.narrow = False
        self.sidebar_before_narrow = True
        self.temperature_window = None
        self.temperature_callbacks = {}
        self.temperature_request = 0
        self.trend_record = None
        self.last_trend_attempt = 0
        self.origin = DEFAULT_ORIGIN
        try:
            self.origin = validate_origin((read_json(CONFIG) or {}).get("origin", DEFAULT_ORIGIN))
        except (ValueError, OSError):
            pass

    def follow_system_colour_scheme(self):
        """elementary's Granite apps copy the system light/dark choice onto GTK; a plain GTK app does not,
        so without this the client stays light on a dark desktop. The schema is looked up first because
        Gio.Settings aborts the process when a schema is missing."""
        source = Gio.SettingsSchemaSource.get_default()
        if not source or not source.lookup("org.gnome.desktop.interface", True):
            return
        self.interface_settings = Gio.Settings.new("org.gnome.desktop.interface")
        gtk_settings = Gtk.Settings.get_default()

        def sync(*_):
            gtk_settings.set_property("gtk-application-prefer-dark-theme",
                                      self.interface_settings.get_string("color-scheme") == "prefer-dark")
        self.interface_settings.connect("changed::color-scheme", sync)
        sync()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.follow_system_colour_scheme()
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.load_theme()
        self.hold()
        for name, callback in (("dashboard", lambda *_: self.open_path("/")),
                               ("barn-first", lambda *_: self.open_path("/home-3d?level=first#home3d-floor-plan")),
                               ("barn-upstairs", lambda *_: self.open_path("/home-3d?level=upstairs#home3d-floor-plan")),
                               ("barn-attic", lambda *_: self.open_path("/home-3d?level=attic&metric=temperature_f#home3d-floor-plan")),
                               ("temperatures", lambda *_: self.open_temperatures()),
                               ("temperature-plan", lambda *_: self.open_path("/home-3d?level=all&metric=temperature_f#home3d-floor-plan")),
                               ("settings", lambda *_: self.open_path("/settings")),
                               ("connection", lambda *_: self.connection_dialog()),
                               ("refresh", lambda *_: self.refresh()),
                               ("switcher", lambda *_: self.quick_switcher()),
                               ("about", lambda *_: self.about_dialog()),
                               ("shortcuts", lambda *_: self.shortcuts_dialog()),
                               ("preferences", lambda *_: self.preferences_dialog()),
                               ("quit", lambda *_: self.quit())) + tuple(
                (f"page{i + 1}", (lambda target: lambda *_: self.open_nav(target))(entry))
                for i, entry in enumerate(self.nav_entries()[:9])):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        for action, keys in (("app.refresh", ["<Control>r", "F5"]), ("app.quit", ["<Control>q"]),
                             ("app.switcher", ["<Control>k"]), ("app.about", ["F1"]),
                             ("app.shortcuts", ["<Control>question", "<Control>slash"]),
                             ("app.preferences", ["<Control>comma"])) + tuple(
                (f"app.page{i}", [f"<Control>{i}"]) for i in range(1, 10)):
            self.set_accels_for_action(action, keys)
        self.connect("window-added", lambda app, window: self.apply_density(window))
        self.build_window()
        self.watch_ui_state()
        self.restore_cache()
        if not self.authenticate_device():
            self.web.load_uri(self.origin + self.pending_path)
        GLib.timeout_add_seconds(30, self.refresh)

    def load_theme(self):
        # One stylesheet built on the elementary named colours: light, dark and accent follow the system.
        self.css.load_from_path(str(Path(__file__).with_name("theme.css")))
        settings = Gtk.Settings.get_default()
        if not getattr(self, "theme_hooked", False):
            self.theme_hooked = True
            for key in ("gtk-application-prefer-dark-theme", "gtk-theme-name"):
                settings.connect("notify::" + key, lambda *_: GLib.idle_add(self.apply_web_theme))
        self.apply_web_theme()

    def apply_web_theme(self):
        """Re-skin the embedded dashboard with the system's light/dark palette and accent colour."""
        if not self.web or not self.window:
            return False
        colour = self.window.get_color()
        dark = (0.299 * colour.red + 0.587 * colour.green + 0.114 * colour.blue) > 0.5
        found, accent = self.window.get_style_context().lookup_color("accent_color")
        # Paint the theme's page colour before the first frame so pages never flash white in dark mode.
        background = Gdk.RGBA()
        background.parse("#2b2b2b" if dark else "#f5f5f5")
        self.web.set_background_color(background)
        manager = self.web.get_user_content_manager()
        manager.remove_all_style_sheets()
        manager.add_style_sheet(WebKit.UserStyleSheet.new(
            web_css(dark, accent if found else None, self.ui['density'], self.ui['dashboard_style']),
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserStyleLevel.USER, None, None))
        return False

    def do_activate(self):
        self.window.present()

    def do_command_line(self, command):
        args = command.get_arguments()[1:]
        if "--background" in args:
            return 0
        if "--connection" in args:
            self.connection_dialog()
        elif "--settings" in args:
            self.open_path("/settings")
        elif "--floor" in args:
            index = args.index("--floor")
            if index + 1 >= len(args) or args[index + 1] not in ('first', 'upstairs', 'attic', 'all'):
                return 2
            self.open_path('/home-3d?level=' + args[index + 1] + '#home3d-floor-plan')
        elif '--temperatures' in args:
            self.open_temperatures()
        elif '--temperature-plan' in args:
            self.open_path('/home-3d?level=all&metric=temperature_f#home3d-floor-plan')
        elif '--temperature' in args:
            index = args.index('--temperature')
            if index + 2 >= len(args):
                return 2
            self.open_temperatures(args[index + 1], args[index + 2])
        elif "--refresh" in args:
            self.refresh()
        elif "--quit" in args:
            self.quit()
        elif "--history" in args:
            index = args.index("--history")
            if index + 1 >= len(args):
                return 2
            self.open_history(args[index + 1])
        elif "--circuit" in args:
            index = args.index("--circuit")
            if index + 1 >= len(args):
                return 2
            self.open_path("/circuit/" + quote(args[index + 1], safe=""))
        else:
            self.activate()
        return 0

    def build_window(self):
        self.window = Gtk.ApplicationWindow(application=self, title="Energy Monitor",
                                            default_width=self.ui["width"], default_height=self.ui["height"])
        self.window.connect("close-request", self.hide_window)
        header = Gtk.HeaderBar()
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: self.web.reload())
        header.pack_start(refresh)
        menu = Gio.Menu()
        # Sections draw the separators elementary menus use between groups of related items.
        for section in ((("Dashboard", "dashboard"), ("Temperatures", "temperatures"),
                         ("Temperature floor plan", "temperature-plan")),
                        (("Barn · First floor", "barn-first"), ("Barn · Upstairs", "barn-upstairs"),
                         ("Barn · Attic", "barn-attic")),
                        (("Collector Settings", "settings"), ("Collector Connection…", "connection")),
                        (("Quick Switcher…", "switcher"), ("Refresh", "refresh"), ("Preferences…", "preferences")),
                        (("Keyboard Shortcuts", "shortcuts"), ("About Energy Monitor", "about"), ("Quit", "quit"))):
            part = Gio.Menu()
            for label, action in section:
                part.append(label, "app." + action)
            menu.append_section(None, part)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"))
        self.window.set_titlebar(header)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Window title with the connection status as its subtitle, as in elementary's own apps.
        self.status = Gtk.Label(label="Connecting to the energy collector…", ellipsize=Pango.EllipsizeMode.END,
                                max_width_chars=70)
        self.status.add_css_class("dim-label")
        self.status.add_css_class("caption")
        heading = Gtk.Label(label="Energy Monitor")
        heading.add_css_class("title")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.append(heading)
        titles.append(self.status)
        header.set_title_widget(titles)
        DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
        session = WebKit.NetworkSession.new(str(DATA / "web-data"), str(DATA / "web-cache"))
        cookies = session.get_cookie_manager()
        self.cookies = cookies
        cookies.set_persistent_storage(str(DATA / "cookies.sqlite"), WebKit.CookiePersistentStorage.SQLITE)
        manager = WebKit.UserContentManager()
        manager.register_script_message_handler("energy", None)
        manager.connect("script-message-received::energy", self.receive_summary)
        manager.register_script_message_handler("history", None)
        manager.connect("script-message-received::history", self.receive_history)
        manager.register_script_message_handler('temperatures', None)
        manager.connect('script-message-received::temperatures', self.receive_temperatures)
        manager.register_script_message_handler('trend', None)
        manager.connect('script-message-received::trend', self.receive_trend)
        self.web = WebKit.WebView(network_session=session, user_content_manager=manager,
                                  vexpand=True, hexpand=True)
        self.web.connect("load-changed", self.loaded)
        self.web.connect("load-failed", self.failed)
        self.web.connect("load-failed-with-tls-errors", self.tls_failed)
        self.web.connect("create", self.popup)
        session.connect("download-started", self.download)
        # Offline page: a clear state with actions instead of a blank or broken web view.
        offline = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                          halign=Gtk.Align.CENTER, vexpand=True, hexpand=True)
        offline.append(Gtk.Image(icon_name="network-offline-symbolic", pixel_size=64))
        offline.get_first_child().add_css_class("dim-label")
        self.offline_title = Gtk.Label(label="Can’t reach the collector")
        self.offline_title.add_css_class("energy-offline-title")
        self.offline_text = Gtk.Label(label="", wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=48)
        self.offline_text.add_css_class("dim-label")
        offline.append(self.offline_title)
        offline.append(self.offline_text)
        actions = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=12)
        retry = Gtk.Button(label="Try Again")
        retry.add_css_class("suggested-action")
        retry.connect("clicked", lambda *_: (self.show_page("web"), self.web.reload()))
        change = Gtk.Button(label="Collector Connection…")
        change.connect("clicked", lambda *_: self.connection_dialog())
        actions.append(retry)
        actions.append(change)
        offline.append(actions)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True, hexpand=True)
        self.pages.add_named(self.web, "web")
        self.pages.add_named(offline, "offline")
        content.append(self.pages)
        # Source-list sidebar, as in elementary's own apps; it replaces the web page's top bar.
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav.add_css_class("navigation-sidebar")
        self.nav.add_css_class("energy-sidebar")
        self.nav_rows = []
        for group, items in NAVIGATION:
            heading = Gtk.ListBoxRow(selectable=False, activatable=False)
            title = Gtk.Label(label=group, xalign=0, margin_start=6, margin_top=10, margin_bottom=2)
            title.add_css_class("energy-side-heading")
            heading.set_child(title)
            self.nav.append(heading)
            for label, path, icon in items:
                row = Gtk.ListBoxRow()
                box = Gtk.Box(spacing=9, margin_top=2, margin_bottom=2, margin_start=3, margin_end=3)
                box.append(Gtk.Image.new_from_icon_name(icon))
                box.append(Gtk.Label(label=label, xalign=0, hexpand=True))
                row.set_child(box)
                row.nav_path = path
                self.nav.append(row)
                self.nav_rows.append((path, row))
        self.nav.connect("row-activated", self.nav_activated)
        scroller = Gtk.ScrolledWindow(child=self.nav, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        # Glanceable live power at the foot of the source list.
        self.footer_dot = Gtk.Image(icon_name="media-record-symbolic", pixel_size=10, valign=Gtk.Align.CENTER)
        self.footer_dot.add_css_class("energy-dot")
        self.footer_power = Gtk.Label(label="—", xalign=0)
        self.footer_power.add_css_class("energy-footer-power")
        self.footer_caption = Gtk.Label(label="", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.footer_caption.add_css_class("dim-label")
        self.footer_caption.add_css_class("caption")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        texts.append(self.footer_power)
        texts.append(self.footer_caption)
        footer = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=8, margin_bottom=10)
        footer.add_css_class("energy-sidebar-footer")
        footer.append(self.footer_dot)
        footer.append(texts)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=190)
        side.append(scroller)
        side.append(Gtk.Separator())
        side.append(footer)
        self.sidebar = Gtk.Revealer(child=Gtk.Box(), transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
                                    reveal_child=self.ui["sidebar"])
        pane = Gtk.Box()
        pane.append(side)
        pane.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.sidebar.set_child(pane)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=self.ui["sidebar"],
                                  tooltip_text="Show sidebar (F9)")
        toggle.bind_property("active", self.sidebar, "reveal-child")
        header.pack_start(toggle)
        self.sidebar_toggle = toggle
        self.set_accels_for_action("win.sidebar", ["F9"])
        action = Gio.SimpleAction.new("sidebar", None)
        action.connect("activate", lambda *_: toggle.set_active(not toggle.get_active()))
        self.window.add_action(action)
        shell = Gtk.Box()
        shell.append(self.sidebar)
        content.set_hexpand(True)
        shell.append(content)
        self.window.set_child(shell)
        self.apply_web_theme()
        self.web.connect("notify::uri", lambda *_: self.sync_navigation())
        self.window.connect("map", lambda *_: GLib.idle_add(self.apply_web_theme))
        self.window.add_tick_callback(self.responsive_sidebar)
        self.first_load = True

    def nav_activated(self, box, row):
        if row.nav_path is None:
            self.open_temperatures()
        else:
            self.open_path(row.nav_path)

    def sync_navigation(self):
        path = urlsplit(self.web.get_uri() or "").path or "/"
        for target, row in self.nav_rows:
            if target and (path == target or (target != "/" and path.startswith(target + "/"))):
                self.nav.select_row(row)
                return
        if path.startswith("/circuit"):
            self.nav.select_row(self.nav_rows[0][1])

    def save_ui_state(self):
        width, height = self.window.get_default_size()
        path = urlsplit(self.web.get_uri() or "").path or "/"
        query = urlsplit(self.web.get_uri() or "").query
        state = {"width": width, "height": height, "density": self.ui["density"],
                 "dashboard_style": self.ui["dashboard_style"],
                 "sidebar": self.sidebar_before_narrow if self.narrow else self.sidebar_toggle.get_active(),
                 "page": path + ("?" + query if query else "")}
        try:
            private_json(UI_STATE, state)
        except OSError:
            pass

    def hide_window(self, *_):
        self.save_ui_state()
        self.window.set_visible(False)
        return True

    def responsive_sidebar(self, widget, clock):
        """Collapse the sidebar below NARROW_WIDTH and restore the user's choice when there is room again."""
        width = widget.get_width()
        if width <= 1:
            return GLib.SOURCE_CONTINUE
        if width < NARROW_WIDTH and not self.narrow:
            self.narrow = True
            self.sidebar_before_narrow = self.sidebar_toggle.get_active()
            self.sidebar_toggle.set_active(False)
        elif width >= NARROW_WIDTH + 40 and self.narrow:
            self.narrow = False
            self.sidebar_toggle.set_active(self.sidebar_before_narrow)
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def nav_entries():
        return [(label, path) for _, items in NAVIGATION for label, path, _ in items]

    def open_nav(self, entry):
        if entry[1] is None:
            self.open_temperatures()
        else:
            self.open_path(entry[1])

    def quick_switcher(self):
        """Ctrl+K: type to filter pages, Enter opens the first match, Escape closes."""
        window = Gtk.Window(title="Go to…", transient_for=self.window, modal=True, default_width=420,
                            default_height=360, destroy_with_parent=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        entry = Gtk.SearchEntry(placeholder_text="Go to a page…", activates_default=False)
        entries = self.nav_entries()
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        listbox.add_css_class("boxed-list")
        rows = []
        for label, path in entries:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=label, xalign=0, margin_top=8, margin_bottom=8, margin_start=10))
            row.nav_entry = (label, path)
            listbox.append(row)
            rows.append(row)
        def refilter(*_):
            text = entry.get_text().strip().lower()
            first = None
            for row in rows:
                match = text in row.nav_entry[0].lower()
                row.set_visible(match)
                if match and first is None:
                    first = row
            if first:
                listbox.select_row(first)
        def choose(*_):
            row = listbox.get_selected_row()
            if row and row.get_visible():
                window.destroy()
                self.open_nav(row.nav_entry)
        entry.connect("search-changed", refilter)
        entry.connect("activate", choose)
        listbox.connect("row-activated", lambda _, row: (listbox.select_row(row), choose()))
        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                             Gtk.CallbackAction.new(lambda *_: window.destroy() or True)))
        window.add_controller(escape)
        scroll = Gtk.ScrolledWindow(child=listbox, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        box.append(entry)
        box.append(scroll)
        window.set_child(box)
        refilter()
        window.present()
        entry.grab_focus()

    def watch_ui_state(self):
        """The panel dropdown can change density; pick that up live instead of waiting for a restart."""
        try:
            self.ui_monitor = Gio.File.new_for_path(str(UI_STATE)).monitor_file(Gio.FileMonitorFlags.NONE, None)
            self.ui_monitor.connect("changed", lambda *_: GLib.timeout_add(150, self.reload_ui_state))
        except GLib.Error:
            self.ui_monitor = None

    def reload_ui_state(self):
        saved = load_ui_state(UI_STATE)
        changed = [key for key in ("density", "dashboard_style") if saved[key] != self.ui[key]]
        if changed:
            for key in changed:
                self.ui[key] = saved[key]
            self.apply_density()
            self.apply_web_theme()
            self.web.reload()
        return GLib.SOURCE_REMOVE

    def apply_density(self, window=None):
        """Compact mode is one CSS class on every window (theme.css) plus the web style sheet's compact rules."""
        for target in ([window] if window else Gtk.Window.list_toplevels()):
            (target.add_css_class if self.ui["density"] == "compact" else target.remove_css_class)("energy-compact")

    def preferences_dialog(self):
        """Visual preferences, applied as you choose: density and the dashboard's visual style."""
        window = Gtk.Window(title="Preferences", transient_for=self.window, modal=True, default_width=460,
                            resizable=False, destroy_with_parent=True)
        window.add_css_class("energy-monitor")
        header = Gtk.HeaderBar()
        window.set_titlebar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        def row(title, subtitle, choices, key, values):
            line = Gtk.Box(spacing=12, margin_top=9, margin_bottom=9, margin_start=12, margin_end=12)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            text.append(Gtk.Label(label=title, xalign=0))
            hint = Gtk.Label(label=subtitle, xalign=0, wrap=True, max_width_chars=34)
            hint.add_css_class("dim-label")
            hint.add_css_class("caption")
            text.append(hint)
            drop = Gtk.DropDown.new_from_strings(choices)
            drop.set_valign(Gtk.Align.CENTER)
            drop.set_selected(values.index(self.ui[key]))
            def changed(*_):
                self.ui[key] = values[drop.get_selected()]
                self.apply_density()
                self.apply_web_theme()
                self.web.reload()
                self.save_ui_state()
            drop.connect("notify::selected", changed)
            line.append(text)
            line.append(drop)
            listbox.append(Gtk.ListBoxRow(child=line, activatable=False))
        row("Density", "Compact tightens rows, padding and type to show more data at once.",
            ["Comfortable", "Compact"], "density", ["comfortable", "compact"])
        row("Dashboard style", "Native follows your elementary OS theme and accent. Original keeps the collector’s olive design.",
            ["elementary OS", "Original"], "dashboard_style", ["elementary", "original"])
        box.append(listbox)
        note = Gtk.Label(label="The panel dropdown follows the density setting. The Panel/List switch lives in the dropdown.",
                         xalign=0, wrap=True)
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        box.append(note)
        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                             Gtk.CallbackAction.new(lambda *_: window.destroy() or True)))
        window.add_controller(escape)
        window.set_child(box)
        window.present()

    def shortcuts_dialog(self):
        window = Gtk.Builder.new_from_string(shortcuts_xml(self.nav_entries()), -1).get_object("shortcuts")
        window.set_transient_for(self.window)
        window.present()

    def about_dialog(self):
        about = Gtk.AboutDialog(transient_for=self.window, modal=True, program_name="Energy Monitor",
                                logo_icon_name="org.techmore.EnergyMonitor", license_type=Gtk.License.MIT_X11,
                                comments="Energy, circuits, costs and home sensors from your collector.",
                                website="https://github.com/techmore/Emporia-Vue3-Mac-Utility-Monitor",
                                website_label="Project on GitHub")
        about.present()

    def popup(self, source, navigation):
        view = WebKit.WebView.new_with_related_view(source)
        window = Gtk.ApplicationWindow(application=self, title="Energy Monitor Sign In",
                                       default_width=700, default_height=750)
        window.set_child(view)
        view.connect("close", lambda *_: window.destroy())
        window.present()
        return view

    def download(self, session, download):
        def destination(item, suggested):
            directory = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
                             or Path.home() / "Downloads")
            directory.mkdir(parents=True, exist_ok=True)
            name = Path(suggested).name or "energy-export"
            target = directory / name
            if target.exists():
                target = directory / (str(time.time_ns()) + "-" + name)
            item.set_destination(target.as_uri())
            return True
        download.connect("decide-destination", destination)

    def cache_file(self):
        return STATE / "responses" / cache_key(self.origin, "/api/menu-summary")

    def restore_cache(self):
        try:
            self.record = read_json(self.cache_file())
        except (OSError, ValueError):
            self.record = None
        self.trend_record = load_temperature_trend_cache(
            STATE / 'responses', self.origin, self.identity()[0])
        self.publish()

    def notify_outage(self):
        event = self.notifier.update(self.connected, time.monotonic())
        if not event:
            return
        if event == "offline":
            note = Gio.Notification.new("Energy collector offline")
            note.set_body("Readings are paused. Recorded history stays available and the client keeps retrying.")
            note.set_icon(Gio.ThemedIcon.new("network-offline-symbolic"))
            note.set_default_action("app.dashboard")
            self.send_notification("collector-offline", note)
        else:
            self.withdraw_notification("collector-offline")
            note = Gio.Notification.new("Energy collector is back")
            note.set_body("Live readings have resumed.")
            note.set_icon(Gio.ThemedIcon.new("network-transmit-receive-symbolic"))
            self.send_notification("collector-online", note)
            GLib.timeout_add_seconds(30, lambda: (self.withdraw_notification("collector-online"), False)[1])

    def publish(self):
        self.notify_outage()
        state = display_state(self.record, self.origin, self.connected)
        if (self.trend_record and self.trend_record.get('origin') == self.origin
                and self.trend_record.get('source_id') == self.identity()[0]):
            payload = self.trend_record['data']
            state['temperature_history'] = {
                'times': payload['times'], 'start': payload['start'], 'end': payload['end'],
                'fetched_at': self.trend_record['fetched_at'],
                'series': [{'source': row['source'], 'sensor_id': row['sensor_id'],
                            'values': row['values']} for row in payload['series']],
            }
        headline, caption, live = sidebar_footer(state)
        self.footer_power.set_label(headline)
        self.footer_caption.set_label(caption)
        (self.footer_dot.remove_css_class if live else self.footer_dot.add_css_class)("energy-offline")
        self.footer_dot.set_tooltip_text("Collector live" if live else "Collector offline or stale")
        private_json(STATE / "summary.json", state)
        if self.temperature_window:
            self.temperature_window.update_current(state.get('temperature_sensors', []))

    def show_page(self, name, text=""):
        self.pages.set_visible_child_name(name)
        if text:
            self.offline_text.set_label(text)

    def loaded(self, view, event):
        if event == WebKit.LoadEvent.FINISHED:
            self.show_page("web")
            self.refresh()

    def failed(self, view, event, uri, error):
        self.transport_failed = True
        self.pending = False
        self.connected = False
        self.status.set_label("Collector unavailable. Check your connection or open Collector Connection.")
        self.show_page("offline", "Check your network connection, or choose a different collector address. "
                                  "Recorded history stays available in the menu bar while you are offline.")
        self.publish()
        return False

    def tls_failed(self, view, uri, certificate, errors):
        self.failed(view, None, uri, None)
        self.status.set_label("The collector's TLS connection failed. Its certificate was not bypassed.")
        self.show_page("offline", "The collector’s secure connection could not be verified, so nothing was loaded. "
                                  "The certificate is never bypassed.")
        return False

    def authenticate_device(self):
        try:
            credentials = load_credentials(CONFIG.with_name("device.json"), self.origin)
        except (ValueError, OSError):
            self.status.set_label("Device credential unavailable. Check Collector Connection.")
            return True
        if not credentials:
            return False
        if self.auth_pending:
            return True
        if time.monotonic() - self.last_auth_attempt < 60:
            return False
        self.auth_pending = True
        self.last_auth_attempt = time.monotonic()
        origin = self.origin
        self.status.set_label("Connecting securely to the energy collector…")
        def work():
            try:
                value = session_cookie(credentials)
            except Exception:
                GLib.idle_add(failed)
            else:
                GLib.idle_add(install_cookie, value)
        def failed():
            self.auth_pending = False
            self.connected = False
            self.status.set_label("Collector unavailable. Device sign-in will retry automatically.")
            self.publish()
        def install_cookie(value):
            if self.origin != origin:
                self.auth_pending = False
                return
            cookie = Soup.Cookie.new("__Host-energy", value, urlsplit(origin).hostname, "/", 86400)
            cookie.set_secure(True)
            cookie.set_http_only(True)
            cookie.set_same_site_policy(Soup.SameSitePolicy.LAX)
            self.cookies.add_cookie(cookie, None, installed, origin)
        def installed(manager, result, expected_origin):
            self.auth_pending = False
            try:
                manager.add_cookie_finish(result)
            except GLib.Error:
                failed()
                return
            if self.origin == expected_origin:
                self.last_auth_success = time.monotonic()
                self.transport_failed = False
                self.pending = False
                self.web.load_uri(self.origin + self.pending_path)
        threading.Thread(target=work, daemon=True).start()
        return True

    def refresh(self):
        self.publish()
        if (not self.connected or time.monotonic() - self.last_auth_success > 12 * 3600):
            if self.authenticate_device():
                return GLib.SOURCE_CONTINUE
        if self.transport_failed and not self.web.is_loading():
            self.transport_failed = False
            self.web.load_uri(self.origin + self.pending_path)
            return GLib.SOURCE_CONTINUE
        if self.pending:
            return GLib.SOURCE_CONTINUE
        current = self.web.get_uri() or ""
        url = urlsplit(current)
        if urlsplit(self.origin).netloc != url.netloc or urlsplit(self.origin).scheme != url.scheme:
            return GLib.SOURCE_CONTINUE
        self.pending = True
        include_trends = time.monotonic() - self.last_trend_attempt >= 300
        if include_trends:
            self.last_trend_attempt = time.monotonic()
        script = """(async () => {
          try {
            const response = await fetch('/api/menu-summary', {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(8000)});
            if (!response.ok) throw new Error('Collector response');
            const text = await response.text();
            if (text.length > 4194304) throw new Error('Oversized summary');
            const data=JSON.parse(text);
            window.webkit.messageHandlers.energy.postMessage(JSON.stringify({ok:true,data}));
            if (INCLUDE_TEMPERATURE_TRENDS) {
              try {
                const r=await fetch('/api/house-control/temperatures?window=24h',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});
                if(!r.ok)throw new Error('History unavailable');
                const history=await r.text();if(history.length>4194304)throw new Error('Oversized');
                window.webkit.messageHandlers.trend.postMessage(JSON.stringify({origin:location.origin,source_id:data.collector_source_id,data:JSON.parse(history)}));
              } catch (_) { /* Retain the previous recorded trend on transport failure. */ }
            }
          } catch (_) { window.webkit.messageHandlers.energy.postMessage(JSON.stringify({ok:false})); }
        })(); null;"""
        script = script.replace("INCLUDE_TEMPERATURE_TRENDS", "true" if include_trends else "false")
        self.web.evaluate_javascript(script, -1, None, self.origin, None, self.script_done, None)
        return GLib.SOURCE_CONTINUE

    def script_done(self, view, result, _):
        try:
            view.evaluate_javascript_finish(result)
        except GLib.Error:
            self.pending = False
            self.connected = False
            self.publish()

    def receive_summary(self, manager, value):
        self.pending = False
        current = urlsplit(self.web.get_uri() or "")
        trusted = urlsplit(self.origin)
        if (current.scheme, current.netloc) != (trusted.scheme, trusted.netloc):
            return
        try:
            raw = value.to_string()
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("Oversized summary")
            message = json.loads(raw)
            if not message.get("ok"):
                raise ValueError("Collector response unavailable")
            summary = validate_summary(message["data"])
            previous_identity = self.identity()
            self.record = {"origin": self.origin, "fetched_at": time.time(), "data": summary}
            private_json(self.cache_file(), self.record)
            if previous_identity != self.identity():
                self.temperature_callbacks.clear()
                if self.temperature_window:
                    self.temperature_window.reset_collector(self.identity()[0])
                for channel, widgets in self.history_windows.items():
                    widgets[1].update(record=None, cached=True)
                    self.render_history(channel)
            self.connected = True
            self.status.set_label("Connected · " + self.origin + " · " + (summary.get("last_reading") or "No recorded reading"))
        except (ValueError, KeyError, TypeError):
            self.connected = False
            self.status.set_label("Collector unavailable or sign-in required. Recorded cache is retained.")
        self.publish()

    def receive_trend(self, manager, value):
        current, trusted = urlsplit(self.web.get_uri() or ''), urlsplit(self.origin)
        if (current.scheme, current.netloc) != (trusted.scheme, trusted.netloc):
            return
        try:
            raw = value.to_string()
            if len(raw) > 4 * 1024 * 1024:
                return
            message = json.loads(raw)
            if message.get('origin') != self.origin or message.get('source_id') != self.identity()[0]:
                return
            payload = validate_temperature_history(message['data'])
            if payload['window'] != '24h':
                return
            self.trend_record = {'origin': self.origin, 'source_id': self.identity()[0],
                                 'fetched_at': time.time(), 'data': payload}
            private_json(STATE / 'responses' / cache_key(self.origin, TEMPERATURE_TREND_CACHE_KEY), self.trend_record)
            self.publish()
        except (ValueError, OSError, KeyError, TypeError):
            return

    def open_path(self, path):
        self.pending_path = path
        self.web.load_uri(self.origin + path)
        self.window.present()

    def open_temperatures(self, source=None, sensor_id=None):
        from temperatures import TemperatureWindow
        if not self.temperature_window:
            self.temperature_window = TemperatureWindow(self)
            self.temperature_window.connect('close-request', self.close_temperatures)
        self.temperature_window.choose(source, sensor_id)
        self.temperature_window.update_current(display_state(self.record, self.origin, self.connected).get('temperature_sensors', []))
        self.temperature_window.present()
        self.temperature_window.fetch()

    def close_temperatures(self, *_):
        self.temperature_window = None
        return False

    def fetch_temperature_history(self, endpoint, callback):
        current, trusted = urlsplit(self.web.get_uri() or ''), urlsplit(self.origin)
        if (current.scheme, current.netloc) != (trusted.scheme, trusted.netloc):
            callback(None)
            return
        self.temperature_request += 1
        ticket = self.temperature_request
        self.temperature_callbacks[ticket] = (self.origin, self.identity()[0], callback)
        # One temperature window; discard superseded requests and their callbacks.
        for old in list(self.temperature_callbacks):
            if old != ticket:
                self.temperature_callbacks.pop(old)
        script = """(async () => {
          try {
            const r=await fetch(%s,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});
            if(!r.ok)throw new Error('Unavailable');
            const text=await r.text();if(text.length>4194304)throw new Error('Oversized');
            window.webkit.messageHandlers.temperatures.postMessage(JSON.stringify({ticket:%s,data:JSON.parse(text)}));
          } catch(_){window.webkit.messageHandlers.temperatures.postMessage(JSON.stringify({ticket:%s,error:true}));}
        })(); null;""" % (json.dumps(endpoint), ticket, ticket)
        self.web.evaluate_javascript(script, -1, None, self.origin, None, None, None)

    def receive_temperatures(self, manager, value):
        current, trusted = urlsplit(self.web.get_uri() or ''), urlsplit(self.origin)
        if (current.scheme, current.netloc) != (trusted.scheme, trusted.netloc):
            return
        try:
            raw = value.to_string()
            if len(raw) > 4 * 1024 * 1024:
                return
            message = json.loads(raw)
            saved = self.temperature_callbacks.pop(message['ticket'], None)
            if saved and saved[0] == self.origin and saved[1] == self.identity()[0]:
                saved[2](None if message.get('error') else message.get('data'))
        except (ValueError, KeyError, TypeError):
            return

    def identity(self):
        summary = self.record["data"] if self.record else {}
        return (summary.get("collector_source_id"), summary.get("active_device_gid"))

    def history_file(self, channel):
        identity = self.record["data"] if self.record else {}
        endpoint = "/api/circuit-history/" + quote(channel, safe="") + "?source=" + str(identity.get("collector_source_id")) + "&device=" + str(identity.get("active_device_gid"))
        return STATE / "responses" / cache_key(self.origin, endpoint)

    def open_history(self, channel):
        if channel in self.history_windows:
            self.history_windows[channel][0].present()
            return
        window = Gtk.ApplicationWindow(application=self, title=channel + " · Recorded Usage",
                                       default_width=680, default_height=500)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        window.add_css_class("energy-monitor")
        header = Gtk.HeaderBar()
        window.set_titlebar(header)
        title = Gtk.Label(label=channel, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("energy-history-title")
        status = Gtk.Label(label="Loading recorded history…", xalign=0, wrap=True)
        period = Gtk.DropDown.new_from_strings(["1 day", "7 days", "30 days"])
        period.set_valign(Gtk.Align.CENTER)
        header.pack_start(period)
        totals = Gtk.Label(xalign=0, wrap=True)
        chart = Chart(fmt=lambda v: "%.2f kWh" % v, zero_based=True, content_width=620, content_height=240)
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        refresh = Gtk.Button(label="Refresh")
        full = Gtk.Button(label="Open Full Circuit Page")
        full.add_css_class("suggested-action")
        full.connect("clicked", lambda *_: self.open_path("/circuit/" + quote(channel, safe="")))
        refresh.connect("clicked", lambda *_: self.fetch_history(channel))
        buttons.append(refresh)
        buttons.append(full)
        for child in (title, status, totals, chart, buttons):
            box.append(child)
        window.set_child(box)
        data = {"record": None, "cached": True}
        box.add_css_class("energy-monitor")
        status.add_css_class("dim-label")
        totals.add_css_class("energy-card")
        totals.add_css_class("numeric")
        chart.add_css_class("energy-card")
        chart.add_css_class("energy-chart")
        self.history_windows[channel] = (window, data, status, period, totals, chart)
        window.connect("close-request", lambda *_: self.history_windows.pop(channel, None) and False)
        period.connect("notify::selected", lambda *_: self.render_history(channel))
        try:
            data["record"] = read_json(self.history_file(channel))
        except (OSError, ValueError):
            pass
        self.render_history(channel)
        window.present()
        self.fetch_history(channel)

    def fetch_history(self, channel):
        current = urlsplit(self.web.get_uri() or "")
        trusted = urlsplit(self.origin)
        if (current.scheme, current.netloc) != (trusted.scheme, trusted.netloc):
            return
        endpoint = "/api/circuit-history/" + quote(channel, safe="")
        script = """(async () => {
          try {
            const r = await fetch(%s, {credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});
            if (!r.ok) throw new Error('Unavailable');
            const text = await r.text();
            if (text.length > 4194304) throw new Error('Oversized history');
            window.webkit.messageHandlers.history.postMessage(JSON.stringify({channel:%s,data:JSON.parse(text)}));
          } catch (_) { window.webkit.messageHandlers.history.postMessage(JSON.stringify({channel:%s,error:true})); }
        })(); null;""" % (json.dumps(endpoint), json.dumps(channel), json.dumps(channel))
        self.web.evaluate_javascript(script, -1, None, self.origin, None, None, None)

    def receive_history(self, manager, value):
        current = urlsplit(self.web.get_uri() or "")
        trusted = urlsplit(self.origin)
        if (current.scheme, current.netloc) != (trusted.scheme, trusted.netloc):
            return
        try:
            raw = value.to_string()
            if len(raw) > 4 * 1024 * 1024:
                return
            message = json.loads(raw)
            channel = message["channel"]
            if channel not in self.history_windows:
                return
            data = self.history_windows[channel][1]
            if not message.get("error") and isinstance(message.get("data", {}).get("windows"), list):
                record = {"origin": self.origin, "fetched_at": time.time(), "data": message["data"]}
                private_json(self.history_file(channel), record)
                data.update(record=record, cached=False)
            else:
                data["cached"] = True
            self.render_history(channel)
        except (ValueError, KeyError, TypeError):
            return

    def render_history(self, channel):
        window, state, status, period, totals, chart = self.history_windows[channel]
        record = state.get("record")
        if not record or record.get("origin") != self.origin:
            status.set_label("No recorded history cached for this circuit. Connect to the collector and refresh.")
            totals.set_label("Missing history is not zero usage.")
            chart.set_series([])
            return
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(record["fetched_at"]))
        status.set_label(("Cached history · " if state["cached"] else "Downloaded history · ") + date)
        days = [1, 7, 30][period.get_selected()]
        selected = next((item for item in record["data"]["windows"] if item.get("days") == days), {})
        def number(key, template):
            return template % selected[key] if selected.get(key) is not None else "—"
        parts = [number("total_kwh", "%.2f kWh"), number("total_cents", "%.0f¢")]
        change = selected.get("change_pct")
        if change is None:
            parts.append("no trend yet")
        else:
            arrow = "↑" if change > 0 else ("↓" if change < 0 else "→")
            parts.append("%s %+.1f%% vs previous period" % (arrow, change))
        readings = selected.get("readings")
        if readings is not None:
            parts.append("%d readings · gaps are unrecorded periods" % readings)
        series = selected.get("series", [])
        stats = series_stats([item.get("total_kwh") for item in series])
        stamp = lambda index: str(series[index].get("period", "")) if index < len(series) else ""
        if stats:
            parts_two = ["Peak %.2f kWh (%s)" % (stats["peak"], stamp(stats["peak_index"])),
                         "Lowest %.2f (%s)" % (stats["low"], stamp(stats["low_index"])),
                         "Average %.2f" % stats["mean"],
                         "%d of %d buckets recorded" % (stats["recorded"], stats["buckets"])]
            totals.set_label(" · ".join(parts) + "\n" + " · ".join(parts_two))
        else:
            totals.set_label(" · ".join(parts))
        chart.set_series([item.get("total_kwh") if isinstance(item.get("total_kwh"), (int, float))
                          and item.get("total_kwh") >= 0 else None for item in series],
                         [str(item.get("period", "")) for item in series])

    def connection_dialog(self):
        window = Gtk.Window(title="Collector Connection", transient_for=self.window, modal=True,
                            default_width=480, resizable=False)
        window.add_css_class("energy-monitor")
        header = Gtk.HeaderBar(show_title_buttons=False)
        window.set_titlebar(header)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: window.destroy())
        header.pack_start(cancel)
        save = Gtk.Button(label="Connect")
        save.add_css_class("suggested-action")
        header.pack_end(save)
        window.set_default_widget(save)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        heading = Gtk.Label(label="Collector address", xalign=0)
        heading.add_css_class("energy-heading")
        box.append(heading)
        entry = Gtk.Entry(text=self.origin, placeholder_text=DEFAULT_ORIGIN, activates_default=True)
        box.append(entry)
        help_text = Gtk.Label(label="Connects directly over HTTPS. Sign in when prompted to access your collector.",
                              wrap=True, xalign=0)
        help_text.add_css_class("dim-label")
        help_text.add_css_class("caption")
        box.append(help_text)
        error = Gtk.Label(wrap=True, xalign=0)
        error.add_css_class("error")
        box.append(error)
        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                             Gtk.CallbackAction.new(lambda *_: window.destroy() or True)))
        window.add_controller(escape)
        def connect(*_):
            try:
                origin = validate_origin(entry.get_text())
                private_json(CONFIG, {"origin": origin})
            except (ValueError, OSError) as exc:
                error.set_label(str(exc))
                return
            for widgets in list(self.history_windows.values()):
                widgets[0].destroy()
            self.history_windows.clear()
            if self.temperature_window:
                self.temperature_window.destroy()
                self.temperature_window = None
            self.temperature_callbacks.clear()
            self.origin = origin
            self.connected = False
            self.pending = False
            self.restore_cache()
            self.web.load_uri(self.origin)
            window.destroy()
            self.window.present()
        save.connect("clicked", connect)
        window.set_child(box)
        window.present()
        entry.grab_focus()


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(EnergyMonitor().run(sys.argv))
