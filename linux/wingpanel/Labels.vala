/* SPDX-License-Identifier: MIT */
// Text helpers shared by the dropdown's views. Pure functions, so `meson test` checks them without a display.
namespace EnergyLabels {
    // A circuit nobody has named arrives as its Emporia channel: "5", "14" or "Circuit_14".
    public static bool is_unnamed (string display) {
        string name = display.strip ();
        string digits = name.has_prefix ("Circuit_") ? name.substring ("Circuit_".length) : name;
        if (digits == "") return false;
        for (int i = 0; i < digits.length; i++) {
            if (!digits[i].isdigit ()) return false;
        }
        return true;
    }

    // What to print for a breaker: its name, or "Slot N" (dimmed by the caller) when it has none.
    public static string circuit_title (string display, int slot) {
        return is_unnamed (display) ? "Slot %d".printf (slot) : display;
    }

    // The collector's reading stamp is ISO 8601 with microseconds ("2026-10-20T06:19:11.880900") in its local
    // clock (docs/UTC_MIGRATION.md); older summaries used a space separator. An explicit offset is honoured.
    public static DateTime? parse_reading (string stamp) {
        string text = stamp.strip ();
        if (text == "") return null;
        return new DateTime.from_iso8601 (text.replace (" ", "T"), new TimeZone.local ());
    }

    // "06:19" for the status line; "" when there is no readable stamp.
    public static string reading_clock (string stamp) {
        var parsed = parse_reading (stamp);
        return parsed == null ? "" : parsed.to_local ().format ("%H:%M");
    }

    // "2 h ago" relative to `now` (unix seconds); "" when there is no readable stamp.
    public static string reading_age (string stamp, int64 now) {
        var parsed = parse_reading (stamp);
        return parsed == null ? "" : TemperatureView.ago (now - parsed.to_unix ());
    }
}
