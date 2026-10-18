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
    public bool perspective { get; set; default = true; }
    private double center_x;
    private double center_z;
    private double origin_x;
    private double origin_y;
    private bool positioned;
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
            positioned = false;
            canvas.queue_draw ();
        });
        append (floors);
        var views = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 4);
        var spatial = new Gtk.ToggleButton.with_label ("3D cutaway") { active = true, hexpand = true };
        var flat = new Gtk.ToggleButton.with_label ("Floor plan") { group = spatial, hexpand = true };
        spatial.toggled.connect (() => { perspective = spatial.active; positioned = false; canvas.queue_draw (); });
        views.append (spatial); views.append (flat); append (views);
        canvas = new Gtk.DrawingArea () { content_width = 350, content_height = 310, hexpand = true };
        canvas.set_draw_func ((area, cr, width, height) => { draw_plan (cr, width, height); });
        canvas.tooltip_text = "Saved house geometry · select a sensor dot for recorded history";
        var click = new Gtk.GestureClick ();
        click.released.connect ((n, x, y) => { select_sensor (x, y); });
        canvas.add_controller (click);
        append (canvas);
        var hint = new Gtk.Label ("Blue <70 · Yellow 70–75 · Amber 75–78 · Red ≥78 °F\nGrey = no recent reading · • sensor → history") { xalign = 0, wrap = true };
        hint.add_css_class ("energy-muted");
        append (hint);
    }

    private string text (Json.Object row, string key, string fallback = "") {
        return row.has_member (key) && !row.get_member (key).is_null () ? row.get_string_member (key) : fallback;
    }

    public void set_state (Json.Object root, bool cache_fresh) {
        positioned = false;
        plan = root.has_member ("house_preview") ? root.get_object_member ("house_preview") : null;
        sensors = root.has_member ("temperature_sensors") ? root.get_array_member ("temperature_sensors") : null;
        fresh = cache_fresh;
        if (plan == null) { title.label = "House preview"; floors.visible = false; canvas.queue_draw (); return; }
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
        positioned = false;
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
        if (perspective) { draw_spatial (cr, width, height, min_x, min_z, max_x, max_z, ink); return; }
        positioned = true;
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

    // Orthographic cutaway: every layer uses the same meter-to-screen transform,
    // including hit testing. The front walls stay low so rooms remain readable.
    private void project (double x, double z, double h, out double sx, out double sy) {
        x -= center_x; z -= center_z;
        sx = origin_x + (x * 0.84 + z * 0.54) * scale;
        sy = origin_y + (x * 0.30 - z * 0.47 - h * 0.85) * scale;
    }

    private void face (Cairo.Context cr, double x, double z, double w, double d, double h) {
        double sx, sy;
        project (x, z, h, out sx, out sy); cr.move_to (sx, sy);
        project (x + w, z, h, out sx, out sy); cr.line_to (sx, sy);
        project (x + w, z + d, h, out sx, out sy); cr.line_to (sx, sy);
        project (x, z + d, h, out sx, out sy); cr.line_to (sx, sy); cr.close_path ();
    }

    private void edge (Cairo.Context cr, double x, double z, double x2, double z2, double h) {
        double sx, sy;
        project (x, z, 0, out sx, out sy); cr.move_to (sx, sy);
        project (x2, z2, 0, out sx, out sy); cr.line_to (sx, sy);
        project (x2, z2, h, out sx, out sy); cr.line_to (sx, sy);
        project (x, z, h, out sx, out sy); cr.line_to (sx, sy); cr.close_path ();
    }

    private void draw_spatial (Cairo.Context cr, int width, int height, double min_x, double min_z,
                               double max_x, double max_z, Gdk.RGBA ink) {
        center_x = (min_x + max_x) / 2; center_z = (min_z + max_z) / 2;
        double w = max_x - min_x, d = max_z - min_z;
        scale = double.min ((width - 32) / (w * 0.84 + d * 0.54),
                            (height - 76) / (w * 0.30 + d * 0.47 + 0.65));
        origin_x = width / 2.0; origin_y = height / 2.0 + 4;
        positioned = true;
        var rooms = plan.get_array_member ("rooms");
        // A soft offset shadow and a visible foundation make the footprint legible.
        cr.save (); cr.translate (0, 5);
        for (uint i = 0; i < rooms.get_length (); i++) {
            var room = rooms.get_object_element (i); if (!on_floor (room)) continue;
            var r = room.get_array_member ("rect");
            face (cr, r.get_double_element (0), r.get_double_element (1), r.get_double_element (2), r.get_double_element (3), 0);
            cr.set_source_rgba (0, 0, 0, 0.16); cr.fill ();
        }
        cr.restore ();
        for (uint i = 0; i < rooms.get_length (); i++) {
            var room = rooms.get_object_element (i); if (!on_floor (room)) continue;
            var r = room.get_array_member ("rect");
            double x = r.get_double_element (0), z = r.get_double_element (1), rw = r.get_double_element (2), rd = r.get_double_element (3);
            face (cr, x, z, rw, rd, 0);
            // Neutral floor material under the translucent temperature wash.
            string material = text (room, "floor", "oak");
            if (material == "walnut") cr.set_source_rgb (0.46, 0.35, 0.26);
            else if (material == "tile" || material == "stone") cr.set_source_rgb (0.66, 0.68, 0.65);
            else cr.set_source_rgb (0.70, 0.60, 0.43);
            if (room_temperature (text (room, "id")) == null) cr.set_source_rgb (0.57, 0.59, 0.57);
            cr.fill_preserve (); hue (cr, room_temperature (text (room, "id")), 0.60); cr.fill_preserve ();
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.65); cr.set_line_width (1); cr.stroke ();
        }
        // Low walls show the room volumes without hiding readings behind a roof.
        for (uint i = 0; i < rooms.get_length (); i++) {
            var room = rooms.get_object_element (i); if (!on_floor (room)) continue;
            var r = room.get_array_member ("rect");
            double x = r.get_double_element (0), z = r.get_double_element (1), rw = r.get_double_element (2), rd = r.get_double_element (3);
            edge (cr, x, z, x, z + rd, 0.48);
            cr.set_source_rgba (0.62, 0.64, 0.58, 0.90); cr.fill_preserve ();
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.5); cr.stroke ();
            edge (cr, x, z + rd, x + rw, z + rd, 0.48);
            cr.set_source_rgba (0.80, 0.81, 0.74, 0.95); cr.fill_preserve ();
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.5); cr.stroke ();
        }
        if (plan.has_member ("openings")) {
            var openings = plan.get_array_member ("openings");
            for (uint i = 0; i < uint.min (openings.get_length (), 256); i++) {
                var item = openings.get_object_element (i); if (!on_floor (item)) continue;
                var a = item.get_array_member ("from"); var b = item.get_array_member ("to");
                double sx, sy; bool window = text (item, "type") == "window";
                project (a.get_double_element (0), a.get_double_element (1), window ? 0.25 : 0, out sx, out sy); cr.move_to (sx, sy);
                project (b.get_double_element (0), b.get_double_element (1), window ? 0.25 : 0, out sx, out sy); cr.line_to (sx, sy);
                if (window) cr.set_source_rgb (0.42, 0.77, 0.90); else cr.set_source_rgb (0.84, 0.78, 0.64);
                cr.set_line_width (4); cr.stroke ();
            }
        }
        // Saved furniture footprints aid orientation; no device or circuit data.
        if (plan.has_member ("furniture")) {
            var furniture = plan.get_array_member ("furniture");
            for (uint i = 0; i < uint.min (furniture.get_length (), 256); i++) {
                var item = furniture.get_object_element (i); if (!on_floor (item)) continue;
                var p = item.get_array_member ("pos");
                double fw = 1.2, fd = 0.7;
                switch (text (item, "type")) {
                    case "sofa": fw = 2.2; fd = 0.95; break;
                    case "bed": fw = 1.6; fd = 2.1; break;
                    case "rug": fw = 2; fd = 1.4; break;
                    case "diningSet": fw = 1.8; fd = 0.95; break;
                    case "kitchen": fw = 3; fd = 0.62; break;
                    case "island": fw = 2; fd = 0.95; break;
                    case "wardrobe": fw = 1.6; fd = 0.6; break;
                    case "plant": fw = 0.45; fd = 0.45; break;
                    default: break;
                }
                if (item.has_member ("width")) fw = item.get_double_member ("width");
                if (item.has_member ("size")) { var size = item.get_array_member ("size"); fw = size.get_double_element (0); fd = size.get_double_element (1); }
                double angle = item.has_member ("rot") ? item.get_double_member ("rot") * Math.PI / 180 : 0;
                double px = p.get_double_element (0), pz = p.get_double_element (1), sx, sy;
                for (int corner = 0; corner < 4; corner++) {
                    double dx = (corner == 0 || corner == 3 ? -fw : fw) / 2;
                    double dz = (corner < 2 ? -fd : fd) / 2;
                    project (px + dx * Math.cos (angle) + dz * Math.sin (angle),
                             pz - dx * Math.sin (angle) + dz * Math.cos (angle), 0.08, out sx, out sy);
                    if (corner == 0) cr.move_to (sx, sy); else cr.line_to (sx, sy);
                }
                cr.close_path (); cr.set_source_rgba (0.90, 0.88, 0.78, 0.65); cr.fill_preserve ();
                cr.set_source_rgba (0.20, 0.24, 0.22, 0.65); cr.set_line_width (1); cr.stroke ();
            }
        }
        var stairs = plan.get_array_member ("stairs");
        for (uint i = 0; i < uint.min (stairs.get_length (), 24); i++) {
            var stair = stairs.get_object_element (i);
            if (text (stair, "level") != level && text (stair, "toLevel") != level) continue;
            var path = stair.get_array_member ("path");
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.8); cr.set_line_width (3);
            for (uint j = 0; j < path.get_length (); j++) {
                var p = path.get_array_element (j); double sx, sy;
                project (p.get_double_element (0), p.get_double_element (1), 0, out sx, out sy);
                if (j == 0) cr.move_to (sx, sy); else cr.line_to (sx, sy);
            }
            cr.stroke ();
        }
        var accesses = plan.get_array_member ("accesses");
        for (uint i = 0; i < uint.min (accesses.get_length (), 24); i++) {
            var item = accesses.get_object_element (i);
            if (text (item, "level") != level && text (item, "toLevel") != level) continue;
            var r = item.get_array_member ("rect");
            face (cr, r.get_double_element (0), r.get_double_element (1), r.get_double_element (2), r.get_double_element (3), 0);
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 0.9); cr.set_line_width (1.5); cr.set_dash ({3, 2}, 0); cr.stroke (); cr.set_dash ({}, 0);
        }
        // Labels stay screen-aligned, above architecture. Skip cramped rooms.
        for (uint i = 0; i < rooms.get_length (); i++) {
            var room = rooms.get_object_element (i); if (!on_floor (room)) continue;
            var r = room.get_array_member ("rect"); double sx, sy;
            double rw = r.get_double_element (2), rd = r.get_double_element (3);
            project (r.get_double_element (0) + rw / 2, r.get_double_element (1) + rd / 2, 0, out sx, out sy);
            double label_width = double.min (116, rw * scale * 0.72);
            sy -= 20; // Keep badges above the sensor at the room center.
            if (label_width < 40 || rd * scale < 28) continue;
            string name = text (room, "name"), building = text (plan, "name");
            if (name.has_prefix (building + " · ")) name = name.substring ((building + " · ").length);
            cr.rectangle (sx - label_width / 2, sy - 16, label_width, 33);
            bool dark = ink.red > 0.6;
            cr.set_source_rgba (dark ? 0.12 : 0.98, dark ? 0.14 : 0.98, dark ? 0.13 : 0.95, 0.92); cr.fill ();
            label (cr, name, sx - label_width / 2 + 3, sy - 14, label_width - 6, 9, ink);
            double? value = room_temperature (text (room, "id"));
            label (cr, value == null ? "—" : "%.1f °F".printf (value), sx - label_width / 2 + 3, sy, label_width - 6, 10, ink);
        }
        if (sensors != null) for (uint i = 0; i < uint.min (sensors.get_length (), 256); i++) {
            var sensor = sensors.get_object_element (i);
            if (text (sensor, "level") != level || !sensor.has_member ("placement") || sensor.get_member ("placement").is_null ()) continue;
            var placement = sensor.get_object_member ("placement"); var p = placement.get_array_member ("position");
            double sx, sy; project (p.get_double_element (0), p.get_double_element (1), 0, out sx, out sy);
            cr.arc (sx, sy, 8, 0, 2 * Math.PI); cr.set_source_rgba (0, 0, 0, 0.18); cr.fill ();
            cr.arc (sx, sy, 5, 0, 2 * Math.PI); hue (cr, temperature (sensor), 1); cr.fill_preserve ();
            cr.set_source_rgba (ink.red, ink.green, ink.blue, 1); cr.set_line_width (1.5); cr.stroke ();
            if (text (placement, "mount") == "pole-base") { cr.move_to (sx, sy - 6); cr.line_to (sx, sy - 18); cr.stroke (); }
        }
        label (cr, "FRONT · 3D CUTAWAY", 0, height - 18, width, 9, ink);
    }

    public void select_sensor (double x, double y) {
        if (sensors == null || plan == null || !positioned) return;
        Json.Object? nearest = null; double distance = 145;
        for (uint i = 0; i < uint.min (sensors.get_length (), 256); i++) {
            var sensor = sensors.get_object_element (i);
            if (text (sensor, "level") != level || !sensor.has_member ("placement") || sensor.get_member ("placement").is_null ()) continue;
            var p = sensor.get_object_member ("placement").get_array_member ("position");
            double sx, sy;
            if (perspective) project (p.get_double_element (0), p.get_double_element (1), 0, out sx, out sy);
            else { sx = left + p.get_double_element (0) * scale; sy = bottom - p.get_double_element (1) * scale; }
            double dx = x - sx, dy = y - sy;
            if (dx * dx + dy * dy <= 144 && dx * dx + dy * dy < distance) {
                nearest = sensor; distance = dx * dx + dy * dy;
            }
        }
        if (nearest != null) sensor_selected (text (nearest, "source"), text (nearest, "sensor_id"));
    }
}
