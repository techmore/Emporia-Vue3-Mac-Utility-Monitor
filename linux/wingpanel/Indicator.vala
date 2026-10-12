/* SPDX-License-Identifier: MIT */
public class EnergyIndicator : Wingpanel.Indicator {
    private Gtk.Box display_box;
    private Gtk.Label power;
    private Gtk.Box menu;
    private Gtk.Box readings;
    private Gtk.Label status;
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
        menu = new Gtk.Box (Gtk.Orientation.VERTICAL, 6) {
            margin_top = 12, margin_bottom = 6, margin_start = 12, margin_end = 12,
            width_request = 320
        };
        menu.add_css_class ("energy-monitor");
        menu.add_css_class ("energy-menu");
        css = new Gtk.CssProvider ();
        Gtk.StyleContext.add_provider_for_display (Gdk.Display.get_default (), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION);
        load_theme ();
        Gtk.Settings.get_default ().notify["gtk-application-prefer-dark-theme"].connect (() => { load_theme (); });
        Gtk.Settings.get_default ().notify["gtk-theme-name"].connect (() => { load_theme (); });
        var title = new Gtk.Label ("Energy Monitor") { xalign = 0 };
        title.add_css_class ("energy-title");
        status = new Gtk.Label ("Waiting for collector connection") { xalign = 0, wrap = true };
        status.add_css_class ("energy-muted");
        menu.append (title);
        menu.append (status);
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

    private Gtk.Widget metric_card (string name, string value, bool hero = false) {
        var box = new Gtk.Box (Gtk.Orientation.VERTICAL, 3) { hexpand = true };
        var caption = new Gtk.Label (name) { xalign = 0 };
        caption.add_css_class ("energy-caption");
        box.append (caption);
        var number = new Gtk.Label (value) { xalign = 0 };
        number.add_css_class ("energy-value");
        box.append (number);
        box.add_css_class (hero ? "energy-hero" : "energy-card");
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
            power.label = live ? metric (root, "current_watts", "%.0f W") : "";
            status.label = live ? "Live · " + string_member (root, "last_reading") :
                string_member (root, "connection", "Collector unavailable") + "\n" + string_member (root, "last_reading", "No cached readings");
            if (!live && status.label.has_prefix ("Connected")) status.label = "Collector readings are stale";
            while (readings.get_first_child () != null) readings.remove (readings.get_first_child ());
            var hero = (Gtk.Box) metric_card ("MINUTE-AVERAGE POWER", live ? metric (root, "current_watts", "%.0f W") : "—", true);
            var cost = new Gtk.Label (live ? metric (root, "cost_per_hour", "$%.3f / hour") : "Live readings unavailable") { xalign = 0 };
            cost.add_css_class ("energy-caption");
            hero.append (cost);
            readings.append (hero);
            var grid = new Gtk.Grid () { column_spacing = 6, row_spacing = 6, column_homogeneous = true };
            grid.attach (metric_card ("RECORDED · 24 HOURS", metric (root, "recorded_kwh", "%.2f kWh")), 0, 0);
            grid.attach (metric_card ("COST · 24 HOURS", metric (root, "cost_24h", "$%.2f")), 1, 0);
            grid.attach (metric_card ("RECORDED MONTH COST", metric (root, "month_cost", "$%.2f")), 0, 1, 2);
            readings.append (grid);
            if (root.has_member ("breaker_slots")) {
                var section = new Gtk.Label ("CIRCUITS") { xalign = 0, margin_top = 8, margin_bottom = 2 };
                section.add_css_class ("energy-section");
                readings.append (section);
                var slots = root.get_array_member ("breaker_slots");
                for (uint i = 0; i < uint.min (slots.get_length (), 96); i++) {
                    var circuit = slots.get_object_element (i);
                    var channel = string_member (circuit, "channel_name");
                    if (channel == "") continue;
                    var box = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 8);
                    box.append (new Gtk.Label ("%02d  %s".printf ((int) circuit.get_int_member ("slot"),
                        string_member (circuit, "display_name", channel))) {
                        xalign = 0, hexpand = true, ellipsize = Pango.EllipsizeMode.END, max_width_chars = 23
                    });
                    var watts = new Gtk.Label (live ? metric (circuit, "watts", "%.0f W") : "—") { xalign = 1 };
                    watts.add_css_class ("energy-circuit-value");
                    box.append (watts);
                    var rating = new Gtk.Label (metric (circuit, "amps", "%.0f A")) { width_chars = 4, xalign = 1 };
                    rating.add_css_class ("energy-muted");
                    box.append (rating);
                    var arrow = new Gtk.Image.from_icon_name ("go-next-symbolic") { pixel_size = 12 };
                    box.append (arrow);
                    var button = new Gtk.Button () { child = box, has_frame = false };
                    button.add_css_class ("energy-circuit");
                    button.tooltip_text = "View recorded history for " + string_member (circuit, "display_name", channel);
                    string load_state = string_member (circuit, "load_state");
                    if (load_state == "warn" || load_state == "danger") button.add_css_class ("energy-" + load_state);
                    button.clicked.connect (() => { launch ({"--history", channel}); });
                    readings.append (button);
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
