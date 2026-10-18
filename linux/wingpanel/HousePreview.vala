/* SPDX-License-Identifier: MIT */
// The menu renders only the saved plan and recorded sensor values from summary.json.
// It has no network client or credentials of its own.
public class HousePreview : Gtk.Box {
    private Gtk.DropDown floors;
    private Gtk.DrawingArea canvas;
    private Gtk.Label title;
    private Json.Object? plan;
    private Json.Array? sensors;
    private bool fresh;
    private string[] floor_ids = {};
    private string level = "";
    private string level_signature = "";
    private double scale = 1;
    private double left;
    private double bottom;
    public signal void sensor_selected (string source, string identity);

    public HousePreview () {
        Object (orientation: Gtk.Orientation.VERTICAL, spacing: 4);
        title = new Gtk.Label ("House preview") { xalign = 0, ellipsize = Pango.EllipsizeMode.END };
        title.add_css_class ("energy-section");
        append (title);
        floors = new Gtk.DropDown.from_strings ({});
        floors.notify["selected"].connect (() => {
            if (floors.selected < floor_ids.length) level = floor_ids[floors.selected];
            canvas.queue_draw ();
        });
        append (floors);
        canvas = new Gtk.DrawingArea () { content_width = 350, content_height = 270, hexpand = true };
        canvas.set_draw_func ((area, cr, width, height) => { draw_plan (cr, width, height); });
        canvas.tooltip_text = "Your saved floor plan · select a sensor dot for recorded history";
        var click = new Gtk.GestureClick ();
        click.released.connect ((n, x, y) => { select_sensor (x, y); });
        canvas.add_controller (click);
        append (canvas);
        var hint = new Gtk.Label ("Room hues use recent readings · • sensor → history") { xalign = 0, wrap = true };
        hint.add_css_class ("energy-muted");
        append (hint);
    }

    private string text (Json.Object row, string key, string fallback = "") {
        return row.has_member (key) && !row.get_member (key).is_null () ? row.get_string_member (key) : fallback;
    }

    public void set_state (Json.Object root, bool cache_fresh) {
        plan = root.has_member ("house_preview") ? root.get_object_member ("house_preview") : null;
        sensors = root.has_member ("temperature_sensors") ? root.get_array_member ("temperature_sensors") : null;
        fresh = cache_fresh;
        if (plan == null) { floors.visible = false; canvas.queue_draw (); return; }
        title.label = text (plan, "name", "Home") + (plan.get_boolean_member ("example") ? " · example plan" : " · house preview");
        var levels = plan.get_array_member ("levels");
        string signature = Json.to_string (plan.get_member ("levels"), false);
        if (signature != level_signature) {
            level_signature = signature;
            var names = new Gtk.StringList (null);
            floor_ids = {};
            uint selected = 0;
            for (uint i = 0; i < uint.min (levels.get_length (), 12); i++) {
                var item = levels.get_object_element (i);
                floor_ids += text (item, "id");
                names.append (text (item, "name"));
                if (floor_ids[i] == level) selected = i;
            }
            floors.model = names;
            floors.selected = selected;
            if (floor_ids.length > 0) level = floor_ids[selected];
        }
        floors.visible = true;
        canvas.queue_draw ();
    }

    private bool on_floor (Json.Object room) { return text (room, "level", floor_ids.length > 0 ? floor_ids[0] : "first") == level; }

    private double? temperature (Json.Object sensor) {
        if (!fresh || sensor.get_boolean_member ("stale")) return null;
        var metrics = sensor.get_object_member ("metrics");
        if (!metrics.has_member ("temperature_f") || metrics.get_member ("temperature_f").is_null ()) return null;
        double value = metrics.get_double_member ("temperature_f");
        if (!value.is_finite ()) return null;
        return value;
    }

    private double? room_temperature (string identity) {
        double sum = 0; uint count = 0;
        if (sensors == null) return null;
        for (uint i = 0; i < uint.min (sensors.get_length (), 256); i++) {
            var sensor = sensors.get_object_element (i);
            if (!sensor.has_member ("placement") || sensor.get_member ("placement").is_null ()) continue;
            if (text (sensor.get_object_member ("placement"), "room_id") != identity) continue;
            double? value = temperature (sensor);
            if (value != null) { sum += value; count++; }
        }
        if (count == 0) return null;
        return sum / count;
    }

    // Explicit ranges match the web floor plan and native temperature cards.
    private void hue (Cairo.Context cr, double? value, double alpha) {
        if (value == null) cr.set_source_rgba (0.53, 0.56, 0.53, alpha * 0.45);
        else if (value < 70) cr.set_source_rgba (0.40, 0.66, 0.94, alpha);
        else if (value <= 75) cr.set_source_rgba (0.91, 0.77, 0.26, alpha);
        else if (value < 78) cr.set_source_rgba (0.85, 0.56, 0.22, alpha);
        else cr.set_source_rgba (0.91, 0.36, 0.30, alpha);
    }

    private void label (Cairo.Context cr, string value, double x, double y, double width, double size, Gdk.RGBA ink) {
        if (width < 15) return;
        var layout = canvas.create_pango_layout (value);
        var font = Pango.FontDescription.from_string ("Sans");
        font.set_absolute_size (size * Pango.SCALE);
        layout.set_font_description (font);
        layout.set_width ((int) (width * Pango.SCALE));
        layout.set_ellipsize (Pango.EllipsizeMode.END);
        layout.set_alignment (Pango.Alignment.CENTER);
        cr.set_source_rgba (ink.red, ink.green, ink.blue, ink.alpha);
        cr.move_to (x, y); Pango.cairo_show_layout (cr, layout);
    }

