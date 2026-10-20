"""Shared line chart for the native windows: gridlines, hover read-out, selection, gaps preserved.

Missing buckets are unknown, never zero and never interpolated: each recorded run is drawn on its own.
All geometry and colour comes from the widget so it follows the system theme and accent.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from client_core import index_at, nice_ticks, value_runs  # noqa: E402
from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

PAD_TOP, PAD_RIGHT, PAD_BOTTOM, PAD_X_AXIS = 12, 12, 10, 18
TAU = 6.283185307


class Chart(Gtk.DrawingArea):
    def __init__(self, fmt=lambda v: f"{v:g}", zero_based=False, **kwargs):
        kwargs.setdefault("content_width", 600)
        kwargs.setdefault("content_height", 220)
        kwargs.setdefault("hexpand", True)
        kwargs.setdefault("vexpand", True)
        super().__init__(**kwargs)
        self.fmt, self.zero_based = fmt, zero_based
        self.values, self.stamps = [], []
        self.hover = None          # index under the pointer
        self.selected = None       # index chosen by click or an external control
        self.on_pick = None        # callback(index) when the pointer moves over or clicks a point
        self.left = 48
        self.add_css_class("energy-card")
        self.add_css_class("energy-chart")
        self.set_focusable(True)
        self.set_draw_func(self.draw)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        motion.connect("leave", self._leave)
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda g, n, x, y: self._pick(x, True))
        self.add_controller(click)

    def set_series(self, values, stamps=None):
        self.values = list(values)
        self.stamps = list(stamps) if stamps is not None else [""] * len(self.values)
        self.hover = None
        recorded = [v for v in self.values if isinstance(v, (int, float))]
        extremes = f"; lowest {self.fmt(min(recorded))}, highest {self.fmt(max(recorded))}" if recorded else ""
        summary = f"Chart of {len(recorded)} recorded points{extremes}"
        self.set_tooltip_text(summary)
        try:
            self.update_property([Gtk.AccessibleProperty.LABEL], [summary])
        except (TypeError, AttributeError):
            pass
        self.queue_draw()

    def set_selected(self, index):
        self.selected = index
        self.queue_draw()

    # -- geometry ----------------------------------------------------------------------------
    def _plot(self, width, height):
        return self.left, PAD_TOP, width - self.left - PAD_RIGHT, height - PAD_TOP - PAD_BOTTOM - PAD_X_AXIS

    def _motion(self, controller, x, y):
        self._pick(x, False)

    def _leave(self, controller):
        self.hover = None
        self.queue_draw()

    def _pick(self, x, clicked):
        left, _, plot_w, _ = self._plot(self.get_width(), self.get_height())
        index = index_at(x, left, plot_w, len(self.values))
        if index is None:
            return
        self.hover = index
        if clicked:
            self.selected = index
        if self.on_pick:
            self.on_pick(index)
        self.queue_draw()

    # -- drawing -----------------------------------------------------------------------------
    def draw(self, area, cr, width, height):
        recorded = [v for v in self.values if isinstance(v, (int, float))]
        fg = self.get_color()
        accent = self.accent()
        layout = self.create_pango_layout("")
        layout.set_font_description(Pango.FontDescription.from_string("Sans 9"))
        if not recorded:
            layout.set_text("No recorded values in this period", -1)
            _, extent = layout.get_pixel_extents()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.65)
            cr.move_to((width - extent.width) / 2, (height - extent.height) / 2)
            PangoCairo.show_layout(cr, layout)
            return
        low, high = (0.0, max(max(recorded), 0.001)) if self.zero_based else (min(recorded), max(recorded))
        ticks, low, high = nice_ticks(low, high, 4)
        # Gutter wide enough for the widest tick label.
        widest = 0
        for tick in ticks:
            layout.set_text(self.fmt(tick), -1)
            widest = max(widest, layout.get_pixel_extents()[1].width)
        self.left = widest + 14
        left, top, plot_w, plot_h = self._plot(width, height)
        if plot_w <= 0 or plot_h <= 0:
            return

        def y_of(value):
            return top + plot_h - (value - low) * plot_h / (high - low)

        def x_of(index):
            return left + index * plot_w / max(1, len(self.values) - 1)

        cr.set_line_width(1)
        for tick in ticks:
            y = round(y_of(tick)) + 0.5
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.14 if tick != ticks[0] else 0.3)
            cr.move_to(left, y)
            cr.line_to(left + plot_w, y)
            cr.stroke()
            layout.set_text(self.fmt(tick), -1)
            _, extent = layout.get_pixel_extents()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
            cr.move_to(left - extent.width - 8, y - extent.height / 2)
            PangoCairo.show_layout(cr, layout)
        # First and last time labels under the plot.
        if self.stamps and any(self.stamps):
            for text, anchor in ((self.stamps[0], "start"), (self.stamps[-1], "end")):
                layout.set_text(text, -1)
                _, extent = layout.get_pixel_extents()
                px = left if anchor == "start" else left + plot_w - extent.width
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
                cr.move_to(px, top + plot_h + 4)
                PangoCairo.show_layout(cr, layout)
        cr.set_line_join(1)
        for run in value_runs(self.values):
            points = [(x_of(i), y_of(v)) for i, v in run]
            if len(points) > 1:
                cr.move_to(points[0][0], top + plot_h)
                for px, py in points:
                    cr.line_to(px, py)
                cr.line_to(points[-1][0], top + plot_h)
                cr.close_path()
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.14)
                cr.fill()
                cr.set_line_width(2)
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.95)
                cr.move_to(*points[0])
                for px, py in points[1:]:
                    cr.line_to(px, py)
                cr.stroke()
            else:
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.95)
                cr.arc(points[0][0], points[0][1], 2.5, 0, TAU)
                cr.fill()
        # Selection line and hover read-out (value and time), kept inside the plot.
        for index, strong in ((self.selected, False), (self.hover, True)):
            if index is None or index >= len(self.values):
                continue
            px = round(x_of(index)) + 0.5
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35 if strong else 0.5)
            cr.set_line_width(1)
            cr.move_to(px, top)
            cr.line_to(px, top + plot_h)
            cr.stroke()
            value = self.values[index]
            if isinstance(value, (int, float)):
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
                cr.arc(px, y_of(value), 4, 0, TAU)
                cr.fill()
        shown = self.hover if self.hover is not None else self.selected
        if shown is not None and shown < len(self.values):
            value = self.values[shown]
            label = (self.fmt(value) if isinstance(value, (int, float)) else "No recording") + \
                ("  ·  " + self.stamps[shown] if shown < len(self.stamps) and self.stamps[shown] else "")
            layout.set_text(label, -1)
            _, extent = layout.get_pixel_extents()
            bx = min(max(x_of(shown) - extent.width / 2 - 6, left), left + plot_w - extent.width - 12)
            by = top + 2
            bg = self.background()
            cr.set_source_rgba(bg[0], bg[1], bg[2], 0.94)
            self._round_rect(cr, bx, by, extent.width + 12, extent.height + 8, 6)
            cr.fill_preserve()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.25)
            cr.set_line_width(1)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 1)
            cr.move_to(bx + 6, by + 4)
            PangoCairo.show_layout(cr, layout)

    @staticmethod
    def _round_rect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -TAU / 4, 0)
        cr.arc(x + w - r, y + h - r, r, 0, TAU / 4)
        cr.arc(x + r, y + h - r, r, TAU / 4, TAU / 2)
        cr.arc(x + r, y + r, r, TAU / 2, 3 * TAU / 4)
        cr.close_path()

    def accent(self):
        found, colour = self.get_style_context().lookup_color("accent_color")
        return colour if found else self.get_color()

    def background(self):
        """Opposite of the foreground, so the read-out box is light on light themes and dark on dark ones."""
        fg = self.get_color()
        light_text = (fg.red * 0.299 + fg.green * 0.587 + fg.blue * 0.114) > 0.5
        return (0.17, 0.17, 0.17) if light_text else (1.0, 1.0, 1.0)
