/* SPDX-License-Identifier: MIT */
// Native Temperatures view for the Wingpanel dropdown. Everything shown comes from the
// recorded summary.json (current sensor readings plus the 24-hour history series); the
// view has no network client of its own and never invents readings for stale sensors.

// Small per-user dropdown preferences, kept beside the client's other config.
public class EnergyPrefs : Object {
    public int view { get; set; default = 0; }
    public int sort { get; set; default = 0; }
    public bool house_open { get; set; default = true; }
    public bool sensors_open { get; set; default = true; }
    private string path;

    public EnergyPrefs () {
        path = Path.build_filename (Environment.get_user_config_dir (), "energy-monitor", "wingpanel.ini");
        var file = new KeyFile ();
        try {
            file.load_from_file (path, KeyFileFlags.NONE);
            if (file.has_key ("dropdown", "view")) view = file.get_integer ("dropdown", "view").clamp (0, 1);
            if (file.has_key ("temperatures", "sort")) sort = file.get_integer ("temperatures", "sort").clamp (0, 3);
            if (file.has_key ("temperatures", "house_open")) house_open = file.get_boolean ("temperatures", "house_open");
            if (file.has_key ("temperatures", "sensors_open")) sensors_open = file.get_boolean ("temperatures", "sensors_open");
        } catch (Error error) { /* First run or unreadable: keep defaults. */ }
    }

    public void save () {
        var file = new KeyFile ();
        file.set_integer ("dropdown", "view", view);
        file.set_integer ("temperatures", "sort", sort);
        file.set_boolean ("temperatures", "house_open", house_open);
        file.set_boolean ("temperatures", "sensors_open", sensors_open);
        try {
            DirUtils.create_with_parents (Path.get_dirname (path), 0700);
            FileUtils.set_contents (path, file.to_data ());
        } catch (Error error) { warning ("Energy dropdown preferences: %s", error.message); }
    }
}

public class TemperatureView : Gtk.Box {
    // Band edges shared with the web floor plan, Home 3D badges and the history window.
    public const double COLD = 70;
    public const double WARM = 75;
    public const double HOT = 78;
    private const double FAST_RATE = 3.0;  // °F per hour worth calling out

    private class Reading {
        public string name;
        public string room;
        public string level_id;
        public string level_name;
        public string source;
        public string identity;
        public double temp = double.NAN;
        public double humidity = double.NAN;
        public double battery = double.NAN;
        public bool stale;
        public bool measured = true;         // false: timestamp is when the snapshot was taken
        public bool placed;
        public double[] values = {};
        public double rate = double.NAN;      // °F/h over the last recorded hour or two
        public int64 last_seen;              // unix seconds of the last recorded observation
        public int64 updated;                // unix seconds of the current snapshot
        public int order;                    // floor order from the saved plan

        public bool live { get { return !stale && !temp.is_nan (); } }
    }

    public signal void sensor_selected (string source, string identity);

    private EnergyPrefs prefs;
    private Gtk.Box overview;
    private Gtk.Box attention;
    private Gtk.Box sensor_list;
    private Gtk.Label sensor_heading;
    private Gtk.DropDown sort_picker;
    private Reading[] readings = {};
    private int64 history_start;
    private double bucket_hours = 0.5;

