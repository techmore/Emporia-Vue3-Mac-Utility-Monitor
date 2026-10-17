#!/usr/bin/python3
"""Elementary OS desktop client for the existing, independently running collector."""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit

from client_core import (DEFAULT_ORIGIN, cache_key, display_state, private_json,
                         read_json, validate_origin, validate_summary)

STATE = Path(GLib.get_user_state_dir()) / "energy-monitor"
CONFIG = Path(GLib.get_user_config_dir()) / "energy-monitor/client.json"
DATA = Path(GLib.get_user_data_dir()) / "energy-monitor"


class EnergyMonitor(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.techmore.EnergyMonitor",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.web = None
        self.connected = False
        self.record = None
        self.pending = False
        self.transport_failed = False
        self.history_windows = {}
        self.origin = DEFAULT_ORIGIN
        try:
            self.origin = validate_origin((read_json(CONFIG) or {}).get("origin", DEFAULT_ORIGIN))
        except (ValueError, OSError):
            pass

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        settings = Gtk.Settings.get_default()
        settings.connect("notify::gtk-application-prefer-dark-theme", lambda *_: self.load_theme())
        settings.connect("notify::gtk-theme-name", lambda *_: self.load_theme())
        self.load_theme()
        self.hold()
        for name, callback in (("dashboard", lambda *_: self.open_path("/")),
                               ("settings", lambda *_: self.open_path("/settings")),
                               ("connection", lambda *_: self.connection_dialog()),
                               ("refresh", lambda *_: self.refresh()),
                               ("quit", lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.build_window()
        self.restore_cache()
        self.web.load_uri(self.origin)
        GLib.timeout_add_seconds(30, self.refresh)

    def load_theme(self):
        settings = Gtk.Settings.get_default()
        dark = settings.get_property("gtk-application-prefer-dark-theme") or "dark" in settings.get_property("gtk-theme-name").lower()
        self.css.load_from_path(str(Path(__file__).with_name("theme-dark.css" if dark else "theme-light.css")))

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
                                            default_width=1120, default_height=780)
        self.window.connect("close-request", self.hide_window)
        header = Gtk.HeaderBar()
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh")
        refresh.connect("clicked", lambda *_: self.web.reload())
        header.pack_start(refresh)
        menu = Gio.Menu()
        for label, action in (("Dashboard", "dashboard"), ("Collector Settings", "settings"),
                              ("Collector Connection…", "connection"), ("Quit", "quit")):
            menu.append(label, "app." + action)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        self.window.set_titlebar(header)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.status = Gtk.Label(label="Connecting to the energy collector…", xalign=0,
                                margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.status.add_css_class("dim-label")
        content.append(self.status)
        DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
        session = WebKit.NetworkSession.new(str(DATA / "web-data"), str(DATA / "web-cache"))
        cookies = session.get_cookie_manager()
        cookies.set_persistent_storage(str(DATA / "cookies.sqlite"), WebKit.CookiePersistentStorage.SQLITE)
        manager = WebKit.UserContentManager()
        manager.register_script_message_handler("energy", None)
        manager.connect("script-message-received::energy", self.receive_summary)
        manager.register_script_message_handler("history", None)
        manager.connect("script-message-received::history", self.receive_history)
        self.web = WebKit.WebView(network_session=session, user_content_manager=manager,
                                  vexpand=True, hexpand=True)
        self.web.connect("load-changed", self.loaded)
        self.web.connect("load-failed", self.failed)
        self.web.connect("load-failed-with-tls-errors", self.tls_failed)
        self.web.connect("create", self.popup)
        session.connect("download-started", self.download)
        content.append(self.web)
        self.window.set_child(content)

    def hide_window(self, *_):
        self.window.set_visible(False)
        return True

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
        self.publish()

    def publish(self):
        state = display_state(self.record, self.origin, self.connected)
        private_json(STATE / "summary.json", state)

    def loaded(self, view, event):
        if event == WebKit.LoadEvent.FINISHED:
            self.refresh()

    def failed(self, view, event, uri, error):
        self.transport_failed = True
        self.pending = False
        self.connected = False
        self.status.set_label("Collector unavailable. Open Collector Connection to select the private tunnel.")
        self.publish()
        return False

    def tls_failed(self, view, uri, certificate, errors):
        self.failed(view, None, uri, None)
        self.status.set_label("The collector's TLS connection failed. Its certificate was not bypassed.")
        return False

    def refresh(self):
        self.publish()
        if self.transport_failed and not self.web.is_loading():
            self.transport_failed = False
            self.web.load_uri(self.origin)
            return GLib.SOURCE_CONTINUE
        if self.pending:
            return GLib.SOURCE_CONTINUE
        current = self.web.get_uri() or ""
        url = urlsplit(current)
        if urlsplit(self.origin).netloc != url.netloc or urlsplit(self.origin).scheme != url.scheme:
            return GLib.SOURCE_CONTINUE
        self.pending = True
        script = """(async () => {
          try {
            const response = await fetch('/api/menu-summary', {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(8000)});
            if (!response.ok) throw new Error('Collector response');
            const text = await response.text();
            if (text.length > 4194304) throw new Error('Oversized summary');
            window.webkit.messageHandlers.energy.postMessage(JSON.stringify({ok:true,data:JSON.parse(text)}));
          } catch (_) { window.webkit.messageHandlers.energy.postMessage(JSON.stringify({ok:false})); }
        })(); null;"""
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
                for channel, widgets in self.history_windows.items():
                    widgets[1].update(record=None, cached=True)
                    self.render_history(channel)
            self.connected = True
            self.status.set_label("Connected · " + self.origin + " · " + (summary.get("last_reading") or "No recorded reading"))
        except (ValueError, KeyError, TypeError):
            self.connected = False
            self.status.set_label("Collector unavailable or sign-in required. Recorded cache is retained.")
        self.publish()

    def open_path(self, path):
        self.web.load_uri(self.origin + path)
        self.window.present()

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
                                       default_width=640, default_height=470)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        window.add_css_class("energy-monitor")
        window.set_titlebar(Gtk.HeaderBar())
        title = Gtk.Label(label=channel, xalign=0, ellipsize=3)
        title.add_css_class("energy-history-title")
        status = Gtk.Label(label="Loading recorded history…", xalign=0, wrap=True)
        period = Gtk.DropDown.new_from_strings(["1 day", "7 days", "30 days"])
        totals = Gtk.Label(xalign=0, wrap=True)
        chart = Gtk.DrawingArea(content_width=580, content_height=220, hexpand=True, vexpand=True)
        buttons = Gtk.Box(spacing=6)
        refresh = Gtk.Button(label="Refresh")
        full = Gtk.Button(label="Open Full Circuit Page")
        full.connect("clicked", lambda *_: self.open_path("/circuit/" + quote(channel, safe="")))
        refresh.connect("clicked", lambda *_: self.fetch_history(channel))
        buttons.append(refresh)
        buttons.append(full)
        for child in (title, status, period, totals, chart, buttons):
            box.append(child)
        window.set_child(box)
        data = {"record": None, "cached": True}
        box.add_css_class("energy-monitor")
        status.add_css_class("energy-muted")
        totals.add_css_class("energy-value")
        totals.add_css_class("energy-card")
        chart.add_css_class("energy-card")
        self.history_windows[channel] = (window, data, status, period, totals, chart)
        window.connect("close-request", lambda *_: self.history_windows.pop(channel, None) and False)
        period.connect("notify::selected", lambda *_: self.render_history(channel))
        chart.set_draw_func(lambda area, cr, width, height: self.draw_history(channel, cr, width, height))
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
            chart.queue_draw()
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
        totals.set_label(" · ".join(parts))
        chart.queue_draw()

    def draw_history(self, channel, cr, width, height):
        if channel not in self.history_windows:
            return
        _, state, _, period, _, _ = self.history_windows[channel]
        record = state.get("record")
        if not record or record.get("origin") != self.origin:
            return
        days = [1, 7, 30][period.get_selected()]
        selected = next((item for item in record["data"]["windows"] if item.get("days") == days), {})
        series = selected.get("series", [])
        points = [item.get("total_kwh") for item in series]
        values = [value for value in points if isinstance(value, (int, float)) and value >= 0]
        if len(points) < 2 or not values:
            return
        color = self.history_windows[channel][5].get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, 0.75)
        cr.set_line_width(2)
        maximum = max(max(values), 0.001)
        connected = False
        for index, value in enumerate(points):
            if value is None:
                cr.stroke()
                connected = False
                continue
            x = 8 + index * (width - 16) / (len(points) - 1)
            y = height - 8 - value * (height - 16) / maximum
            if connected:
                cr.line_to(x, y)
            else:
                cr.move_to(x, y)
            connected = True
        cr.stroke()

    def connection_dialog(self):
        window = Gtk.Window(title="Collector Connection", transient_for=self.window, modal=True,
                            default_width=540)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        box.append(Gtk.Label(label="Use the existing collector's HTTPS origin or loopback SSH tunnel.",
                             wrap=True, xalign=0))
        entry = Gtk.Entry(text=self.origin, placeholder_text="http://127.0.0.1:15001")
        box.append(entry)
        error = Gtk.Label(wrap=True, xalign=0)
        box.append(error)
        save = Gtk.Button(label="Connect")
        save.add_css_class("suggested-action")
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
            self.origin = origin
            self.connected = False
            self.pending = False
            self.restore_cache()
            self.web.load_uri(self.origin)
            window.destroy()
            self.window.present()
        save.connect("clicked", connect)
        box.append(save)
        window.set_child(box)
        window.present()


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(EnergyMonitor().run(sys.argv))
