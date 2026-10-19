"""User style sheet that makes the embedded collector dashboard follow the elementary OS theme.

The collector's pages are styled with CSS variables (an olive/stone palette shared with the Mac app).
The native client overrides those variables with the system's light/dark palette and accent colour,
swaps the web fonts for the system font and hides the page's own top bar, which the native sidebar
replaces. The collector itself is not changed.
"""


def _hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(c))) for c in rgb)


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def parse_accent(value, fallback=(0x36, 0x89, 0xe6)):
    """Accept a Gdk.RGBA-like object (0..1 floats) or an '#rrggbb' string."""
    try:
        if isinstance(value, str):
            value = value.lstrip('#')
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        return (value.red * 255, value.green * 255, value.blue * 255)
    except (AttributeError, ValueError, IndexError):
        return fallback


def build_css(dark, accent=None):
    accent = parse_accent(accent) if accent is not None else (0x36, 0x89, 0xe6)
    white, black = (255, 255, 255), (0, 0, 0)
    ramp = {50: _mix(accent, white, .92), 100: _mix(accent, white, .84), 200: _mix(accent, white, .68),
            300: _mix(accent, white, .5), 400: _mix(accent, white, .28), 500: accent,
            600: _mix(accent, black, .12), 700: _mix(accent, black, .28), 800: _mix(accent, black, .45),
            900: _mix(accent, black, .62), 950: _mix(accent, black, .8)}
    olive = ''.join('--olive-%d:%s;' % (k, _hex(v)) for k, v in ramp.items())
    # Author styles beat user style sheets unless the declaration is !important.
    def important(block):
        return ';'.join(d + ' !important' for d in block.split(';') if d.strip()) + ';'

    if dark:
        surface = ('--bg:#2b2b2b;--surface:#353535;--surface2:#3d3d3d;--border:#4a4a4a;--text:#fafafa;'
                   '--text-light:#a8a8a8;--stone-50:#2b2b2b;--stone-100:#333;--stone-200:#3d3d3d;'
                   '--stone-300:#4a4a4a;--stone-400:#7a7a7a;--stone-500:#a8a8a8;--stone-600:#c8c8c8;'
                   '--stone-700:#dedede;--stone-800:#fafafa;--stone-900:#fff;')
        scheme = 'dark'
        card_shadow = '0 1px 2px rgba(0,0,0,.4)'
        extra = '.circuit-bar-wrap { background: rgba(255,255,255,.12) !important; }'
    else:
        surface = ('--bg:#f5f5f5;--surface:#ffffff;--surface2:#fafafa;--border:#d4d4d4;--text:#333333;'
                   '--text-light:#666666;--stone-50:#fafafa;--stone-100:#f5f5f5;--stone-200:#e8e8e8;'
                   '--stone-300:#d4d4d4;--stone-400:#a0a0a0;--stone-500:#7a7a7a;--stone-600:#5a5a5a;'
                   '--stone-700:#444;--stone-800:#333;--stone-900:#1f1f1f;')
        scheme = 'light'
        card_shadow = '0 1px 2px rgba(0,0,0,.12)'
        extra = ''
    return f"""
:root {{
  color-scheme: {scheme};
  {important(olive + surface + '--accent:' + _hex(ramp[600] if not dark else ramp[400]) + ';--accent-fg:#fff;--green:#68b723;--red:#ed5353;--amber:#f37329;--temperature-cold:#3689e6;--temperature-comfortable:#f9c440;--temperature-warm:#ffa154;--temperature-hot:#ed5353')}
}}
/* System font, not the web fonts: elementary's UI font with tabular figures for readings. */
*:not(code):not(pre):not(kbd):not(samp):not(svg *) {{
  font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif !important;
}}
h1, h2, h3, h4 {{ font-weight: 700; letter-spacing: -0.01em; }}
body {{ font-feature-settings: 'tnum', 'cv01'; background: var(--bg); color: var(--text); }}
/* The native sidebar replaces the page's own navigation bar. */
nav.topnav {{ display: none !important; }}
/* elementary rhythm: 6 px controls, 9 px panels, hairline borders, restrained shadows. */
button, input, select, textarea {{ border-radius: 6px; }}
.card, .chart-box, .mains-card, .history-device-summary {{ border-radius: 9px; box-shadow: {card_shadow}; }}
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-thumb {{ background: color-mix(in srgb, var(--text) 30%, transparent); border-radius: 5px;
  border: 2px solid transparent; background-clip: content-box; }}
::-webkit-scrollbar-thumb:hover {{ background: color-mix(in srgb, var(--text) 50%, transparent); background-clip: content-box; }}
:focus-visible {{ outline: 2px solid {_hex(ramp[500])}; outline-offset: 2px; }}
{extra}
::selection {{ background: color-mix(in srgb, {_hex(ramp[500])} 40%, transparent); }}
"""
