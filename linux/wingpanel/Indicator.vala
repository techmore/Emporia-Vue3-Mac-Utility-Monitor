/* SPDX-License-Identifier: MIT */
public class EnergyIndicator : Wingpanel.Indicator {
    private const int MENU_WIDTH = 440;          // comfortable
    private const int MENU_WIDTH_COMPACT = 340;  // dense, CodexBar-like
    private Gtk.Box display_box;
    private Gtk.Label power;
    private Gtk.Box menu;
    private Gtk.Box readings;
    private Gtk.Label status;
    private Gtk.Image live_dot;
    private bool compact = false;
    private Gtk.Switch? compact_switch = null;
    private bool syncing_switch = false;
    private Gtk.Box notice;
    private Gtk.Label notice_text;
    private Gtk.Image panel_icon;
    private Gtk.Label panel_title;
    private uint timer;
    private string previous = "";
    private Gtk.CssProvider css;
    private Gtk.ToggleButton[] view_buttons = {};
    private int current_view = 0;
    private HousePreview house_preview;
    private TemperatureView temperature_view;
    private EnergyPrefs prefs;
    private Gtk.ScrolledWindow scroll;

    public EnergyIndicator () {
        Object (code_name: "energy-monitor", visible: true);
        display_box = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 3);
        panel_icon = new Gtk.Image.from_icon_name ("weather-storm-symbolic");
        display_box.append (panel_icon);
        power = new Gtk.Label ("");
        display_box.append (power);
        display_box.tooltip_text = "Energy Monitor";
        menu = new Gtk.Box (Gtk.Orientation.VERTICAL, 6) {
            margin_top = 6, margin_bottom = 6, margin_start = 6, margin_end = 6,
            width_request = MENU_WIDTH
        };
        menu.add_css_class ("energy-monitor");
        menu.add_css_class ("energy-menu");
        css = new Gtk.CssProvider ();
        Gtk.StyleContext.add_provider_for_display (Gdk.Display.get_default (), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION);
        load_theme ();
        panel_title = new Gtk.Label ("Energy Monitor") { xalign = 0, hexpand = true, ellipsize = Pango.EllipsizeMode.END };
        panel_title.add_css_class ("energy-title");
        panel_title.add_css_class ("energy-menu-title");
        status = new Gtk.Label ("Waiting for collector connection") {
            xalign = 0, ellipsize = Pango.EllipsizeMode.END, max_width_chars = 44, hexpand = true
        };
        status.add_css_class (Granite.CssClass.DIM);
        status.add_css_class ("energy-timestamp");
        live_dot = new Gtk.Image.from_icon_name ("media-record-symbolic") { pixel_size = 10, valign = Gtk.Align.CENTER };
        live_dot.add_css_class ("energy-dot");
        live_dot.update_property (Gtk.AccessibleProperty.LABEL, "Collector status", -1);
        // Title over a status line, the way Quick Settings headers read; the dot never carries state alone.
        var subtitle = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 6);
        subtitle.append (live_dot);
        subtitle.append (status);
        var header = new Gtk.Box (Gtk.Orientation.VERTICAL, 0) { margin_start = 6, margin_end = 6, margin_top = 3 };
        header.append (panel_title);
        header.append (subtitle);
        menu.append (header);
        notice_text = new Gtk.Label ("") { xalign = 0, wrap = true, hexpand = true };
        notice = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 8) { visible = false };
        notice.add_css_class ("energy-notice");
        notice.append (new Gtk.Image.from_icon_name ("dialog-warning-symbolic") { pixel_size = 16, valign = Gtk.Align.START });
        notice.append (notice_text);
        menu.append (notice);
        prefs = new EnergyPrefs ();
        current_view = prefs.view;
        // A linked pair of toggle buttons is elementary's segmented control (see Quick Settings).
        var switcher = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 0) { homogeneous = true };
        switcher.add_css_class ("linked");
        switcher.add_css_class ("energy-switcher");
        string[] view_names = {"Power", "Temperatures"};
        for (int i = 0; i < view_names.length; i++) {
            var toggle = new Gtk.ToggleButton.with_label (view_names[i]);
            if (i > 0) toggle.group = view_buttons[0];
            view_buttons += toggle;
            switcher.append (toggle);
        }
        view_buttons[current_view].active = true;
        for (int i = 0; i < view_buttons.length; i++) {
            int index = i;
            var toggle = view_buttons[i];
            toggle.toggled.connect (() => {
                if (!toggle.active || current_view == index) return;
                current_view = index; prefs.view = index; prefs.save ();
                previous = ""; update ();
            });
        }
        menu.append (switcher);
        house_preview = new HousePreview ();
        house_preview.sensor_selected.connect ((source, identity) => { launch ({"--temperature", source, identity}); });
        temperature_view = new TemperatureView (house_preview, prefs);
        temperature_view.sensor_selected.connect ((source, identity) => { launch ({"--temperature", source, identity}); });
        readings = new Gtk.Box (Gtk.Orientation.VERTICAL, 6);
        scroll = new Gtk.ScrolledWindow () {
            child = readings, hscrollbar_policy = Gtk.PolicyType.NEVER,
            max_content_height = 440, propagate_natural_height = true
        };
        menu.append (scroll);
        menu.append (new Gtk.Separator (Gtk.Orientation.HORIZONTAL));
        add_action ("Dashboard", {}, null, "go-home-symbolic");
        add_action ("Temperature history…", {"--temperatures"}, null, "document-properties-symbolic");
        var extra = new Gtk.Box (Gtk.Orientation.VERTICAL, 3);
        // Visible, one-click density switch (also in the app's Preferences); the app picks the change up live.
        var density_row = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 8) { margin_start = 6, margin_end = 6, margin_top = 3, margin_bottom = 3 };
        density_row.append (new Gtk.Image.from_icon_name ("view-list-symbolic") { pixel_size = 16 });
        density_row.append (new Gtk.Label ("Compact layout") { xalign = 0, hexpand = true });
        compact_switch = new Gtk.Switch () { valign = Gtk.Align.CENTER, active = compact, tooltip_text = "Denser rows and a narrower dropdown" };
        compact_switch.update_property (Gtk.AccessibleProperty.LABEL, "Compact layout", -1);
        compact_switch.notify["active"].connect (() => {
            if (!syncing_switch) write_density (compact_switch.active);
        });
        density_row.append (compact_switch);
        extra.append (density_row);
        var more = new Gtk.Expander ("More…") { child = extra };
        menu.append (more);
        add_action ("Barn · First floor", {"--floor", "first"}, extra, "go-home-symbolic");
        add_action ("Barn · Upstairs", {"--floor", "upstairs"}, extra, "go-up-symbolic");
        add_action ("Barn · Attic", {"--floor", "attic"}, extra, "go-top-symbolic");
        add_action ("Temperature floor plan", {"--temperature-plan"}, extra, "find-location-symbolic");
        add_action ("Collector Settings…", {"--settings"}, extra, "preferences-system-symbolic");
        add_action ("Collector Connection…", {"--connection"}, extra, "network-server-symbolic");
        add_action ("Refresh", {"--refresh"}, extra, "view-refresh-symbolic");
        menu.append (new Gtk.Separator (Gtk.Orientation.HORIZONTAL));
        add_action ("Quit Energy Monitor", {"--quit"}, null, "application-exit-symbolic");
        update ();
        timer = Timeout.add_seconds (3, () => { update (); return Source.CONTINUE; });
        display_box.destroy.connect (() => { if (timer != 0) Source.remove (timer); });
    }

    private void load_theme () {
        // One stylesheet built on the elementary named colours: light, dark and accent follow the system.
        // Prefer the installed copy next to the app so style changes never need a panel reinstall.
        var installed = Path.build_filename (Environment.get_home_dir (), ".local", "lib", "energy-monitor", "theme.css");
        if (FileUtils.test (installed, FileTest.IS_REGULAR)) css.load_from_path (installed);
        else css.load_from_resource ("/org/techmore/energy/theme.css");
    }

    private void write_density (bool on) {
        var path = Path.build_filename (Environment.get_user_config_dir (), "energy-monitor", "ui.json");
        var parser = new Json.Parser ();
        var object = new Json.Object ();
        try {
            string text;
            FileUtils.get_contents (path, out text);
            parser.load_from_data (text);
            object = parser.get_root ().get_object ();
        } catch (Error e) { /* No file yet: start a fresh one. */ }
        object.set_string_member ("density", on ? "compact" : "comfortable");
        var root = new Json.Node (Json.NodeType.OBJECT);
        root.set_object (object);
        var generator = new Json.Generator ();
        generator.root = root;
        try {
            DirUtils.create_with_parents (Path.get_dirname (path), 0700);
            FileUtils.set_contents (path, generator.to_data (null));
            FileUtils.chmod (path, 0600);
        } catch (Error e) { warning ("Energy dropdown density: %s", e.message); return; }
        previous = "";
        update ();
    }

    // Density is chosen in the app's Preferences (ui.json); the dropdown only follows it.
    private void refresh_density () {
        bool wanted = false;
        try {
            string text;
            FileUtils.get_contents (Path.build_filename (Environment.get_user_config_dir (), "energy-monitor", "ui.json"), out text);
            var parser = new Json.Parser ();
            parser.load_from_data (text);
            var saved = parser.get_root ().get_object ();
            wanted = saved.has_member ("density") && saved.get_string_member ("density") == "compact";
        } catch (Error e) { wanted = false; }
        compact = wanted;
        if (compact_switch != null && compact_switch.active != wanted) {
            syncing_switch = true;
            compact_switch.active = wanted;
            syncing_switch = false;
        }
        if (compact) menu.add_css_class ("energy-compact"); else menu.remove_css_class ("energy-compact");
    }

    private void set_live (bool live) {
        if (live) { live_dot.remove_css_class ("energy-offline"); panel_icon.remove_css_class ("dim-label"); }
        else { live_dot.add_css_class ("energy-offline"); panel_icon.add_css_class ("dim-label"); }
        live_dot.update_property (Gtk.AccessibleProperty.LABEL, live ? "Collector live" : "Collector offline or stale", -1);
        display_box.tooltip_text = live ? "Energy Monitor · live" : "Energy Monitor · readings are stale";
        notice.visible = !live;
        notice_text.label = "Live readings are unavailable. Showing the last recorded values; they are not current.";
    }

    private void launch (string[] options) {
        string[] args = {Environment.get_home_dir () + "/.local/bin/energy-monitor"};
        foreach (var option in options) args += option;
        try { Pid pid; Process.spawn_async (null, args, null, SpawnFlags.SEARCH_PATH, null, out pid); }
        catch (Error e) { status.label = "Could not open Energy Monitor"; warning ("%s", e.message); }
    }

    private void add_action (string label, string[] options, Gtk.Box? target = null, string icon = "") {
        var button = new Wingpanel.PopoverMenuItem ();
        // Icon + label row, the layout of elementary's own Quick Settings entries.
        var row = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 8);
        row.append (new Gtk.Image.from_icon_name (icon != "" ? icon : "window-new-symbolic") { pixel_size = 16 });
        row.append (new Gtk.Label (label) { xalign = 0, hexpand = true });
        button.child = row;
        button.clicked.connect (() => { launch (options); });
        (target ?? menu).append (button);
    }

    private string string_member (Json.Object obj, string key, string fallback = "") {
        if (!obj.has_member (key) || obj.get_member (key).is_null ()) return fallback;
        return obj.get_string_member (key);
    }

    private string metric (Json.Object obj, string key, string format) {
        if (!obj.has_member (key) || obj.get_member (key).is_null ()) return "—";
        return format.printf (obj.get_double_member (key));
    }

    private Gtk.Widget metric_card (string name, string value, string detail = "") {
        var box = new Gtk.Box (Gtk.Orientation.VERTICAL, 1) { hexpand = true };
        var caption = new Gtk.Label (name) { xalign = 0 };
        caption.add_css_class ("energy-caption");
        box.append (caption);
        var number = new Gtk.Label (value) { xalign = 0 };
        number.add_css_class ("energy-value");
        box.append (number);
        if (detail != "") {
            var detail_label = new Gtk.Label (detail) { xalign = 0 };
            detail_label.add_css_class ("energy-muted");
            box.append (detail_label);
        }
        box.add_css_class ("energy-card");
        return box;
    }

    private Gtk.Widget breaker_cell (Json.Object? circuit, int slot, bool left, bool live, int span, Gtk.SizeGroup handles) {
        int ROW = compact ? 26 : 46;
        int height = ROW * span + 4 * (span - 1);
        string state = circuit != null ? string_member (circuit, "slot_state", "empty") : "empty";
        string channel = circuit != null ? string_member (circuit, "channel_name") : "";
        string display = circuit != null ? string_member (circuit, "display_name", channel) : "";
        // Compact: name and reading share one line; comfortable: reading under the name.
        var info = new Gtk.Box (compact ? Gtk.Orientation.HORIZONTAL : Gtk.Orientation.VERTICAL, compact ? 8 : 1) { hexpand = true, valign = Gtk.Align.CENTER };
        var name_label = new Gtk.Label (state == "empty" ? "Empty" : display) {
            xalign = left ? 1 : 0, ellipsize = Pango.EllipsizeMode.END, max_width_chars = compact ? 9 : 14, hexpand = compact
        };
        string detail = "";
        int poles = 1;
        if (circuit != null) {
            poles = circuit.has_member ("poles") && !circuit.get_member ("poles").is_null () ? (int) circuit.get_int_member ("poles") : 1;
            if (state == "monitored") detail = live ? metric (circuit, "watts", "%.0f W") : "—";
            else if (state == "unmonitored") detail = "Unmonitored";
            if (!compact && circuit.has_member ("amps") && !circuit.get_member ("amps").is_null ())
                detail += (detail != "" ? " · " : "") + "%dP %d A".printf (poles, (int) circuit.get_int_member ("amps"));
            else if (!compact && poles == 2) detail += (detail != "" ? " · " : "") + "2P";
        }
        var detail_label = new Gtk.Label (detail) { xalign = left ? 1 : 0, ellipsize = Pango.EllipsizeMode.END };
        detail_label.add_css_class (Granite.CssClass.SMALL);
        detail_label.add_css_class (Granite.CssClass.NUMERIC);
        if (state != "monitored") { name_label.add_css_class (Granite.CssClass.DIM); }
        detail_label.add_css_class (Granite.CssClass.DIM);
        if (compact && left) { info.append (detail_label); info.append (name_label); }
        else { info.append (name_label); info.append (detail_label); }
        // The toggle handle sits on the bus side, like the real breaker's: accent when carrying load, amber/red when loaded, grey when idle or offline.
        var handle = new Gtk.Box (Gtk.Orientation.VERTICAL, 0) { width_request = 10, height_request = compact ? (span == 2 ? 28 : 12) : (span == 2 ? 36 : 18), valign = Gtk.Align.CENTER };
        handle.add_css_class ("energy-handle");
        handles.add_widget (handle);
        if (state != "monitored" || !live) handle.add_css_class ("energy-handle-off");
        else if (circuit != null) {
            string load_state = string_member (circuit, "load_state");
            if (load_state == "warn" || load_state == "danger") handle.add_css_class ("energy-handle-" + load_state);
        }
        var content = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 8) { margin_start = 8, margin_end = 8 };
        if (left) { content.append (info); content.append (handle); } else { content.append (handle); content.append (info); }
        var button = new Gtk.Button () { child = content, has_frame = false, height_request = height, hexpand = true, sensitive = state == "monitored" };
        button.add_css_class ("energy-breaker");
        if (state != "monitored") button.add_css_class ("energy-breaker-idle");
        if (circuit != null && live) {
            string usage_state = string_member (circuit, "usage_state");
            if (usage_state == "heat" || usage_state == "high") button.add_css_class ("energy-" + usage_state);
        }
        string spoken = state == "monitored"
            ? "Slot %d, %s, %s. View recorded history".printf (slot, display, live ? metric (circuit, "watts", "%.0f watts") : "no live reading")
            : (state == "unmonitored" ? "Slot %d, %s, unmonitored".printf (slot, display) : "Slot %d, empty".printf (slot));
        button.update_property (Gtk.AccessibleProperty.LABEL, spoken, -1);
        if (state == "monitored") {
            button.tooltip_text = "Slot %d · %s".printf (slot, display);
            button.clicked.connect (() => { launch ({"--history", channel}); });
        }
        return button;
    }

    // A panel as it hangs on the wall: odd slots left, even slots right, a centre bus labelled 1–2, 3–4 …;
    // a double-pole breaker takes two rows on its side.
    private Gtk.Widget build_panel (Json.Array slots, bool live, Json.Object root) {
        int total = root.has_member ("panel_slots") ? (int) root.get_int_member ("panel_slots") : 0;
        for (uint i = 0; i < uint.min (slots.get_length (), 96); i++) {
            int slot = (int) slots.get_object_element (i).get_int_member ("slot");
            if (slot > 0 && slot <= 96) total = int.max (total, slot);
        }
        total = int.min (int.max (total, 2), 96);
        if (total % 2 == 1) total++;
        Json.Object?[] by_slot = new Json.Object?[total + 3];
        for (uint i = 0; i < uint.min (slots.get_length (), 96); i++) {
            var circuit = slots.get_object_element (i);
            int slot = (int) circuit.get_int_member ("slot");
            if (slot > 0 && slot <= total) by_slot[slot] = circuit;
        }
        bool[] covered = new bool[total + 3];
        var handles = new Gtk.SizeGroup (Gtk.SizeGroupMode.HORIZONTAL);
        var sides = new Gtk.SizeGroup (Gtk.SizeGroupMode.HORIZONTAL);
        var left_column = new Gtk.Box (Gtk.Orientation.VERTICAL, 4) { hexpand = true };
        var right_column = new Gtk.Box (Gtk.Orientation.VERTICAL, 4) { hexpand = true };
        var bus = new Gtk.Box (Gtk.Orientation.VERTICAL, 4) { width_request = 44 };
        sides.add_widget (left_column);
        sides.add_widget (right_column);
        for (int slot = 1; slot <= total; slot++) {
            var circuit = by_slot[slot];
            bool is_left = slot % 2 == 1;
            if (covered[slot]) continue;
            int span = 1;
            if (circuit != null && circuit.has_member ("poles") && !circuit.get_member ("poles").is_null ()
                    && circuit.get_int_member ("poles") == 2 && slot + 2 <= total) {
                var below = by_slot[slot + 2];
                if (below == null || string_member (below, "slot_state", "empty") == "empty") { span = 2; covered[slot + 2] = true; }
            }
            (is_left ? left_column : right_column).append (breaker_cell (circuit, slot, is_left, live, span, handles));
        }
        for (int row = 0; row < total / 2; row++) {
            var label = new Gtk.Label ("%d–%d".printf (row * 2 + 1, row * 2 + 2)) { height_request = compact ? 26 : 46, justify = Gtk.Justification.CENTER };
            label.add_css_class ("energy-bus-label");
            label.add_css_class (Granite.CssClass.NUMERIC);
            bus.append (label);
        }
        bus.add_css_class ("energy-bus");
        var panel = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 0);
        panel.add_css_class ("energy-panel");
        panel.append (left_column);
        panel.append (bus);
        panel.append (right_column);
        return panel;
    }

    private void update () {
        refresh_density ();
        bool showing_temperatures = current_view == 1;
        int screen_height = 900, screen_width = 1200;
        var monitor = Gdk.Display.get_default ().get_monitors ().get_item (0) as Gdk.Monitor;
        if (monitor != null) { var bounds = monitor.get_geometry (); screen_height = bounds.height; screen_width = bounds.width; }
        // One width for both views so switching never moves or resizes the popover.
        menu.width_request = int.min (compact ? MENU_WIDTH_COMPACT : MENU_WIDTH, screen_width - 32);
        house_preview.set_view_height (int.min (440, int.max (300, screen_height * 38 / 100)));
        scroll.max_content_height = showing_temperatures ? int.max (360, screen_height - 240) : 440;
        try {
            string text;
            FileUtils.get_contents (Environment.get_user_state_dir () + "/energy-monitor/summary.json", out text);
            if (text.length > 4 * 1024 * 1024) return;
            var parser = new Json.Parser ();
            parser.load_from_data (text);
            var root = parser.get_root ().get_object ();
            var age = root.has_member ("fetched_at") ?
                new DateTime.now_utc ().to_unix () - root.get_double_member ("fetched_at") : 1e10;
            bool live = root.has_member ("online") && root.get_boolean_member ("online") && age >= 0 && age <= 180;
            string fingerprint = text + (live ? "live" : "offline") + (compact ? "compact" : "roomy");
            if (previous == fingerprint) return;
            previous = fingerprint;
            panel_title.label = string_member (root, "panel_label", "Energy Monitor");
            power.label = live ? metric (root, "current_watts", "%.0f W") : "";
            set_live (live);
            string stamp = string_member (root, "last_reading");
            int separator = stamp.index_of ("T");
            if (separator >= 0 && stamp.length >= separator + 6) stamp = stamp.substring (separator + 1, 5);
            status.tooltip_text = string_member (root, "last_reading", "No recorded reading");
            status.label = live ? "Live · Updated " + stamp :
                string_member (root, "connection", "Collector unavailable") + " · " + string_member (root, "last_reading", "No cached readings");
            if (!live && status.label.has_prefix ("Connected")) status.label = "Collector readings are stale";
            // The temperature view stays parented so its expanders, sort and 3D camera survive refreshes.
            var child = readings.get_first_child ();
            while (child != null) {
                var next = child.get_next_sibling ();
                if (!(showing_temperatures && child == temperature_view)) readings.remove (child);
                child = next;
            }
            if (showing_temperatures) {
                house_preview.set_state (root, age >= 0 && age <= 180);
                temperature_view.set_state (root, age >= 0 && age <= 180);
                if (temperature_view.parent == null) readings.append (temperature_view);
                return;
            }
            // Hero: the number first, with its meaning and cost beside it (no frame; the menu is the surface).
            var hero = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 12) { hexpand = true, margin_start = 6, margin_end = 6, margin_top = 3 };
            hero.add_css_class ("energy-hero");
            hero.add_css_class (live ? "energy-hero-live" : "energy-hero-stale");
            hero.append (new Gtk.Image.from_icon_name ("weather-storm-symbolic") { pixel_size = 32, valign = Gtk.Align.CENTER });
            var number = new Gtk.Label (live ? metric (root, "current_watts", "%.0f W") : "—") { xalign = 0, hexpand = true };
            number.add_css_class ("energy-value");
            hero.append (number);
            var side = new Gtk.Box (Gtk.Orientation.VERTICAL, 0) { valign = Gtk.Align.CENTER, halign = Gtk.Align.END };
            var cost = new Gtk.Label (live ? metric (root, "cost_per_hour", "$%.3f / hour") : "Not live") { xalign = 1 };
            cost.add_css_class (Granite.CssClass.NUMERIC);
            var hero_caption = new Gtk.Label ("Power · minute average") { xalign = 1 };
            hero_caption.add_css_class (Granite.CssClass.DIM);
            hero_caption.add_css_class (Granite.CssClass.SMALL);
            side.append (cost);
            side.append (hero_caption);
            hero.append (side);
            readings.append (hero);
            // Three plain stat tiles in one row.
            string month_detail = "";
            if (root.has_member ("month_days_recorded") && !root.get_member ("month_days_recorded").is_null ()) {
                int month_days = (int) root.get_int_member ("month_days_recorded");
                month_detail = "%d day%s".printf (month_days, month_days == 1 ? "" : "s");
            }
            var stats = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 6) { homogeneous = true };
            stats.append (metric_card ("Energy · 24 h", metric (root, "recorded_kwh", "%.2f kWh")));
            stats.append (metric_card ("Cost · 24 h", metric (root, "cost_24h", "$%.2f")));
            stats.append (metric_card ("Month", metric (root, "month_cost", "$%.2f"), month_detail));
            readings.append (stats);
            if (root.has_member ("breaker_slots")) {
                var slots = root.get_array_member ("breaker_slots");
                // Monitored circuits as one list, busiest first; the slot number stays in front of the name.
                var items = new Json.Object[0];
                int unassigned = 0;
                for (uint i = 0; i < uint.min (slots.get_length (), 96); i++) {
                    var circuit = slots.get_object_element (i);
                    int slot = (int) circuit.get_int_member ("slot");
                    if (slot <= 0 || slot > 96) continue;
                    if (string_member (circuit, "channel_name") == "") { unassigned++; continue; }
                    items += circuit;
                }
                for (int i = 1; i < items.length; i++) {
                    var key = items[i];
                    double key_watts = (live && key.has_member ("watts") && !key.get_member ("watts").is_null ()) ? key.get_double_member ("watts") : -1;
                    int j = i - 1;
                    while (j >= 0) {
                        double other = (live && items[j].has_member ("watts") && !items[j].get_member ("watts").is_null ()) ? items[j].get_double_member ("watts") : -1;
                        if (other >= key_watts) break;
                        items[j + 1] = items[j];
                        j--;
                    }
                    items[j + 1] = key;
                }
                var section = new Granite.HeaderLabel ("Circuits") { hexpand = true };
                var layout_switch = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 0) { homogeneous = true, valign = Gtk.Align.CENTER };
                layout_switch.add_css_class ("linked");
                layout_switch.add_css_class ("energy-switcher");
                var panel_toggle = new Gtk.ToggleButton.with_label ("Panel") { active = prefs.circuits_panel, tooltip_text = "Show circuits as the physical breaker panel" };
                var list_toggle = new Gtk.ToggleButton.with_label ("List") { active = !prefs.circuits_panel, group = panel_toggle, tooltip_text = "Show circuits as a list, busiest first" };
                layout_switch.append (panel_toggle);
                layout_switch.append (list_toggle);
                panel_toggle.toggled.connect (() => {
                    if (prefs.circuits_panel == panel_toggle.active) return;
                    prefs.circuits_panel = panel_toggle.active; prefs.save (); previous = ""; update ();
                });
                var section_row = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 6) { margin_top = 12 };
                section_row.append (section);
                section_row.append (layout_switch);
                readings.append (section_row);
                if (prefs.circuits_panel) {
                    readings.append (build_panel (slots, live, root));
                    return;
                }
                var list = new Gtk.ListBox () { selection_mode = Gtk.SelectionMode.NONE, activate_on_single_click = true };
                list.add_css_class ("boxed-list");
                list.add_css_class ("energy-circuits");
                foreach (var circuit in items) {
                    var channel = string_member (circuit, "channel_name");
                    int slot = (int) circuit.get_int_member ("slot");
                    string display = string_member (circuit, "display_name", channel);
                    var line = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 8) { margin_top = compact ? 2 : 6, margin_bottom = compact ? 2 : 6, margin_start = 9, margin_end = 9 };
                    var number_label = new Gtk.Label ("%02d".printf (slot)) { width_chars = 2, xalign = 1 };
                    number_label.add_css_class (Granite.CssClass.DIM);
                    number_label.add_css_class (Granite.CssClass.NUMERIC);
                    line.append (number_label);
                    var names = new Gtk.Box (Gtk.Orientation.VERTICAL, 3) { hexpand = true, valign = Gtk.Align.CENTER };
                    var name_row = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 4);
                    name_row.append (new Gtk.Label (display) { xalign = 0, ellipsize = Pango.EllipsizeMode.END, max_width_chars = 22 });
                    if (circuit.has_member ("is_peak") && !circuit.get_member ("is_peak").is_null () && circuit.get_boolean_member ("is_peak")) {
                        var star = new Gtk.Image.from_icon_name ("starred-symbolic") { pixel_size = 12 };
                        star.add_css_class ("energy-peak");
                        star.tooltip_text = "Top usage";
                        star.update_property (Gtk.AccessibleProperty.LABEL, "Top usage", -1);
                        name_row.append (star);
                    }
                    names.append (name_row);
                    if (!compact && live && circuit.has_member ("load_percent") && !circuit.get_member ("load_percent").is_null ()) {
                        var bar = new Gtk.ProgressBar () { fraction = double.min (double.max (circuit.get_double_member ("load_percent") / 100.0, 0.0), 1.0) };
                        bar.add_css_class ("energy-load-bar");
                        string load_state = string_member (circuit, "load_state");
                        if (load_state == "warn" || load_state == "danger") bar.add_css_class ("energy-" + load_state);
                        names.append (bar);
                    }
                    line.append (names);
                    var values = new Gtk.Box (Gtk.Orientation.VERTICAL, 0) { valign = Gtk.Align.CENTER, halign = Gtk.Align.END };
                    var watts = new Gtk.Label (live ? metric (circuit, "watts", "%.0f W") : "—") { xalign = 1 };
                    watts.add_css_class ("energy-circuit-value");
                    values.append (watts);
                    if (!compact && circuit.has_member ("amps") && !circuit.get_member ("amps").is_null ()) {
                        int poles = circuit.has_member ("poles") ? (int) circuit.get_int_member ("poles") : 1;
                        var rating = new Gtk.Label ("%dP · %d A".printf (poles, (int) circuit.get_int_member ("amps"))) { xalign = 1 };
                        rating.add_css_class (Granite.CssClass.DIM);
                        rating.add_css_class (Granite.CssClass.SMALL);
                        values.append (rating);
                    }
                    line.append (values);
                    line.append (new Gtk.Image.from_icon_name ("go-next-symbolic") { pixel_size = 12, opacity = 0.5 });
                    var row = new Gtk.ListBoxRow () { child = line, activatable = true, tooltip_text = "View recorded history for " + display };
                    string usage_state = string_member (circuit, "usage_state");
                    if (usage_state == "heat" || usage_state == "high") row.add_css_class ("energy-" + usage_state);
                    row.update_property (Gtk.AccessibleProperty.LABEL, "%s, %s. View recorded history".printf (
                        display, live ? metric (circuit, "watts", "%.0f watts") : "no live reading"), -1);
                    row.set_data<string> ("energy-channel", channel);
                    list.append (row);
                }
                list.row_activated.connect ((row) => {
                    string? target = row.get_data<string> ("energy-channel");
                    if (target != null) launch ({"--history", target});
                });
                readings.append (list);
                if (items.length == 0) {
                    var none = new Gtk.Label ("No circuits are assigned yet. Assign them in Collector Settings.") { xalign = 0, wrap = true, margin_start = 6 };
                    none.add_css_class (Granite.CssClass.DIM);
                    readings.append (none);
                }
                if (unassigned > 0) {
                    var footer = new Gtk.Label ("%d unassigned or unmonitored slot%s".printf (unassigned, unassigned == 1 ? "" : "s")) { xalign = 0, margin_start = 6 };
                    footer.add_css_class (Granite.CssClass.DIM);
                    footer.add_css_class (Granite.CssClass.SMALL);
                    readings.append (footer);
                }
            }
        } catch (Error e) {
            power.label = "";
            set_live (false);
            status.label = "Collector connection required";
        }
    }

    public override void opened () { update (); }
    public override void closed () {}
    public override Gtk.Widget get_display_widget () { return display_box; }
    public override Gtk.Widget? get_widget () { return menu; }
}

public Wingpanel.Indicator? get_indicator (Module module, Wingpanel.ServerType type) {
    if (type != Wingpanel.ServerType.SESSION) return null;
    return new EnergyIndicator ();
}
