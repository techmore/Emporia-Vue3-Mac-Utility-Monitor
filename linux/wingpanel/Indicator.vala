/* SPDX-License-Identifier: MIT */
public class EnergyIndicator : Wingpanel.Indicator {
    private Gtk.Box display_box;
    private Gtk.Label power;
    private Gtk.Box menu;
    private Gtk.Box readings;
    private Gtk.Label status;
    private Gtk.Label panel_title;
    private uint timer;
    private string previous = "";
    private Gtk.CssProvider css;

    public EnergyIndicator () {
        Object (code_name: "energy-monitor", visible: true);
        display_box = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 3);
        display_box.append (new Gtk.Image.from_icon_name ("weather-storm-symbolic"));
        power = new Gtk.Label ("");
        display_box.append (power);
        display_box.tooltip_text = "Energy Monitor";
        menu = new Gtk.Box (Gtk.Orientation.VERTICAL, 3) {
            margin_top = 3, margin_bottom = 3, margin_start = 6, margin_end = 6,
            width_request = 390
        };
        menu.add_css_class ("energy-monitor");
        menu.add_css_class ("energy-menu");
        css = new Gtk.CssProvider ();
        Gtk.StyleContext.add_provider_for_display (Gdk.Display.get_default (), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION);
        load_theme ();
        Gtk.Settings.get_default ().notify["gtk-application-prefer-dark-theme"].connect (() => { load_theme (); });
        Gtk.Settings.get_default ().notify["gtk-theme-name"].connect (() => { load_theme (); });
        panel_title = new Gtk.Label ("Energy Monitor") { xalign = 0, hexpand = true };
        panel_title.add_css_class ("energy-title");
        status = new Gtk.Label ("Waiting for collector connection") {
            xalign = 1, ellipsize = Pango.EllipsizeMode.END, max_width_chars = 30
        };
        status.add_css_class ("energy-muted");
        status.add_css_class ("energy-timestamp");
        var header = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 8);
        header.append (panel_title);
        header.append (status);
        menu.append (header);
        readings = new Gtk.Box (Gtk.Orientation.VERTICAL, 4);
        var scroll = new Gtk.ScrolledWindow () {
            child = readings, hscrollbar_policy = Gtk.PolicyType.NEVER,
            max_content_height = 440, propagate_natural_height = true
        };
        menu.append (scroll);
        menu.append (new Gtk.Separator (Gtk.Orientation.HORIZONTAL));
        add_action ("Dashboard", {});
        add_action ("Collector Settings…", {"--settings"});
        add_action ("Collector Connection…", {"--connection"});
        add_action ("Refresh", {"--refresh"});
        add_action ("Quit", {"--quit"});
        update ();
        timer = Timeout.add_seconds (3, () => { update (); return Source.CONTINUE; });
        display_box.destroy.connect (() => { if (timer != 0) Source.remove (timer); });
    }

    private void load_theme () {
        var settings = Gtk.Settings.get_default ();
        bool dark = settings.gtk_application_prefer_dark_theme || settings.gtk_theme_name.down ().contains ("dark");
        css.load_from_resource (dark ? "/org/techmore/energy/theme-dark.css" : "/org/techmore/energy/theme-light.css");
    }

    private void launch (string[] options) {
        string[] args = {Environment.get_home_dir () + "/.local/bin/energy-monitor"};
        foreach (var option in options) args += option;
        try { Pid pid; Process.spawn_async (null, args, null, SpawnFlags.SEARCH_PATH, null, out pid); }
        catch (Error e) { status.label = "Could not open Energy Monitor"; warning ("%s", e.message); }
    }

    private void add_action (string label, string[] options) {
        var button = new Wingpanel.PopoverMenuItem () { text = label };
        button.clicked.connect (() => { launch (options); });
        menu.append (button);
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

    private void update () {
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
            string fingerprint = text + (live ? "live" : "offline");
            if (previous == fingerprint) return;
            previous = fingerprint;
            panel_title.label = string_member (root, "panel_label", "Energy Monitor");
            power.label = live ? metric (root, "current_watts", "%.0f W") : "";
            string stamp = string_member (root, "last_reading");
            int separator = stamp.index_of ("T");
            if (separator >= 0 && stamp.length >= separator + 6) stamp = stamp.substring (separator + 1, 5);
            status.tooltip_text = string_member (root, "last_reading", "No recorded reading");
            status.label = live ? "Live · Updated " + stamp :
                string_member (root, "connection", "Collector unavailable") + " · " + string_member (root, "last_reading", "No cached readings");
            if (!live && status.label.has_prefix ("Connected")) status.label = "Collector readings are stale";
            while (readings.get_first_child () != null) readings.remove (readings.get_first_child ());
            var hero = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 10) { hexpand = true };
            hero.add_css_class ("energy-hero");
            var number = new Gtk.Label (live ? metric (root, "current_watts", "%.0f W") : "—") { xalign = 0 };
            number.add_css_class ("energy-value");
            hero.append (number);
            var side = new Gtk.Box (Gtk.Orientation.VERTICAL, 0) { valign = Gtk.Align.CENTER, hexpand = true };
            var hero_caption = new Gtk.Label ("MINUTE-AVERAGE POWER") { xalign = 0 };
            hero_caption.add_css_class ("energy-caption");
            side.append (hero_caption);
            var cost = new Gtk.Label (live ? metric (root, "cost_per_hour", "$%.3f / hour") : "Live readings unavailable") { xalign = 0 };
            cost.add_css_class ("energy-caption");
            side.append (cost);
            hero.append (side);
            readings.append (hero);
            var grid = new Gtk.Grid () { column_spacing = 6, row_spacing = 6, column_homogeneous = true };
            grid.attach (metric_card ("RECORDED · 24 HOURS", metric (root, "recorded_kwh", "%.2f kWh")), 0, 0);
            grid.attach (metric_card ("COST · 24 HOURS", metric (root, "cost_24h", "$%.2f")), 1, 0);
            string month_detail = "";
            if (root.has_member ("month_days_recorded") && !root.get_member ("month_days_recorded").is_null ()) {
                int month_days = (int) root.get_int_member ("month_days_recorded");
                month_detail = "%d day%s recorded".printf (month_days, month_days == 1 ? "" : "s");
            }
            grid.attach (metric_card ("RECORDED MONTH COST", metric (root, "month_cost", "$%.2f"), month_detail), 0, 1, 2);
            readings.append (grid);
            if (root.has_member ("breaker_slots")) {
                var section = new Gtk.Label ("CIRCUITS") { xalign = 0, margin_top = 5, margin_bottom = 1 };
                section.add_css_class ("energy-section");
                readings.append (section);
                var slots = root.get_array_member ("breaker_slots");
                var panel = new Gtk.Grid () { column_homogeneous = true, column_spacing = 5, row_spacing = 2 };
                readings.append (panel);
                // Slot coordinates preserve physical odd-left/even-right positions,
                // including gaps: never pack only monitored circuits sequentially.
                int last_slot = root.has_member ("panel_slots") ? (int) root.get_int_member ("panel_slots") : 0;
                for (uint i = 0; i < uint.min (slots.get_length (), 96); i++) {
                    int slot = (int) slots.get_object_element (i).get_int_member ("slot");
                    if (slot > 0 && slot <= 96) last_slot = int.max (last_slot, slot);
                }
                last_slot = int.min (int.max (last_slot, 0), 96);
                for (int slot = 1; slot <= last_slot; slot++) {
                    string slot_state = "empty";
                    for (uint j = 0; j < uint.min (slots.get_length (), 96); j++) {
                        var candidate = slots.get_object_element (j);
                        if ((int) candidate.get_int_member ("slot") == slot) {
                            slot_state = string_member (candidate, "slot_state", "empty");
                            break;
                        }
                    }
                    string empty_text = slot_state == "unmonitored"
                        ? "%02d  ·  Unmonitored".printf (slot)
                        : "%02d  ·  Unassigned".printf (slot);
                    var empty = new Gtk.Label (empty_text) { xalign = 0, hexpand = true };
                    empty.add_css_class ("energy-muted");
                    empty.add_css_class ("energy-empty-slot");
                    panel.attach (empty, (slot - 1) % 2, (slot - 1) / 2);
                }
                for (uint i = 0; i < uint.min (slots.get_length (), 96); i++) {
                    var circuit = slots.get_object_element (i);
                    var channel = string_member (circuit, "channel_name");
                    int slot = (int) circuit.get_int_member ("slot");
                    if (slot <= 0 || slot > 96) continue;
                    if (channel == "") continue;
                    var card = new Gtk.Box (Gtk.Orientation.VERTICAL, 1);
                    var box = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 4);
                    box.append (new Gtk.Label ("%02d  %s".printf (slot,
                        string_member (circuit, "display_name", channel))) {
                        xalign = 0, hexpand = true, ellipsize = Pango.EllipsizeMode.END, max_width_chars = 14
                    });
                    if (circuit.has_member ("is_peak") && !circuit.get_member ("is_peak").is_null ()
                            && circuit.get_boolean_member ("is_peak")) {
                        var star = new Gtk.Image.from_icon_name ("starred-symbolic") { pixel_size = 10 };
                        star.add_css_class ("energy-peak");
                        star.tooltip_text = "Top usage";
                        box.append (star);
                    }
                    card.append (box);
                    var detail = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 6);
                    var watts = new Gtk.Label (live ? metric (circuit, "watts", "%.0f W") : "—") { xalign = 0, hexpand = true };
                    watts.add_css_class ("energy-circuit-value");
                    detail.append (watts);
                    string rating_text = "—";
                    if (circuit.has_member ("amps") && !circuit.get_member ("amps").is_null ()) {
                        int poles = circuit.has_member ("poles") ? (int) circuit.get_int_member ("poles") : 1;
                        rating_text = "%dP/%dA".printf (poles, (int) circuit.get_int_member ("amps"));
                    }
                    var rating = new Gtk.Label (rating_text) { width_chars = 6, xalign = 1 };
                    rating.add_css_class ("energy-muted");
                    detail.append (rating);
                    var arrow = new Gtk.Image.from_icon_name ("go-next-symbolic") { pixel_size = 12 };
                    box.append (arrow);
                    card.append (detail);
                    var button = new Gtk.Button () { child = card, has_frame = false, hexpand = true };
                    button.add_css_class ("energy-circuit");
                    button.tooltip_text = "View recorded history for " + string_member (circuit, "display_name", channel);
                    string load_state = string_member (circuit, "load_state");
                    if (load_state == "warn" || load_state == "danger") button.add_css_class ("energy-" + load_state);
                    string usage_state = string_member (circuit, "usage_state");
                    if (usage_state == "heat" || usage_state == "high") button.add_css_class ("energy-" + usage_state);
                    if (live && circuit.has_member ("load_percent") && !circuit.get_member ("load_percent").is_null ()) {
                        double percent = circuit.get_double_member ("load_percent");
                        var bar = new Gtk.ProgressBar ();
                        bar.fraction = double.min (double.max (percent / 100.0, 0.0), 1.0);
                        bar.add_css_class ("energy-load-bar");
                        card.append (bar);
                    }
                    button.clicked.connect (() => { launch ({"--history", channel}); });
                    var placeholder = panel.get_child_at ((slot - 1) % 2, (slot - 1) / 2);
                    if (placeholder != null) panel.remove (placeholder);
                    panel.attach (button, (slot - 1) % 2, (slot - 1) / 2);
                }
            }
        } catch (Error e) {
            power.label = "";
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
