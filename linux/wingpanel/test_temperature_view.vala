/* SPDX-License-Identifier: MIT */
// Unit checks for the Temperatures view's pure helpers (no display needed).
void main () {
    // Half-hour buckets: a 1 °F rise over the last hour is 1 °F/h.
    assert (TemperatureView.trend ({70, 70.5, 71}, 0.5) == 1.0);
    // Offline gaps are skipped back to the nearest recorded bucket within two hours.
    double rate = TemperatureView.trend ({66, 68, double.NAN, 70, 72}, 0.5);
    assert ((rate - 4.0 / 1.5).abs () < 1e-9);
    // Nothing recent enough, or a single point, gives no trend instead of a guess.
    assert (TemperatureView.trend ({70, double.NAN, double.NAN, double.NAN, double.NAN, double.NAN, 72}, 0.5).is_nan ());
    assert (TemperatureView.trend ({double.NAN, 72}, 0.5).is_nan ());
    assert (TemperatureView.rate_text (double.NAN) == "");
    assert (TemperatureView.rate_text (0.1) == "→ steady");
    assert (TemperatureView.rate_text (2.04) == "↑ 2.0 °F/h");
    assert (TemperatureView.rate_text (-3.5) == "↓ 3.5 °F/h");
    assert (TemperatureView.ago (30) == "just now");
    assert (TemperatureView.ago (600) == "10 min ago");
    assert (TemperatureView.ago (3 * 3600) == "3 h ago");
    assert (TemperatureView.ago (72 * 3600) == "3 days ago");
    // Bands match the web floor plan: <70 cold, 70–75 comfortable, 75–78 warm, ≥78 hot.
    assert (TemperatureView.band (69.9) == "temperature-cold");
    assert (TemperatureView.band (75) == "temperature-comfortable");
    assert (TemperatureView.band (77.9) == "temperature-warm");
    assert (TemperatureView.band (78) == "temperature-hot");
    print ("temperature view helpers ok\n");
}