    // Public for the offscreen renderer smoke test; GTK uses the same draw path.
    public void draw_plan (Cairo.Context cr, int width, int height) {
        var ink = canvas.get_color ();
        if (plan == null) {
            label (cr, "Waiting for saved house layout…", 0, height / 2, width, 12, ink);
            return;
        }
        var rooms = plan.get_array_member ("rooms");
        double min_x = double.MAX, min_z = double.MAX, max_x = -double.MAX, max_z = -double.MAX;
        for (uint i = 0; i < uint.min (rooms.get_length (), 64); i++) {
            var room = rooms.get_object_element (i); if (!on_floor (room)) continue;
            var r = room.get_array_member ("rect");
            min_x = double.min (min_x, r.get_double_element (0)); min_z = double.min (min_z, r.get_double_element (1));
            max_x = double.max (max_x, r.get_double_element (0) + r.get_double_element (2));
            max_z = double.max (max_z, r.get_double_element (1) + r.get_double_element (3));
        }
        if (max_x <= min_x || max_z <= min_z) {
            label (cr, "No rooms mapped on this floor", 0, height / 2, width, 12, ink); return;
        }
        scale = double.min ((width - 24) / (max_x - min_x), (height - 32) / (max_z - min_z));
        left = (width - (max_x - min_x) * scale) / 2 - min_x * scale;
        bottom = (height - 20 + (max_z - min_z) * scale) / 2 + min_z * scale;
        for (uint i = 0; i < uint.min (rooms.get_length (), 64); i++) {
            var room = rooms.get_object_element (i); if (!on_floor (room)) continue;
            var r = room.get_array_member ("rect");
            double x = left + r.get_double_element (0) * scale, w = r.get_double_element (2) * scale;
            double h = r.get_double_element (3) * scale, y = bottom - r.get_double_element (1) * scale - h;
            double? value = room_temperature (text (room, "id"));
            cr.rectangle (x, y, w, h); hue (cr, value, 0.48); cr.fill_preserve ();
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.65); cr.set_line_width (1.3); cr.stroke ();
            if (h >= 24) {
                // Remove the repeated building prefix without changing the saved names.
                string name = text (room, "name");
                string building = text (plan, "name");
                if (name.has_prefix (building + " · ")) name = name.substring ((building + " · ").length);
                if (h < 45 && value != null) name += " · %.1f °F".printf (value);
                label (cr, name, x + 3, y + 4, w - 6, h < 40 ? 9 : 10, ink);
                if (h >= 45 && value != null) label (cr, "%.1f °F".printf (value), x + 3, y + 20, w - 6, 11, ink);
            }
        }
        // The landing/stair route and attic hatch keep the floors easy to relate.
        var stairs = plan.get_array_member ("stairs");
        for (uint i = 0; i < uint.min (stairs.get_length (), 24); i++) {
            var stair = stairs.get_object_element (i);
            if (text (stair, "level") != level && text (stair, "toLevel") != level) continue;
            var path = stair.get_array_member ("path");
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.7); cr.set_line_width (3);
            for (uint j = 0; j < path.get_length (); j++) {
                var p = path.get_array_element (j); double x = left + p.get_double_element (0) * scale, y = bottom - p.get_double_element (1) * scale;
                if (j == 0) cr.move_to (x, y); else cr.line_to (x, y);
            }
            cr.stroke ();
        }
        var accesses = plan.get_array_member ("accesses");
        for (uint i = 0; i < uint.min (accesses.get_length (), 24); i++) {
            var access = accesses.get_object_element (i);
            if (text (access, "level") != level && text (access, "toLevel") != level) continue;
            var r = access.get_array_member ("rect");
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.9); cr.set_line_width (1.5); cr.set_dash ({3, 2}, 0);
            cr.rectangle (left + r.get_double_element (0) * scale, bottom - (r.get_double_element (1) + r.get_double_element (3)) * scale, r.get_double_element (2) * scale, r.get_double_element (3) * scale);
            cr.stroke (); cr.set_dash ({}, 0);
        }
        if (sensors != null) for (uint i = 0; i < uint.min (sensors.get_length (), 256); i++) {
            var sensor = sensors.get_object_element (i);
            if (text (sensor, "level") != level || !sensor.has_member ("placement") || sensor.get_member ("placement").is_null ()) continue;
            var placement = sensor.get_object_member ("placement");
            var p = placement.get_array_member ("position");
            double x = left + p.get_double_element (0) * scale, y = bottom - p.get_double_element (1) * scale;
            cr.arc (x, y, 5, 0, 2 * Math.PI); hue (cr, temperature (sensor), 1); cr.fill_preserve ();
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 1); cr.set_line_width (1.5); cr.stroke ();
            if (text (placement, "mount") == "pole-base") {
                cr.move_to (x, y - 6); cr.line_to (x, y - 18); cr.stroke ();
            }
        }
        label (cr, "FRONT", 0, height - 15, width, 9, ink);
    }

    private void select_sensor (double x, double y) {
        if (sensors == null || plan == null) return;
        for (uint i = 0; i < uint.min (sensors.get_length (), 256); i++) {
            var sensor = sensors.get_object_element (i);
            if (text (sensor, "level") != level || !sensor.has_member ("placement") || sensor.get_member ("placement").is_null ()) continue;
            var p = sensor.get_object_member ("placement").get_array_member ("position");
            double dx = x - left - p.get_double_element (0) * scale, dy = y - bottom + p.get_double_element (1) * scale;
            if (dx * dx + dy * dy <= 144) { sensor_selected (text (sensor, "source"), text (sensor, "sensor_id")); return; }
        }
    }
}
