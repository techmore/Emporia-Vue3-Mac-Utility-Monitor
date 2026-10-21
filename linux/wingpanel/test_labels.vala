/* SPDX-License-Identifier: MIT */
// Unit checks for the dropdown's text helpers (no display needed).
void main () {
    // Unnamed circuits (bare channel numbers or Circuit_N) read as their slot; named ones keep their name.
    assert (EnergyLabels.is_unnamed ("5"));
    assert (EnergyLabels.is_unnamed ("14"));
    assert (EnergyLabels.is_unnamed ("Circuit_14"));
    assert (!EnergyLabels.is_unnamed ("Dryer"));
    assert (!EnergyLabels.is_unnamed ("Circuit_"));
    assert (!EnergyLabels.is_unnamed (""));
    assert (!EnergyLabels.is_unnamed ("Shop 2"));
    assert (EnergyLabels.circuit_title ("5", 5) == "Slot 5");
    assert (EnergyLabels.circuit_title ("Circuit_14", 17) == "Slot 17");
    assert (EnergyLabels.circuit_title ("Kitchen Stove", 15) == "Kitchen Stove");
    // The collector's microsecond ISO stamp formats as a clock time, not a fragment of the seconds.
    assert (EnergyLabels.reading_clock ("2026-10-20T06:21:11.880900") == "06:21");
    assert (EnergyLabels.reading_clock ("2026-10-19 03:59:00") == "03:59");
    assert (EnergyLabels.reading_clock ("") == "");
    assert (EnergyLabels.reading_clock ("not a time") == "");
    // Relative age, two hours after a reading taken in the local clock.
    var taken = EnergyLabels.parse_reading ("2026-10-20T06:19:11.880900");
    assert (taken != null);
    assert (EnergyLabels.reading_age ("2026-10-20T06:19:11.880900", taken.to_unix () + 2 * 3600 + 30) == "2 h ago");
    assert (EnergyLabels.reading_age ("2026-10-20T06:19:11.880900", taken.to_unix () + 40) == "just now");
    assert (EnergyLabels.reading_age ("garbage", 0) == "");
    print ("label helpers ok\n");
}