    public TemperatureView (HousePreview house, EnergyPrefs prefs) {
        Object (orientation: Gtk.Orientation.VERTICAL, spacing: 6);
        this.prefs = prefs;
        add_css_class ("temperature-view");

        overview = new Gtk.Box (Gtk.Orientation.VERTICAL, 4);
        overview.add_css_class ("energy-card");
        overview.add_css_class ("temperature-overview");
        append (overview);

        attention = new Gtk.Box (Gtk.Orientation.VERTICAL, 2);
        attention.add_css_class ("temperature-attention");
        append (attention);

        var house_box = new Gtk.Expander ("House view") { child = house, expanded = prefs.house_open };
        house_box.add_css_class ("temperature-expander");
        house_box.notify["expanded"].connect (() => { prefs.house_open = house_box.expanded; prefs.save (); });
        append (house_box);

        var heading = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 6);
        sensor_heading = new Gtk.Label ("Sensors") { xalign = 0, hexpand = true };
        heading.append (sensor_heading);
        sort_picker = new Gtk.DropDown.from_strings ({"By floor", "Warmest first", "Coolest first", "Changing fastest"}) {
            selected = prefs.sort, tooltip_text = "Order the sensor list"
        };
        sort_picker.add_css_class ("flat");
        sort_picker.notify["selected"].connect (() => { prefs.sort = (int) sort_picker.selected; prefs.save (); rebuild_list (); });
        heading.append (sort_picker);
        sensor_list = new Gtk.Box (Gtk.Orientation.VERTICAL, 4);
        var sensors_box = new Gtk.Expander (null) { label_widget = heading, child = sensor_list, expanded = prefs.sensors_open };
        sensors_box.add_css_class ("temperature-expander");
        sensors_box.notify["expanded"].connect (() => { prefs.sensors_open = sensors_box.expanded; prefs.save (); });
        append (sensors_box);

