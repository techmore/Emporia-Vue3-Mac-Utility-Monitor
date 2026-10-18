/* SPDX-License-Identifier: MIT */
// Offline WebKit host for the exact scene builder used by Home 3D.
public class WebHouseView : Gtk.Box {
    private WebKit.WebView web;
    private Json.Object? state;
    private bool ready;
    public signal void room_selected (string room);

    public WebHouseView () {
        Object (orientation: Gtk.Orientation.VERTICAL);
        web = (WebKit.WebView) GLib.Object.new (typeof (WebKit.WebView), "network-session", new WebKit.NetworkSession.ephemeral ());
        web.hexpand = true; web.vexpand = true;
        var manager = web.get_user_content_manager ();
        manager.register_script_message_handler ("history", null);
        manager.script_message_received["history"].connect ((value) => {
            if (value.is_string ()) room_selected (value.to_string ());
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
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:#252a20;font:13px system-ui}header{display:flex;gap:8px;padding:6px 10px;align-items:center}select,button{font:inherit;padding:5px 10px;border:1px solid #8888;border-radius:6px;background:var(--surface);color:inherit}select{flex:1}#stage{position:relative;height:calc(100vh - 72px);min-height:280px;overflow:hidden}canvas{width:100%;height:100%;display:block;touch-action:none;background:var(--surface)}#labels{position:absolute;inset:0;pointer-events:none;overflow:hidden}.home3d-label{position:absolute;transform:translate(-50%,-50%);background:#fffffff0;color:#252a20;border:1px solid #7777;border-radius:6px;padding:4px 7px;font-size:12px;text-align:center;white-space:normal;width:max-content;max-width:170px;box-shadow:0 2px 5px #0002}.home3d-label[hidden]{display:none}.home3d-sensor-marker{font-size:11px;border-color:#a58a38}.stale{opacity:.6}#status{font-size:11px;padding:4px 10px}details{padding:8px 12px}#rooms>div{display:flex;justify-content:space-between;gap:16px;padding:5px 0;border-bottom:1px solid #8884}#rooms strong{white-space:nowrap;font-size:12px}
@media(prefers-color-scheme:dark){:root{--surface:#1f2117}body{color:#eef0e6}.home3d-label{background:#252a20ee;color:#eef0e6}}
</style></head><body><header><select aria-label="House floor"></select><button>Reset view</button></header><div id="stage"><canvas aria-label="Interactive 3D home"></canvas><div id="labels"></div></div><div id="status">Loading Home 3D…</div><details><summary>Room temperatures</summary><div id="rooms"></div></details><script>""" + script + "</script></body></html>";
            web.load_html (html, "about:blank");
        } catch (Error error) { warning ("Home 3D resource: %s", error.message); }
    }

    public void set_state (Json.Object root, bool fresh) {
        // Serialize a separate object; never attach or mutate the collector's root.
        var copy = new Json.Object ();
        foreach (string key in new string[] {"house_preview", "temperature_sensors"}) {
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
