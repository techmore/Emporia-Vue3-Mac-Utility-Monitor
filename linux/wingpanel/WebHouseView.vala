/* SPDX-License-Identifier: MIT */
// Offline WebKit host for the exact scene builder used by Home 3D.
public class WebHouseView : Gtk.Box {
    private WebKit.WebView web;
    private Json.Object? state;
    private bool ready;
    public signal void room_selected (string room);
    public signal void sensor_selected (string source, string identity);

    public WebHouseView () {
        Object (orientation: Gtk.Orientation.VERTICAL);
        web = (WebKit.WebView) GLib.Object.new (typeof (WebKit.WebView), "network-session", new WebKit.NetworkSession.ephemeral ());
        web.hexpand = true; web.vexpand = true;
        var manager = web.get_user_content_manager ();
        manager.register_script_message_handler ("history", null);
        manager.script_message_received["history"].connect ((value) => {
            if (value.is_string ()) room_selected (value.to_string ());
        });
        manager.register_script_message_handler ("sensor", null);
        manager.script_message_received["sensor"].connect ((value) => {
            if (!value.is_string () || state == null || !state.has_member ("temperature_sensors")) return;
            try {
                var parser = new Json.Parser (); parser.load_from_data (value.to_string ());
                var identity = parser.get_root ().get_object ();
                if (!identity.has_member ("source") || !identity.has_member ("sensor_id")) return;
                var sensors = state.get_array_member ("temperature_sensors");
                for (uint i = 0; i < sensors.get_length (); i++) {
                    var sensor = sensors.get_object_element (i);
                    if (sensor.get_string_member ("source") == identity.get_string_member ("source") &&
                        sensor.get_string_member ("sensor_id") == identity.get_string_member ("sensor_id")) {
                        sensor_selected (sensor.get_string_member ("source"), sensor.get_string_member ("sensor_id")); return;
                    }
                }
            } catch (Error error) { warning ("Temperature selection: %s", error.message); }
        });
        web.get_settings ().enable_webgl = true;
        web.load_changed.connect ((event) => {
            if (event == WebKit.LoadEvent.FINISHED) { ready = true; publish (); }
        });
        append (web);
        try {
            var bytes = GLib.resources_lookup_data ("/org/techmore/energy/native-house.js", ResourceLookupFlags.NONE);
            string script = ((string) bytes.get_data ()).replace ("</script", "<\\/script");
            string html = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:">
<style>
:root{color-scheme:light dark;--surface:#f4f1e8;--text-light:#777c65;--olive-600:#6e7755;--temperature-cold:#83b6ef;--temperature-comfortable:#ead063;--temperature-warm:#d7a55e;--temperature-hot:#ed8c80}
*{box-sizing:border-box}body{height:100vh;display:flex;flex-direction:column;overflow:hidden;margin:0;background:var(--surface);color:#252a20;font:13px system-ui}header{flex-shrink:0;display:flex;gap:8px;padding:6px 10px;align-items:center}select,button{font:inherit;padding:5px 10px;border:1px solid #8888;border-radius:6px;background:var(--surface);color:inherit}select{flex:1;min-width:100px}header label{display:flex;align-items:center;gap:5px;font-size:12px}input[type=range]{width:75px}#stage{position:relative;flex:1;height:auto;min-height:180px;overflow:hidden}canvas{width:100%;height:100%;display:block;touch-action:none;background:var(--surface)}#labels{position:absolute;inset:0;pointer-events:none;overflow:hidden}.home3d-label{position:absolute;transform:translate(-50%,-50%);background:#fffffff0;color:#252a20;border:1px solid #7777;border-radius:6px;padding:4px 7px;font-size:12px;text-align:center;white-space:normal;width:max-content;max-width:170px;box-shadow:0 2px 5px #0002}.home3d-label[hidden]{display:none}.home3d-sensor-marker{font-size:11px;border-color:#a58a38}.stale{opacity:.6}#status{font-size:11px;padding:4px 10px}details{padding:8px 12px;flex-shrink:0}details[open]{max-height:45vh;overflow-y:auto}#summary,#legend,#status{flex-shrink:0}#rooms>div{display:flex;justify-content:space-between;gap:16px;padding:5px 0;border-bottom:1px solid #8884}#rooms strong{white-space:nowrap;font-size:12px}
.temperature-leaders{position:absolute;inset:0;width:100%;height:100%;overflow:visible}.temperature-leaders line{stroke:#69725c;stroke-width:1.5}.temperature-leaders circle{fill:#f4f1e8;stroke:#69725c;stroke-width:1.5}.home3d-sensor-marker{transform:none;pointer-events:auto;cursor:pointer;display:flex;flex-direction:column;min-width:84px;max-width:116px;text-align:left;padding:4px 7px;border-left:3px solid var(--text-light);z-index:1;opacity:1}.badge-room{font-size:10px;line-height:1.3}.home3d-sensor-marker strong{font-size:18px;line-height:1.15;font-variant-numeric:tabular-nums;letter-spacing:-.5px}.home3d-sensor-marker small{font-size:9px;color:#68705e}.home3d-sensor-marker[data-band=cold],.sensor-row[data-band=cold]{border-left-color:var(--temperature-cold)}.home3d-sensor-marker[data-band=comfortable],.sensor-row[data-band=comfortable]{border-left-color:var(--temperature-comfortable)}.home3d-sensor-marker[data-band=warm],.sensor-row[data-band=warm]{border-left-color:var(--temperature-warm)}.home3d-sensor-marker[data-band=hot],.sensor-row[data-band=hot]{border-left-color:var(--temperature-hot)}.home3d-sensor-marker[data-status=stale]{border-style:dashed}.home3d-sensor-marker:focus-visible,.sensor-row:focus-visible{outline:2px solid var(--olive-600);outline-offset:2px}#summary{padding:3px 10px 5px;display:flex;justify-content:space-between;gap:12px;align-items:center}#overview{font-weight:600;font-size:12px;font-variant-numeric:tabular-nums}#counts{font-size:11px;color:#68705e;max-width:52%;text-align:right;line-height:1.3}#legend{font-size:10px;padding:2px 10px;color:#68705e}#rooms>button.sensor-row{width:100%;display:flex;justify-content:space-between;gap:12px;text-align:left;border:0;border-left:4px solid var(--text-light);border-bottom:1px solid #8884;border-radius:0;padding:8px}.sensor-names,.sensor-reading{display:flex;flex-direction:column;gap:3px}.sensor-names{min-width:0}.sensor-reading{text-align:right;flex-shrink:0}.sensor-reading strong{font-size:17px;font-variant-numeric:tabular-nums}.sensor-row small{font-size:11px;color:#68705e}
.home3d-sensor-marker[data-ice=true]{background:linear-gradient(180deg,#d9efff 0%,#e9f6ff 65%,#fff 94%);color:#183c57;border-color:#94c9ec;border-left-color:#70b3e3;box-shadow:0 3px 10px #306b9930,inset 0 -10px 13px #ffffffd9;overflow:hidden}.home3d-sensor-marker[data-ice=true] small{color:#426580}.home3d-sensor-marker[data-ice=true]>*{position:relative;z-index:1}.home3d-sensor-marker[data-ice=true]::after{z-index:0;content:"";position:absolute;pointer-events:none;bottom:0;left:0;right:0;height:8px;background:linear-gradient(150deg,transparent 15%,#fff 16% 34%,transparent 35%) 0 0/14px 10px,linear-gradient(20deg,#ffffffcc,transparent);opacity:.9}
.home3d-sensor-marker[data-heat=true]{background:linear-gradient(180deg,#ffddd6,#fff0eb);color:#772b23;border-color:#e9a094;border-left-color:#d95645;box-shadow:0 3px 10px #a3342430}.home3d-sensor-marker[data-heat=true] small{color:#8f4c43}
.temperature-trend{display:flex;flex-direction:column;gap:1px;margin-top:3px;font-size:8px;font-weight:normal}.temperature-trend svg{max-width:100%;overflow:visible}.temperature-trend path{fill:none;stroke:#758058;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.temperature-trend circle{fill:#758058}.temperature-trend path.cold{stroke:#4093cc}.temperature-trend path.hot{stroke:#d55646}.temperature-trend circle.cold{fill:#4093cc}.temperature-trend circle.hot{fill:#d55646}.temperature-trend small{font-size:9px!important;letter-spacing:0}#rooms>button.sensor-row[data-ice=true]{background:linear-gradient(#d9efff,#fff);color:#183c57}#rooms>button.sensor-row[data-heat=true]{background:linear-gradient(#ffddd6,#fff0eb);color:#772b23}.sensor-row[data-ice=true] small{color:#426580}.sensor-row[data-heat=true] small{color:#8f4c43}
/* The native Temperatures view owns the summary, legend and sensor list. */
#summary,#legend,details{display:none}
@media(prefers-color-scheme:dark){:root{--surface:#1f2117}body{color:#eef0e6}.home3d-label{background:#252a20ee;color:#eef0e6}.home3d-sensor-marker small,.sensor-row small,#counts,#legend{color:#b1b9a2}.temperature-leaders line{stroke:#b1b9a2}}
</style></head><body><header><select aria-label="House floor"></select><button>Reset view</button><label>Floors<input id="opacity" type="range" min="15" max="100" value="45" aria-label="Upper floor opacity"></label></header><div id="stage"><canvas aria-label="Interactive 3D home"></canvas><div id="labels"></div></div><div id="summary"><span id="overview">Loading temperatures…</span><span id="counts"></span></div><div id="legend">Blue &lt;70 · Yellow 70–75 · Amber 75–78 · Red ≥78 °F · Grey = no recent reading</div><div id="status">Loading Home 3D…</div><details><summary id="sensor-count">All sensor readings &amp; history</summary><div id="rooms"></div></details><script>""" + script + "</script></body></html>";
            web.load_html (html, "about:blank");
        } catch (Error error) { warning ("Home 3D resource: %s", error.message); }
    }

    public void set_state (Json.Object root, bool fresh) {
        // Serialize a separate object; never attach or mutate the collector's root.
        var copy = new Json.Object ();
        foreach (string key in new string[] {"house_preview", "temperature_sensors", "temperature_history"}) {
            if (root.has_member (key)) copy.set_member (key, root.get_member (key).copy ());
        }
        copy.set_boolean_member ("cache_fresh", fresh);
        state = copy; publish ();
    }

    private void publish () {
        if (!ready || state == null || !state.has_member ("house_preview")) return;
        var node = new Json.Node (Json.NodeType.OBJECT); node.set_object (state);
        string script = "window.renderHouse(" + Json.to_string (node, false) + ")";
        web.evaluate_javascript.begin (script, -1, null, null, null, (obj, result) => {
            try { web.evaluate_javascript.end (result); }
            catch (Error error) { warning ("Home 3D update: %s", error.message); }
        });
    }
}