        var legend = new Gtk.Label ("Blue <70 · Yellow 70–75 · Amber 75–78 · Red ≥78 °F · Grey = no recent reading\nSelect a sensor, room or warning for its recorded history.") {
            xalign = 0, wrap = true
        };
        legend.add_css_class ("dim-label");
        legend.add_css_class ("caption");
        append (legend);
    }

    // ---- data ---------------------------------------------------------------------------

    private static string text (Json.Object row, string key, string fallback = "") {
        if (!row.has_member (key) || row.get_member (key).is_null ()) return fallback;
        var node = row.get_member (key);
        return node.get_value_type () == typeof (string) ? node.get_string () : fallback;
    }

    private static double number (Json.Object row, string key) {
        if (!row.has_member (key) || row.get_member (key).is_null ()) return double.NAN;
        var node = row.get_member (key);
        if (node.get_node_type () != Json.NodeType.VALUE) return double.NAN;
        var type = node.get_value_type ();
        if (type == typeof (double)) return node.get_double ();
        if (type == typeof (int64)) return (double) node.get_int ();
        return double.NAN;
    }

    private static int64 parse_time (string stamp) {
        if (stamp == "") return 0;
        var parsed = new DateTime.from_iso8601 (stamp, new TimeZone.utc ());
        return parsed == null ? 0 : parsed.to_unix ();
    }

    public static string band (double value) {
        return value < COLD ? "temperature-cold" : value <= WARM ? "temperature-comfortable" : value < HOT ? "temperature-warm" : "temperature-hot";
    }

    private static void band_colour (Cairo.Context cr, double value, double alpha) {
        if (value < COLD) cr.set_source_rgba (0.212, 0.537, 0.902, alpha);
        else if (value <= WARM) cr.set_source_rgba (0.976, 0.769, 0.251, alpha);
        else if (value < HOT) cr.set_source_rgba (1.0, 0.631, 0.329, alpha);
        else cr.set_source_rgba (0.929, 0.325, 0.325, alpha);
    }

    public static string ago (int64 seconds) {
        if (seconds < 90) return "just now";
        if (seconds < 3600) return "%d min ago".printf ((int) (seconds / 60));
        if (seconds < 48 * 3600) return "%d h ago".printf ((int) (seconds / 3600));
        return "%d days ago".printf ((int) (seconds / 86400));
    }

    public static string rate_text (double rate) {
        if (rate.is_nan ()) return "";
        if (rate.abs () < 0.3) return "→ steady";
        return "%s %.1f °F/h".printf (rate > 0 ? "↑" : "↓", rate.abs ());
    }

    // Last recorded hour or two of the 24-hour series; null buckets are offline gaps.
    public static double trend (double[] values, double bucket_hours) {
        int last = values.length - 1;
        while (last >= 0 && values[last].is_nan ()) last--;
        if (last < 1 || bucket_hours <= 0) return double.NAN;
        int span = int.max (1, (int) Math.round (1.0 / bucket_hours));
        for (int back = span; back <= span * 2 && last - back >= 0; back++) {
            if (!values[last - back].is_nan ()) return (values[last] - values[last - back]) / (back * bucket_hours);
        }
        return double.NAN;
    }

    public void set_state (Json.Object root, bool fresh) {
        var level_names = new HashTable<string, string> (str_hash, str_equal);
        var level_order = new HashTable<string, int> (str_hash, str_equal);
        string building = "";
        if (root.has_member ("house_preview") && !root.get_member ("house_preview").is_null ()) {
            var plan = root.get_object_member ("house_preview");
            building = text (plan, "name").split (" · ")[0];
            if (plan.has_member ("levels")) {
                var levels = plan.get_array_member ("levels");
                for (uint i = 0; i < uint.min (levels.get_length (), 12); i++) {
                    var level = levels.get_object_element (i);
                    level_names[text (level, "id")] = text (level, "name");
                    level_order[text (level, "id")] = (int) i;
                }
            }
        }
        var series = new HashTable<string, Json.Array> (str_hash, str_equal);
        history_start = 0;
        if (root.has_member ("temperature_history") && !root.get_member ("temperature_history").is_null ()) {
            var history = root.get_object_member ("temperature_history");
            if (history.has_member ("times")) {
                var times = history.get_array_member ("times");
                uint count = times.get_length ();
                if (count >= 2) {
                    history_start = parse_time (times.get_string_element (0));
                    int64 end = parse_time (times.get_string_element (count - 1));
                    if (history_start > 0 && end > history_start) bucket_hours = (end - history_start) / 3600.0 / (count - 1);
                }
            }
            if (history.has_member ("series")) {
                var rows = history.get_array_member ("series");
                for (uint i = 0; i < uint.min (rows.get_length (), 256); i++) {
                    var row = rows.get_object_element (i);
                    if (row.has_member ("values")) series[text (row, "source") + "\n" + text (row, "sensor_id")] = row.get_array_member ("values");
                }
            }
        }

        readings = {};
        if (root.has_member ("temperature_sensors")) {
            var sensors = root.get_array_member ("temperature_sensors");
            for (uint i = 0; i < uint.min (sensors.get_length (), 256); i++) {
                var sensor = sensors.get_object_element (i);
                var reading = new Reading ();
                reading.name = text (sensor, "name", "Sensor");
                reading.source = text (sensor, "source");
                reading.identity = text (sensor, "sensor_id");
                reading.placed = sensor.has_member ("placement") && !sensor.get_member ("placement").is_null ();
                reading.room = text (sensor, "room_name", "");
                // Saved rooms repeat the building ("Barn · First floor"); the dropdown already names it.
                if (building != "" && reading.room.has_prefix (building + " · ")) reading.room = reading.room.substring ((building + " · ").length);
                reading.level_id = text (sensor, "level", "");
                reading.level_name = level_names.contains (reading.level_id) ? level_names[reading.level_id] : "";
                reading.order = level_order.contains (reading.level_id) ? level_order[reading.level_id] : 99;
                reading.stale = !fresh || (sensor.has_member ("stale") && sensor.get_boolean_member ("stale"));
                reading.updated = parse_time (text (sensor, "timestamp"));
                reading.measured = !sensor.has_member ("measurement_time_known") || sensor.get_boolean_member ("measurement_time_known");
                if (sensor.has_member ("metrics") && !sensor.get_member ("metrics").is_null ()) {
                    var metrics = sensor.get_object_member ("metrics");
                    reading.temp = number (metrics, "temperature_f");
                    reading.humidity = number (metrics, "humidity_pct");
                    reading.battery = number (metrics, "battery_pct");
                }
                if (!reading.temp.is_finite ()) reading.temp = double.NAN;
                var values = series[reading.source + "\n" + reading.identity];
                if (values != null) {
                    double[] list = {};
                    for (uint j = 0; j < uint.min (values.get_length (), 400); j++) {
                        var node = values.get_element (j);
                        double value = node.is_null () ? double.NAN : node.get_double ();
                        list += value.is_finite () ? value : double.NAN;
                        if (!node.is_null () && history_start > 0) reading.last_seen = history_start + (int64) (j * bucket_hours * 3600);
                    }
                    reading.values = list;
                    if (!reading.stale) reading.rate = trend (list, bucket_hours);
                }
                readings += reading;
            }
        }
        build_overview (root.has_member ("temperature_sensors"));
        build_attention ();
        rebuild_list ();
    }

    // ---- overview -----------------------------------------------------------------------

    private static string where (Reading reading) {
        if (reading.room != "") return reading.room;
        return reading.name;
    }

    private void build_overview (bool has_sensors) {
        while (overview.get_first_child () != null) overview.remove (overview.get_first_child ());
        Reading[] live = {}, indoor = {};
        int stale = 0;
        foreach (var reading in readings) {
            if (!reading.live) { stale++; continue; }
            live += reading;
            if (reading.placed) indoor += reading;
        }
        if (indoor.length == 0) indoor = live;
        if (!has_sensors || live.length == 0) {
            var waiting = new Gtk.Label (has_sensors ? "No recent temperature readings · showing last recorded values below" : "Waiting for recorded temperatures…") { xalign = 0, wrap = true };
            waiting.add_css_class ("energy-muted");
            overview.append (waiting);
            return;
        }
        double sum = 0, humidity = 0; int humid_count = 0;
        Reading warmest = indoor[0], coolest = indoor[0];
        foreach (var reading in indoor) {
            sum += reading.temp;
            if (reading.temp > warmest.temp) warmest = reading;
            if (reading.temp < coolest.temp) coolest = reading;
            if (!reading.humidity.is_nan ()) { humidity += reading.humidity; humid_count++; }
        }
        double average = sum / indoor.length;

        var top = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 10);
        var lead = new Gtk.Box (Gtk.Orientation.VERTICAL, 0) { valign = Gtk.Align.CENTER };
        var caption = new Gtk.Label (indoor[0].placed ? "INDOOR AVERAGE" : "AVERAGE") { xalign = 0 };
        caption.add_css_class ("energy-caption");
        lead.append (caption);
        var big = new Gtk.Label ("%.1f °F".printf (average)) { xalign = 0 };
        big.add_css_class ("energy-value");
        big.add_css_class ("temperature-average");
        lead.append (big);
        double spread_rate = 0; int rated = 0;
        foreach (var reading in indoor) if (!reading.rate.is_nan ()) { spread_rate += reading.rate; rated++; }
        string house_trend = rated > 0 ? rate_text (spread_rate / rated) : "";
        var detail = new Gtk.Label ("%d of %d sensors recent%s".printf (live.length, readings.length,
            house_trend != "" ? " · house " + house_trend : "")) { xalign = 0 };
        detail.add_css_class ("energy-muted");
        lead.append (detail);
        top.append (lead);

        var facts = new Gtk.Grid () { column_spacing = 8, row_spacing = 1, hexpand = true, halign = Gtk.Align.END, valign = Gtk.Align.CENTER };
        int row = 0;
        add_fact (facts, row++, "Warmest", where (warmest), "%.1f °F".printf (warmest.temp), warmest);
        if (coolest != warmest) add_fact (facts, row++, "Coolest", where (coolest), "%.1f °F".printf (coolest.temp), coolest);
        if (humid_count > 0) add_fact (facts, row++, "Humidity", "average", "%.0f%%".printf (humidity / humid_count), null);
        foreach (var reading in live) {
            if (reading.placed || !indoor[0].placed) continue;
            add_fact (facts, row++, "Unplaced", reading.name, "%.1f °F".printf (reading.temp), reading);
        }
        top.append (facts);
        overview.append (top);

        var strip = new Gtk.DrawingArea () { content_height = 30, hexpand = true, has_tooltip = true };
        strip.set_draw_func ((area, cr, width, height) => { draw_strip (area, cr, width, height, live); });
        strip.query_tooltip.connect ((x, y, keyboard, tooltip) => {
            var nearest = strip_hit (live, x, strip.get_width ());
            if (nearest == null) return false;
            tooltip.set_text ("%s · %.1f °F".printf (nearest.name, nearest.temp));
            return true;
        });
        var click = new Gtk.GestureClick ();
        click.released.connect ((n, x, y) => {
            var nearest = strip_hit (live, x, strip.get_width ());
            if (nearest != null) sensor_selected (nearest.source, nearest.identity);
        });
        strip.add_controller (click);
        overview.append (strip);
    }

    private void add_fact (Gtk.Grid grid, int row, string title, string subject, string value, Reading? target) {
        var name = new Gtk.Label (title) { xalign = 1 };
        name.add_css_class ("energy-muted");
        grid.attach (name, 0, row);
        var place = new Gtk.Label (subject) { xalign = 0, ellipsize = Pango.EllipsizeMode.END, max_width_chars = 18 };
        grid.attach (place, 1, row);
        var reading = new Gtk.Label (value) { xalign = 1 };
        reading.add_css_class ("energy-circuit-value");
        grid.attach (reading, 2, row);
        if (target != null) {
            var arrow = new Gtk.Label (rate_text (target.rate).split (" ")[0]) { xalign = 0, tooltip_text = rate_text (target.rate) };
            arrow.add_css_class ("energy-muted");
            grid.attach (arrow, 3, row);
        }
    }

    private static void strip_scale (Reading[] live, out double low, out double high) {
        low = 60; high = 82;
        foreach (var reading in live) { low = double.min (low, Math.floor (reading.temp) - 2); high = double.max (high, Math.ceil (reading.temp) + 2); }
    }

    private Reading? strip_hit (Reading[] live, double x, int width) {
        double low, high; strip_scale (live, out low, out high);
        Reading? nearest = null; double best = 10;
        foreach (var reading in live) {
            double distance = (x - (6 + (width - 12) * (reading.temp - low) / (high - low))).abs ();
            if (distance < best) { best = distance; nearest = reading; }
        }
        return nearest;
    }

    // Comfort scale with one marker per recent sensor; overlapping markers stack upwards.
    private void draw_strip (Gtk.DrawingArea area, Cairo.Context cr, int width, int height, Reading[] live) {
        double low, high; strip_scale (live, out low, out high);
        var ink = area.get_color ();
        double left = 6, span = width - 12, bar_y = height - 13, bar_h = 5;
        double[] edges = {low, COLD, WARM, HOT, high};
        double[] samples = {COLD - 1, (COLD + WARM) / 2, (WARM + HOT) / 2, HOT + 1};
        for (int i = 0; i < 4; i++) {
            double a = double.max (low, edges[i]), b = double.min (high, edges[i + 1]);
            if (b <= a) continue;
            band_colour (cr, samples[i], 0.55);
            cr.rectangle (left + span * (a - low) / (high - low), bar_y, span * (b - a) / (high - low), bar_h);
            cr.fill ();
        }
        cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.7);
        var font = Pango.FontDescription.from_string ("Sans");
        font.set_absolute_size (8 * Pango.SCALE);
        foreach (double edge in new double[] {COLD, WARM, HOT}) {
            if (edge <= low || edge >= high) continue;
            var layout = area.create_pango_layout ("%.0f°".printf (edge));
            layout.set_font_description (font);
            int tw, th; layout.get_pixel_size (out tw, out th);
            cr.move_to (left + span * (edge - low) / (high - low) - tw / 2.0, bar_y + bar_h + 1);
            Pango.cairo_show_layout (cr, layout);
        }
        double[] placed_x = {};
        foreach (var reading in live) {
            double x = left + span * (reading.temp - low) / (high - low);
            int stack = 0;
            foreach (double other in placed_x) if ((other - x).abs () < 9) stack++;
            placed_x += x;
            double y = bar_y - 5 - stack * 7;
            cr.new_sub_path (); cr.arc (x, y, 4, 0, 2 * Math.PI);
            band_colour (cr, reading.temp, 1); cr.fill_preserve ();
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.9); cr.set_line_width (1.2); cr.stroke ();
        }
    }

    // ---- attention ----------------------------------------------------------------------

    private void build_attention () {
        while (attention.get_first_child () != null) attention.remove (attention.get_first_child ());
        int64 now = new DateTime.now_utc ().to_unix ();
        bool any_live = false;
        foreach (var reading in readings) any_live = any_live || reading.live;
        // When the whole collector is stale the overview already says so; one line, not one per sensor.
        if (!any_live && readings.length > 1) {
            var line = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 6) { margin_start = 6, margin_top = 2, margin_bottom = 2 };
            line.append (new Gtk.Image.from_icon_name ("network-offline-symbolic") { pixel_size = 12 });
            line.append (new Gtk.Label ("No sensor has a recent reading · collector data may be stale") { xalign = 0, hexpand = true });
            attention.append (line);
            attention.visible = true;
            return;
        }
        foreach (var reading in readings) {
            if (reading.stale) {
                string when = reading.last_seen > 0 ? "no reading for " + ago (now - reading.last_seen).replace (" ago", "")
                                                    : "no reading in the last 24 h";
                add_alert ("network-offline-symbolic", "%s · %s".printf (reading.name, when), reading);
            }
            if (!reading.battery.is_nan () && reading.battery <= 20)
                add_alert ("battery-caution-symbolic", "%s · battery %.0f%%".printf (reading.name, reading.battery), reading);
            if (!reading.live) continue;
            if (!reading.rate.is_nan () && reading.rate.abs () >= FAST_RATE)
                add_alert (reading.rate > 0 ? "go-up-symbolic" : "go-down-symbolic",
                           "%s %s fast · %s".printf (where (reading), reading.rate > 0 ? "warming" : "cooling", rate_text (reading.rate)), reading);
            if (reading.placed && !reading.humidity.is_nan () && (reading.humidity >= 65 || reading.humidity <= 30))
                add_alert ("dialog-information-symbolic", "%s %s · %.0f%% humidity".printf (where (reading), reading.humidity >= 65 ? "humid" : "dry", reading.humidity), reading);
        }
        attention.visible = attention.get_first_child () != null;
    }

    private void add_alert (string icon, string message, Reading reading) {
        var line = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 6);
        line.append (new Gtk.Image.from_icon_name (icon) { pixel_size = 12 });
        line.append (new Gtk.Label (message) { xalign = 0, hexpand = true, ellipsize = Pango.EllipsizeMode.END });
        var button = new Gtk.Button () { child = line, has_frame = false, tooltip_text = "Open recorded history" };
        button.add_css_class ("temperature-alert");
        string source = reading.source, identity = reading.identity;
        button.clicked.connect (() => { sensor_selected (source, identity); });
        attention.append (button);
    }

    // ---- sensor list --------------------------------------------------------------------

    private static int compare (Reading a, Reading b, int mode) {
        // Readings without a recent value always sort after recent ones.
        if (mode != 0 && a.live != b.live) return a.live ? -1 : 1;
        switch (mode) {
            case 1: return a.live && a.temp != b.temp ? (a.temp > b.temp ? -1 : 1) : strcmp (a.name, b.name);
            case 2: return a.live && a.temp != b.temp ? (a.temp < b.temp ? -1 : 1) : strcmp (a.name, b.name);
            case 3:
                double ra = a.rate.is_nan () ? -1 : a.rate.abs (), rb = b.rate.is_nan () ? -1 : b.rate.abs ();
                return ra != rb ? (ra > rb ? -1 : 1) : strcmp (a.name, b.name);
            default:
                if (a.order != b.order) return a.order < b.order ? -1 : 1;
                return strcmp (where (a), where (b));
        }
    }

    private void rebuild_list () {
        while (sensor_list.get_first_child () != null) sensor_list.remove (sensor_list.get_first_child ());
        int recent = 0;
        foreach (var reading in readings) if (reading.live) recent++;
        sensor_heading.label = readings.length == 0 ? "Sensors" : "Sensors · %d of %d recent".printf (recent, readings.length);
        Reading[] ordered = readings;
        int mode = (int) sort_picker.selected;
        // Insertion sort keeps equal items stable; there are only a handful of sensors.
        for (int i = 1; i < ordered.length; i++) {
            var item = ordered[i]; int j = i - 1;
            while (j >= 0 && compare (ordered[j], item, mode) > 0) { ordered[j + 1] = ordered[j]; j--; }
            ordered[j + 1] = item;
        }
        string group = "\n";
        foreach (var reading in ordered) {
            if (mode == 0) {
                string name = reading.level_name != "" ? reading.level_name : reading.placed ? "Other" : "Not on the house plan";
                if (name != group) {
                    group = name;
                    var header = new Gtk.Label (name) { xalign = 0, margin_top = 4 };
                    header.add_css_class ("heading");
                    sensor_list.append (header);
                }
            }
            sensor_list.append (sensor_row (reading));
        }
        if (readings.length == 0) {
            var waiting = new Gtk.Label ("Waiting for recorded temperatures…") { xalign = 0 };
            waiting.add_css_class ("energy-muted");
            sensor_list.append (waiting);
        }
    }

    private Gtk.Widget sensor_row (Reading reading) {
        int64 now = new DateTime.now_utc ().to_unix ();
        var card = new Gtk.Grid () { column_spacing = 8, row_spacing = 1, hexpand = true };
        card.add_css_class ("energy-card");
        card.add_css_class ("temperature-row");

        var name = new Gtk.Label (reading.name) { xalign = 0, hexpand = true, ellipsize = Pango.EllipsizeMode.END };
        name.add_css_class ("energy-circuit-value");
        card.attach (name, 0, 0);
        string place = reading.level_name != "" && reading.room != "" && reading.room != reading.level_name
            ? reading.level_name + " · " + reading.room : reading.room != "" ? reading.room : "Not on the house plan";
        var where_label = new Gtk.Label (place) { xalign = 0, ellipsize = Pango.EllipsizeMode.END };
        where_label.add_css_class ("energy-muted");
        card.attach (where_label, 0, 1);

        var value = new Gtk.Label (reading.temp.is_nan () ? "—" : "%.1f °F".printf (reading.temp)) { xalign = 1 };
        value.add_css_class ("energy-value");
        if (reading.stale) value.add_css_class ("temperature-stale-value");
        card.attach (value, 1, 0);
        var trend = new Gtk.Label (reading.stale ? "last recorded" : rate_text (reading.rate)) { xalign = 1 };
        trend.add_css_class ("energy-muted");
        if (!reading.rate.is_nan () && reading.rate.abs () >= FAST_RATE) trend.add_css_class ("temperature-fast");
        card.attach (trend, 1, 1);

        bool has_history = false;
        double low = double.MAX, high = -double.MAX;
        foreach (double v in reading.values) if (!v.is_nan ()) { has_history = true; low = double.min (low, v); high = double.max (high, v); }
        if (has_history) {
            card.attach (sparkline (reading, low, high), 0, 2, 2);
        }

        string[] facts = {};
        if (has_history) facts += "24 h %.1f–%.1f °F".printf (low, high);
        if (!reading.humidity.is_nan ()) facts += "%.0f%% humidity".printf (reading.humidity);
        if (!reading.battery.is_nan () && reading.battery <= 30) facts += "battery %.0f%%".printf (reading.battery);
        if (reading.stale) facts += reading.last_seen > 0 ? "seen " + ago (now - reading.last_seen) : "no reading in 24 h";
        else if (reading.updated > 0) facts += (reading.measured ? "measured " : "checked ") + ago (now - reading.updated);
        var footer = new Gtk.Label (string.joinv (" · ", facts)) { xalign = 0, ellipsize = Pango.EllipsizeMode.END };
        footer.add_css_class ("energy-muted");
        card.attach (footer, 0, 3, 2);

        var button = new Gtk.Button () { child = card, has_frame = false, hexpand = true };
        button.add_css_class ("temperature-sensor");
        if (reading.live) {
            button.add_css_class (band (reading.temp));
            if (reading.temp < 68) button.add_css_class ("temperature-icy");
            if (reading.temp > HOT) button.add_css_class ("temperature-hot-label");
        } else {
            button.add_css_class ("temperature-stale");
        }
        button.tooltip_text = "Open recorded temperature history for " + reading.name;
        string source = reading.source, identity = reading.identity;
        button.clicked.connect (() => { sensor_selected (source, identity); });
        return button;
    }

    private static double spark_y (double v, double top, double plot, double minimum, double maximum) {
        return top + plot * (1 - (v - minimum) / (maximum - minimum));
    }

    private static double spark_x (int i, int width, int count) { return 2 + (width - 4) * i / (count - 1.0); }

    // 24-hour recorded series with the comfortable band shaded; hover reads off a bucket.
    private Gtk.Widget sparkline (Reading reading, double low, double high) {
        var values = reading.values;
        double minimum = double.min (low, COLD) - 0.5, maximum = double.max (high, WARM) + 0.5;
        // Keep a narrow series readable instead of stretching the comfort band across it.
        if (high - low < 6) { minimum = double.max (minimum, low - 3); maximum = double.min (maximum, high + 3); }
        if (maximum - minimum < 2) { minimum -= 1; maximum += 1; }
        var chart = new Gtk.DrawingArea () { content_height = 34, hexpand = true };
        double hover = -1;
        var motion = new Gtk.EventControllerMotion ();
        motion.motion.connect ((x, y) => { hover = x; chart.queue_draw (); });
        motion.leave.connect (() => { hover = -1; chart.queue_draw (); });
        chart.add_controller (motion);
        int64 start = history_start; double bucket = bucket_hours;
        chart.set_draw_func ((area, cr, width, height) => {
            var ink = area.get_color ();
            int count = values.length;
            if (count < 2) return;
            double top = 3, plot = height - 6;
            double band_top = spark_y (double.min (WARM, maximum), top, plot, minimum, maximum), band_bottom = spark_y (double.max (COLD, minimum), top, plot, minimum, maximum);
            if (band_bottom > band_top) {
                cr.set_source_rgba (0.976, 0.769, 0.251, 0.16);
                cr.rectangle (0, band_top, width, band_bottom - band_top); cr.fill ();
            }
            cr.set_line_width (1.8);
            cr.set_line_cap (Cairo.LineCap.ROUND);
            int last = -1;
            for (int j = 1; j < count; j++) {
                if (values[j - 1].is_nan () || values[j].is_nan ()) continue;
                band_colour (cr, (values[j - 1] + values[j]) / 2, 1);
                cr.move_to (spark_x (j - 1, width, count), spark_y (values[j - 1], top, plot, minimum, maximum)); cr.line_to (spark_x (j, width, count), spark_y (values[j], top, plot, minimum, maximum)); cr.stroke ();
            }
            for (int j = count - 1; j >= 0; j--) if (!values[j].is_nan ()) { last = j; break; }
            if (last >= 0) {
                cr.new_sub_path (); cr.arc (spark_x (last, width, count), spark_y (values[last], top, plot, minimum, maximum), 2.6, 0, 2 * Math.PI);
                band_colour (cr, values[last], 1); cr.fill ();
            }
            if (hover >= 0) {
                int index = (int) Math.round ((hover - 2) / (width - 4.0) * (count - 1));
                index = index.clamp (0, count - 1);
                cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.4); cr.set_line_width (1);
                cr.move_to (spark_x (index, width, count), 0); cr.line_to (spark_x (index, width, count), height); cr.stroke ();
                string stamp = start > 0 ? new DateTime.from_unix_local (start + (int64) (index * bucket * 3600)).format ("%a %H:%M") : "";
                string caption = values[index].is_nan () ? stamp + " · no reading" : "%s · %.1f °F".printf (stamp, values[index]);
                var layout = area.create_pango_layout (caption);
                var font = Pango.FontDescription.from_string ("Sans");
                font.set_absolute_size (9 * Pango.SCALE);
                layout.set_font_description (font);
                int tw, th; layout.get_pixel_size (out tw, out th);
                double tx = spark_x (index, width, count) + 6 + tw > width ? spark_x (index, width, count) - 6 - tw : spark_x (index, width, count) + 6;
                cr.set_source_rgba (0.1, 0.1, 0.1, 0.85); cr.rectangle (tx - 3, 0, tw + 6, th + 2); cr.fill ();
                cr.set_source_rgba (1, 1, 1, 0.95); cr.move_to (tx, 1); Pango.cairo_show_layout (cr, layout);
            }
        });
        return chart;
    }
}
