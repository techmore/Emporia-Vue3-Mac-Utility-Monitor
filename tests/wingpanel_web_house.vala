int main (string[] args) {
    Gtk.init ();
    var home = new WebHouseView ();
    bool history_selected = false;
    home.room_selected.connect ((room) => { assert (room == "living"); history_selected = true; });
    var window = new Gtk.Window () { child = home, default_width = 700, default_height = 520 };
    var parser = new Json.Parser ();
    try { parser.load_from_file (args[1]); } catch (Error e) { error ("%s", e.message); }
    home.set_state (parser.get_root ().get_object (), true);
    window.present ();
    var loop = new MainLoop ();
    var web = (WebKit.WebView) home.get_first_child ();
    Timeout.add_seconds (5, () => {
        web.evaluate_javascript.begin ("window.webkit.messageHandlers.history.postMessage('living'); JSON.stringify({ready:window.nativeHouseReady,error:window.nativeHouseError,labels:document.querySelectorAll('.home3d-label').length,canvas:document.querySelector('canvas').width})", -1, null, null, null, (o,r) => {
            try {
                string status = web.evaluate_javascript.end (r).to_string ();
                stdout.printf ("%s\n", status);
                assert (status.contains ("\"ready\":true"));
                assert (!status.contains ("\"error\""));
            }
            catch (Error e) { stderr.printf ("%s\n", e.message); }
            web.get_snapshot.begin (WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, null, (o,r) => {
                try { var texture = web.get_snapshot.end (r); if (args.length > 2) texture.save_to_png (args[2]); }
                catch (Error e) { stderr.printf ("%s\n", e.message); }
                assert (history_selected);
                loop.quit ();
            });
        });
        return false;
    });
    loop.run (); return 0;
}
