"""Native recorded-temperature browser; all observations come from the collector."""
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import urlencode

from gi.repository import Gtk, Pango

from chart import Chart

from client_core import (temperature_history_cache_key, load_temperature_window_cache,
                         private_json, temperature_band,
                         validate_temperature_history)


class TemperatureWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Temperatures · Recorded History',
                         default_width=700, default_height=740)
        self.app = app
        self.payload = None
        self.end = None
        self.requested = None
        self.ids = []
        self.pending = False
        self.last_fetch = 0
        self.cached = False
        self.closed = False
        self.collector_source = app.identity()[0]
        self.generation = 0
        self.payload_endpoint = None
        self.connect('close-request', self.close)
        self.add_css_class('energy-monitor')
        self.set_titlebar(Gtk.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(box)
        heading = Gtk.Label(label='Current recorded temperatures', xalign=0)
        heading.add_css_class('energy-heading')
        box.append(heading)
        self.current = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.current.add_css_class('boxed-list')
        self.current.connect('row-activated', lambda _, row: self.choose(*row.sensor_key))
        scroll = Gtk.ScrolledWindow(child=self.current, max_content_height=190,
                                    propagate_natural_height=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER)
        box.append(scroll)
        toolbar = Gtk.Box(spacing=8)
        self.sensor = Gtk.DropDown.new_from_strings(['Loading sensors…'])
        self.sensor.set_hexpand(True)
        self.period = Gtk.DropDown.new_from_strings(['4 hours', '24 hours', '7 days', '30 days'])
        self.period.set_selected(1)
        toolbar.append(self.sensor)
        toolbar.append(self.period)
        box.append(toolbar)
        self.status = Gtk.Label(label='Loading recorded history…', xalign=0, wrap=True)
        self.status.add_css_class('dim-label')
        box.append(self.status)
        self.chart = Chart(fmt=lambda v: f'{v:.0f}°F', zero_based=False)
        self.chart.on_pick = lambda index: self.cursor.set_value(index)
        self.chart_key = None
        controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        controller.connect('scroll', self.scroll_history)
        self.chart.add_controller(controller)
        box.append(self.chart)
        look_back = Gtk.Label(label='Look back · scroll the chart or move the time slider', xalign=0)
        look_back.add_css_class('dim-label')
        look_back.add_css_class('caption')
        box.append(look_back)
        self.cursor = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.cursor.set_draw_value(False)
        self.cursor.set_sensitive(False)
        self.cursor.connect('value-changed', lambda *_: self.render())
        box.append(self.cursor)
        self.readout = Gtk.Label(xalign=0, wrap=True)
        self.readout.add_css_class('numeric')
        box.append(self.readout)
        buttons = Gtk.Box(spacing=8)
        navigation = Gtk.Box(spacing=0)
        navigation.add_css_class('linked')
        for title, action in [('Older', lambda *_: self.shift(-1)),
                              ('Newer', lambda *_: self.shift(1)),
                              ('Now', lambda *_: self.now())]:
            button = Gtk.Button(label=title)
            button.connect('clicked', action)
            navigation.append(button)
        buttons.append(navigation)
        plan = Gtk.Button(label='Temperature Floor Plan', hexpand=True, halign=Gtk.Align.END)
        plan.connect('clicked', lambda *_: app.open_path('/home-3d?level=all&metric=temperature_f#home3d-floor-plan'))
        buttons.append(plan)
        box.append(buttons)
        note = Gtk.Label(label='Blue below 70°F · Yellow 70–75°F · Amber above 75–below 78°F · Red 78°F and up\nRecorded collector-observation means. Missing buckets stay unknown; cached snapshots may repeat measurements.', xalign=0, wrap=True)
        note.add_css_class('dim-label')
        note.add_css_class('caption')
        box.append(note)
        self.sensor.connect('notify::selected', lambda *_: self.render())
        self.period.connect('notify::selected', lambda *_: self.fetch())

    def close(self, *_):
        self.closed = True
        return False

    def choose(self, source, identity):
        if source and identity:
            self.requested = (source, identity)
            if self.requested in self.ids:
                self.sensor.set_selected(self.ids.index(self.requested))

    def reset_collector(self, source_id):
        self.collector_source = source_id
        self.generation += 1
        self.payload = None
        self.payload_endpoint = None
        self.pending = False
        self.ids = []
        self.requested = None
        self.end = None
        self.last_fetch = 0
        self.sensor.set_model(Gtk.StringList.new(['Loading sensors…']))
        self.readout.set_label('')
        self.chart.set_series([])
        self.chart_key = None
        self.status.set_label('Collector changed · loading its recorded history…')

    def update_current(self, sensors):
        if self.collector_source != self.app.identity()[0]:
            self.reset_collector(self.app.identity()[0])
        while self.current.get_first_child():
            self.current.remove(self.current.get_first_child())
        for sensor in sensors:
            value = sensor.get('metrics', {}).get('temperature_f')
            stale = bool(sensor.get('stale'))
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=8, margin_end=10)
            band = None if stale else temperature_band(value)
            swatch = Gtk.Box(width_request=4)
            swatch.add_css_class('energy-swatch')
            swatch.add_css_class('temperature-swatch-' + (band or 'none'))
            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            title = Gtk.Label(label=sensor['name'], xalign=0, ellipsize=Pango.EllipsizeMode.END)
            where = Gtk.Label(label=(sensor.get('room_name') or 'Unplaced') + (' · last recorded' if stale else ''),
                              xalign=0, ellipsize=Pango.EllipsizeMode.END)
            where.add_css_class('dim-label')
            where.add_css_class('caption')
            names.append(title)
            names.append(where)
            reading = Gtk.Label(label=f'{value:.1f} °F' if isinstance(value, (int, float)) else 'Unknown')
            reading.add_css_class('energy-value')
            if stale:
                reading.add_css_class('temperature-stale-value')
            for child in (swatch, names, reading):
                box.append(child)
            row.set_child(box)
            row.sensor_key = (sensor['source'], sensor['sensor_id'])
            self.current.append(row)
        if not self.payload and not self.pending and time.monotonic() - self.last_fetch > 10:
            self.fetch()

    def fetch(self):
        if self.closed:
            return
        window = ['4h', '24h', '7d', '30d'][self.period.get_selected()]
        args = {'window': window}
        if self.end:
            args['end'] = self.end
        endpoint = '/api/house-control/temperatures?' + urlencode(args)
        source_id = self.app.identity()[0]
        if not source_id:
            self.status.set_label('Connect to identify the collector before loading history.')
            return
        if self.collector_source != source_id:
            self.reset_collector(source_id)
        self.generation += 1
        generation = self.generation
        cache = self.app.cache_file().parent / temperature_history_cache_key(self.app.origin, source_id, endpoint)
        record = load_temperature_window_cache(cache.parent, self.app.origin, source_id, endpoint)
        if record:
            self.payload_endpoint = endpoint
            self.install(record['data'], cached=True)
        elif self.payload and getattr(self, 'payload_endpoint', None) != endpoint:
            self.payload = None
            self.readout.set_label('No cached history for the selected range.')
            self.chart.set_series([])
            self.chart_key = None
        self.pending = True
        self.last_fetch = time.monotonic()
        self.status.set_label('Loading recorded temperature history…')
        origin = self.app.origin

        def received(payload):
            if (self.closed or origin != self.app.origin or source_id != self.app.identity()[0]
                    or generation != self.generation):
                return
            self.pending = False
            try:
                payload = validate_temperature_history(payload)
                if payload.get('collector_source_id') != source_id:
                    raise ValueError('Temperature history belongs to a different collector')
                private_json(cache, {'origin': origin, 'source_id': source_id, 'fetched_at': time.time(), 'data': payload})
            except (ValueError, OSError, KeyError, TypeError):
                self.cached = True
                self.status.set_label('Collector unavailable · retained history is cached. Connect and refresh with Now.')
                self.render()
                return
            self.payload_endpoint = endpoint
            self.install(payload, cached=False)
        self.app.fetch_temperature_history(endpoint, received)

    def install(self, payload, cached=False):
        previous = self.requested
        if self.ids and self.sensor.get_selected() < len(self.ids):
            previous = self.ids[self.sensor.get_selected()]
        self.payload = payload
        self.cached = cached
        self.ids = [(s['source'], s['sensor_id']) for s in payload['series']]
        self.sensor.set_model(Gtk.StringList.new([s['name'] for s in payload['series']]))
        self.sensor.set_selected(self.ids.index(previous) if previous in self.ids else 0)
        self.cursor.set_range(0, max(1, len(payload['times']) - 1))
        self.cursor.set_value(max(0, len(payload['times']) - 1))
        self.cursor.set_sensitive(bool(payload['times']))
        self.status.set_label(('Cached history · ' if cached else 'Recorded history · ') +
                              f"{datetime.fromisoformat(payload['start']).astimezone():%b %d %H:%M} – {datetime.fromisoformat(payload['end']).astimezone():%b %d %H:%M %Z}")
        self.render()

    def render(self):
        if not self.payload or self.sensor.get_selected() >= len(self.payload['series']):
            return
        series = self.payload['series'][self.sensor.get_selected()]
        index = min(int(self.cursor.get_value()), len(self.payload['times']) - 1)
        value = series['values'][index]
        stamp = datetime.fromisoformat(self.payload['times'][index]).astimezone()
        text = f'{value:.1f} °F' if value is not None else 'Missing · no recorded observation'
        self.readout.set_label(f"{series['name']} · {stamp:%b %d, %Y %H:%M %Z}\n{text} · {self.payload['seconds'] // 60}-minute observation mean" + (' · cached' if self.cached else ''))
        key = (id(self.payload), self.sensor.get_selected())
        if key != self.chart_key:
            self.chart_key = key
            self.chart.set_series(series['values'], [
                datetime.fromisoformat(t).astimezone().strftime('%b %d %H:%M') for t in self.payload['times']])
        self.chart.set_selected(index)

    def scroll_history(self, _, dx, dy):
        self.cursor.set_value(self.cursor.get_value() + (-1 if dy > 0 else 1))
        return True

    def shift(self, direction):
        if not self.payload:
            return
        start = datetime.fromisoformat(self.payload['start'])
        end = datetime.fromisoformat(self.payload['end'])
        following = end + direction * (end - start)
        self.end = None if following >= datetime.now(timezone.utc) else following.isoformat()
        self.fetch()

    def now(self):
        self.end = None
        self.fetch()
