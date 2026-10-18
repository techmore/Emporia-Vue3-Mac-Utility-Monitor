/* Native renderer regression test. Run under a GTK display or xvfb-run. */
int main (string[] args) {
    Gtk.init ();
    var preview = new HousePreview ();
    preview.perspective = false;
    var parser = new Json.Parser ();
    try {
        if (args.length > 1) parser.load_from_file (args[1]);
        else parser.load_from_data ("""{
          "house_preview":{"name":"Test home","example":false,
            "levels":[{"id":"first","name":"First floor"},{"id":"upstairs","name":"Upstairs"},{"id":"attic","name":"Attic"}],
            "rooms":[{"id":"a","name":"Living room","rect":[0,0,10,10],"level":"first"},{"id":"b","name":"Bedroom overlooking the garden and courtyard","rect":[0,0,10,10],"level":"upstairs"},{"id":"c","name":"Attic","rect":[0,0,10,10],"level":"attic"}],
            "furniture":[{"type":"sofa","pos":[2,2],"width":2.5,"rot":90,"level":"first"}],
            "openings":[{"type":"window","from":[0,1],"to":[0,3],"level":"first"}],
            "stairs":[{"level":"first","toLevel":"upstairs","path":[[8,8],[8,9],[4,9]],"width":1}],
            "accesses":[{"level":"upstairs","toLevel":"attic","rect":[4,4,1,1]}]},
          "temperature_sensors":[
            {"source":"aqara_local","sensor_id":"one","level":"first","stale":false,"placement":{"room_id":"a","position":[5,5]},"metrics":{"temperature_f":68}},
            {"source":"aqara_local","sensor_id":"two","level":"upstairs","stale":false,"placement":{"room_id":"b","position":[5,5]},"metrics":{"temperature_f":80}},
            {"source":"aqara_local","sensor_id":"stale","level":"first","stale":true,"placement":{"room_id":"a","position":[6,5]},"metrics":{"temperature_f":100}},
            {"source":"aqara_local","sensor_id":"three","level":"attic","stale":false,"placement":{"room_id":"c","position":[5,5],"mount":"pole-base"},"metrics":{"temperature_f":72}}]}
        """);
        var root = parser.get_root ().get_object ();
        preview.set_state (root, true);
        var picker = (Gtk.DropDown) preview.get_first_child ().get_next_sibling ();
        assert (picker.model.get_n_items () == 3);
        int render_width = args.length > 2 ? 640 : 390;
        int render_height = args.length > 2 ? 420 : 310;
        var surface = new Cairo.ImageSurface (Cairo.Format.ARGB32, render_width, render_height);
        var cr = new Cairo.Context (surface);
        for (uint i = 0; i < 3; i++) {
            picker.selected = i;
            // A newly parsed summary must retain the floor selected in the dropdown.
            var refreshed = new Json.Parser (); refreshed.load_from_data (Json.to_string (parser.get_root (), false));
            preview.set_state (refreshed.get_root ().get_object (), true);
            assert (picker.selected == i);
            if (args.length == 1) {
                var room_expander = (Gtk.Expander) preview.get_last_child ();
                var room_list = (Gtk.Box) room_expander.child;
                var row = (Gtk.Box) room_list.get_first_child ();
                var name = (Gtk.Label) row.get_first_child ();
                var room = root.get_object_member ("house_preview").get_array_member ("rooms").get_object_element (i);
                assert (name.label == room.get_string_member ("name"));
            }
            if (args.length > 2) cr.set_source_rgb (0.14, 0.15, 0.12);
            else cr.set_source_rgb (1, 1, 1);
            cr.paint ();
            preview.perspective = args.length > 2;
            preview.draw_plan (cr, render_width, render_height);
            surface.flush ();
            if (args.length > 2) surface.write_to_png (args[2] + "-%u.png".printf (i));
            else {
                unowned uchar[] bytes = surface.get_data ();
                int p = 100 * surface.get_stride () + 130 * 4;
                // First floor is blue, upstairs red, attic yellow. Stale sensor
                // on the first floor must not warm the room mean.
                if (i == 0) assert (bytes[p] > bytes[p + 2]);
                else if (i == 1) assert (bytes[p + 2] > bytes[p]);
                else assert (bytes[p + 1] > bytes[p]);
            }
        }
        // Old/disconnected cache keeps geometry but loses temperature hues.
        preview.perspective = false;
        preview.set_state (root, false); picker.selected = 0;
        cr.set_source_rgb (1, 1, 1); cr.paint (); preview.draw_plan (cr, 390, 310); surface.flush ();
        unowned uchar[] stale = surface.get_data ();
        int p = 100 * surface.get_stride () + 130 * 4;
        assert (((int) stale[p] - (int) stale[p + 2]).abs () < 5);
        if (args.length == 1) {
            // The projected sensor at the center of the fixture must open the
            // same history in either view; changing floors invalidates old hits.
            int hits = 0;
            preview.sensor_selected.connect ((source, identity) => {
                assert (source == "aqara_local"); assert (identity == "one"); hits++;
            });
            preview.perspective = true; preview.set_state (root, true);
            preview.draw_plan (cr, 390, 310);
            preview.select_sensor (195, 159); assert (hits == 1);
            preview.select_sensor (0, 0); assert (hits == 1);
            picker.selected = 1;
            preview.select_sensor (195, 159); assert (hits == 1);
            for (uint i = 0; i < 3; i++) {
                picker.selected = i;
                cr.set_source_rgb (1, 1, 1); cr.paint ();
                preview.draw_plan (cr, 390, 310); surface.flush ();
                unowned uchar[] pixels = surface.get_data ();
                int sample = 199 * surface.get_stride () + 210 * 4;
                if (i == 0) assert (pixels[sample] > pixels[sample + 2]);
                else if (i == 1) assert (pixels[sample + 2] > pixels[sample]);
                else assert (pixels[sample + 1] > pixels[sample]);
            }
            // Older collectors omit the optional architectural geometry.
            root.get_object_member ("house_preview").remove_member ("furniture");
            preview.set_state (root, false);
            cr.set_source_rgb (1, 1, 1); cr.paint ();
            preview.draw_plan (cr, 390, 310); surface.flush ();
            unowned uchar[] grey = surface.get_data ();
            int sample = 199 * surface.get_stride () + 210 * 4;
            assert (((int) grey[sample] - (int) grey[sample + 2]).abs () < 5);
        }
        var missing = new Json.Object (); preview.set_state (missing, false);
        preview.draw_plan (cr, 390, 310);
        stdout.printf ("Native preview: all floors, refresh selection, hues, stale cache and missing plan passed.\n");
    } catch (Error e) { stderr.printf ("%s\n", e.message); return 1; }
    return 0;
}
