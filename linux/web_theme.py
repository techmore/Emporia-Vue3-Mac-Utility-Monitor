"""User style sheet that makes the embedded collector dashboard follow the elementary OS theme.

The collector's pages are styled with CSS variables (an olive/stone palette shared with the Mac app).
The native client overrides those variables with the system's light/dark palette and accent colour,
swaps the web fonts for the system font and hides the page's own top bar, which the native sidebar
replaces. The collector itself is not changed.

The palette is computed, not hand-picked: every accent the user can choose is adjusted until text,
links, status colours and button labels reach 4.5:1 against the surface they are drawn on. The
collector's "dark brand" panels (Now panel, breaker panel, forecast strip) were written for a dark
olive ramp and would stay dark-on-dark or navy-on-white in the light theme, so in the light theme
those panels re-map the ramp locally (custom properties inherit) instead of being restyled one rule
at a time; in the dark theme they get a neutral charcoal ramp rather than an accent-tinted one.
"""
import re


def _hex(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*tuple(max(0, min(255, round(c))) for c in rgb))


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b, strict=False))


def _channel(v):
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def ensure(fg, backgrounds, target=4.6):
    """Move fg towards black (light surfaces) or white (dark surfaces) until it reaches `target` on all of them."""
    if not isinstance(backgrounds, (list, tuple)) or isinstance(backgrounds[0], (int, float)):
        backgrounds = [backgrounds]
    towards = (0, 0, 0) if sum(luminance(b) for b in backgrounds) / len(backgrounds) > 0.4 else (255, 255, 255)
    for step in range(41):
        candidate = _mix(fg, towards, step / 40)
        if all(contrast(candidate, b) >= target for b in backgrounds):
            return candidate
    return towards


def on_colour(background, target=4.6):
    """Black or white label text, whichever reads better on `background`."""
    white, black = (255, 255, 255), (17, 17, 17)
    return white if contrast(white, background) >= contrast(black, background) else black


def parse_accent(value, fallback=(0x36, 0x89, 0xe6)):
    """Accept a Gdk.RGBA-like object (0..1 floats) or an '#rrggbb' string."""
    try:
        if isinstance(value, str):
            value = value.lstrip('#')
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        return (value.red * 255, value.green * 255, value.blue * 255)
    except (AttributeError, ValueError, IndexError):
        return fallback


# Dense mode: CodexBar-style tight rows and small, quiet type; same colours, less air.
COMPACT = (
    'body { font-size: 12.5px; line-height: 1.4; } .page { padding: 12px 16px 28px; } .page h1 { font-size: 1.45rem; } '
    '.section { margin-top: 18px; } .section-head { margin-bottom: 8px; padding-bottom: 4px; } .section-head h2 { font-size: 1.05rem; } '
    '.card { padding: 10px 12px; border-radius: 8px; } .card-label { font-size: 0.62rem; margin-bottom: 2px; } '
    '.card-value { font-size: 1.3rem; } .card-meta { font-size: 0.7rem; margin-top: 2px; } '
    '.data-table th { padding: 3px 8px; font-size: 0.64rem; } .data-table td { padding: 3px 8px; } '
    '.grid-2, .grid-3, .grid-4 { gap: 8px; } .now-panel { padding: 12px 14px; margin-top: 10px; } .panel-wrap { padding: 12px; margin-top: 10px; } '
    '.breaker { padding: 0.3rem 0.55rem; } .sys-link { padding: 0.3rem 0.55rem; } .page-jump-nav a { padding: 0.2rem 0.55rem; }'
)

WHITE, BLACK = (255, 255, 255), (0, 0, 0)
LIGHT = {'bg': (245, 245, 245), 'surface': (255, 255, 255), 'surface2': (250, 250, 250), 'border': (200, 200, 200),
         'text': (51, 51, 51), 'text-light': (102, 102, 102)}
DARK = {'bg': (43, 43, 43), 'surface': (53, 53, 53), 'surface2': (61, 61, 61), 'border': (74, 74, 74),
        'text': (250, 250, 250), 'text-light': (178, 178, 178)}
# elementary palette status colours (Lime, Strawberry, Orange 500), pulled to AA for text use.
STATUS = {'green': (0x68, 0xb7, 0x23), 'red': (0xed, 0x53, 0x53), 'amber': (0xf3, 0x73, 0x29)}


def palette(dark, accent):
    """All computed colours as {name: (r, g, b)}; exposed so tests can assert contrast for every accent."""
    mode = DARK if dark else LIGHT
    text_surfaces = [mode['bg'], mode['surface'], mode['surface2']]
    ramp = {50: _mix(accent, WHITE, .92), 100: _mix(accent, WHITE, .84), 200: _mix(accent, WHITE, .68),
            300: _mix(accent, WHITE, .5), 400: _mix(accent, WHITE, .28), 500: accent,
            600: _mix(accent, BLACK, .12), 700: _mix(accent, BLACK, .28), 800: _mix(accent, BLACK, .45),
            900: _mix(accent, BLACK, .62), 950: _mix(accent, BLACK, .8)}
    # Button fill: the accent, darkened until white labels pass; the same shade is the accent in both modes.
    button = ensure(accent, [WHITE], 4.6)
    out = {'ramp': ramp, 'button': button, 'on-button': WHITE, 'ring': _mix(accent, WHITE, .0)}
    out['accent-text'] = ensure(accent, text_surfaces)
    for name, value in STATUS.items():
        # Also clear the 12% tint of the status colour itself, which the collector uses behind badges.
        out[name] = ensure(value, text_surfaces + [_mix(mode['surface'], value, .14)], 5.3)
    out.update(mode)
    return out


def build_css(dark, accent=None, density='comfortable', style='elementary'):
    if style == 'original':
        # The collector's own olive design, with only the page's top bar hidden (the native sidebar replaces it).
        return 'nav.topnav { display: none !important; }\n'
    accent = parse_accent(accent) if accent is not None else (0x36, 0x89, 0xe6)
    p = palette(dark, accent)
    ramp = dict(p['ramp'])
    # Deep ramp steps are used as button fills and active chips with --olive-50 labels: keep them on the
    # AA button shade rather than a near-black navy.
    ramp[800] = p['button']
    ramp[700] = _mix(p['button'], BLACK, .16)
    # 500/400 are the collector's mid-weight *text* steps (no-data notes, small labels) on cards.
    ramp[600] = ramp[500] = ramp[400] = p['accent-text']
    olive = ''.join(f'--olive-{k}:{_hex(v)};' for k, v in ramp.items())

    # Author styles beat user style sheets unless the declaration is !important.
    def important(block):
        return ';'.join(d + ' !important' for d in block.split(';') if d.strip()) + ';'

    def rules(css):
        return re.sub(r'\{([^{}]*)\}', lambda m: '{' + important(m.group(1)) + '}', css)

    mode = DARK if dark else LIGHT
    surface = ''.join(f'--{k}:{_hex(mode[k])};' for k in ('bg', 'surface', 'surface2', 'border', 'text', 'text-light'))
    if dark:
        stone = ('--stone-50:#2b2b2b;--stone-100:#333;--stone-200:#3d3d3d;--stone-300:#4a4a4a;--stone-400:#a0a0a0;'
                 '--stone-500:#b4b4b4;--stone-600:#c8c8c8;--stone-700:#dedede;--stone-800:#fafafa;--stone-900:#fff;')
        scheme, card_shadow = 'dark', '0 0 0 1px rgba(0,0,0,.35), 0 1px 3px rgba(0,0,0,.45)'
        accent_var = p['accent-text']
    else:
        stone = ('--stone-50:#fafafa;--stone-100:#f5f5f5;--stone-200:#e8e8e8;--stone-300:#d4d4d4;--stone-400:#6b6b6b;'
                 '--stone-500:#616161;--stone-600:#5a5a5a;--stone-700:#444;--stone-800:#333;--stone-900:#1f1f1f;')
        scheme, card_shadow = 'light', '0 0 0 1px rgba(0,0,0,.08), 0 1px 3px rgba(0,0,0,.10)'
        accent_var = p['button']
    status = '--green:{};--red:{};--amber:{};'.format(*tuple(_hex(p[k]) for k in ('green', 'red', 'amber')))
    bands = '--temperature-cold:#3689e6;--temperature-comfortable:#f9c440;--temperature-warm:#ffa154;--temperature-hot:#ed5353;'
    root = olive + surface + stone + status + bands + '--accent:{};--accent-fg:{};--accent-text:{}'.format(
        _hex(accent_var), _hex(on_colour(accent_var) if dark else p['on-button']), _hex(p['accent-text']))

    if dark:
        # Neutral charcoal for the collector's "dark brand" panels; the accent only tints the labels.
        panel = {950: (38, 38, 38), 900: (50, 50, 50), 800: (61, 61, 61), 700: (78, 78, 78), 600: (96, 96, 96),
                 500: ensure(accent, [(38, 38, 38), (78, 78, 78)]), 400: ensure(accent, [(38, 38, 38), (78, 78, 78)]), 300: (205, 205, 205),
                 200: (222, 222, 222), 100: (236, 236, 236), 50: (250, 250, 250)}
        plate = '#262626'
        extra = """
[style*="color:var(--olive-800)"], [style*="color: var(--olive-800)"], [style*="color:var(--olive-700)"], [style*="color: var(--olive-700)"] { color: var(--accent-text); }
.circuit-bar-wrap { background: rgba(255,255,255,.12) !important; }
.sys-link:hover { background: rgba(255,255,255,.07); }
.sys-link.active { background: color-mix(in srgb, var(--accent-text) 12%, transparent); }
.delta.flat, .delta.nodata { background: var(--surface2); }
.data-table tr:hover td { background: rgba(255,255,255,.05); }
.skeleton, [class*="skeleton"] { background: linear-gradient(90deg, var(--surface2) 25%, var(--border) 50%, var(--surface2) 75%); }
"""
    else:
        # Light theme: the dark panels become light cards. The ramp is mirrored locally so every
        # var(--olive-N) inside them (backgrounds, borders, labels) lands on a legible light/dark pair.
        def tint(t):
            return _mix(accent, WHITE, t)
        label = ensure(accent, [WHITE, tint(.84)], 4.8)
        panel = {950: WHITE, 900: tint(.94), 800: tint(.9), 700: tint(.78), 600: tint(.6), 500: label,
                 400: label, 300: _mix(label, BLACK, .25), 200: _mix(label, BLACK, .45), 100: (51, 51, 51), 50: (31, 31, 31)}
        plate = '#2b3138'
        green, orange, yellow, red = (ensure(c, [WHITE, tint(.93)], 3.0) for c in
                                      ((0x4a, 0xde, 0x80), (0xf9, 0x73, 0x16), (0xfa, 0xcc, 0x15), (0xef, 0x44, 0x44)))
        extra = f"""
[style*="color:var(--olive-300)"], [style*="color: var(--olive-300)"], [style*="color:var(--olive-200)"], [style*="color: var(--olive-200)"] {{ color: var(--accent-text); }}
.breaker-safe .breaker-bar, .fill-normal {{ background: {_hex(green)}; }}
.fill-moderate {{ background: {_hex(yellow)}; }}
.breaker-safe.warn .breaker-bar, .fill-caution {{ background: {_hex(orange)}; }}
.breaker-safe.danger .breaker-bar, .fill-danger {{ background: {_hex(red)}; }}
.load-normal {{ color: {_hex(ensure(green, tint(.93), 4.5))}; }} .load-moderate {{ color: {_hex(ensure(yellow, tint(.93), 4.5))}; }} .load-caution {{ color: {_hex(ensure(orange, tint(.93), 4.5))}; }} .load-danger {{ color: {_hex(ensure(red, tint(.93), 4.5))}; }}
.breaker-peak-badge {{ color: {_hex(ensure(yellow, tint(.93), 4.5))}; }}
.breaker-load-text, .breaker-watts, .breaker-name {{ color: var(--olive-100); }}
"""
    panel_vars = ''.join(f'--olive-{k}:{_hex(v)};' for k, v in panel.items())
    panel_selectors = '.now-panel, .panel-wrap, .banner-chart'
    ring = _hex(accent_var if dark else p['button'])
    return f"""
:root {{
  color-scheme: {scheme};
  {important(root)}
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
/* Dark brand panels (see module docstring): remapped ramp, inherited by everything inside. */
{panel_selectors} {{ {panel_vars} box-shadow: {card_shadow}; }}
/* Controls the collector leaves unstyled get the system look; styled ones keep their own rules. */
input:not([type=checkbox]):not([type=radio]):not([type=range]):not([class]):not([style]), select:not([class]):not([style]), textarea:not([class]):not([style]) {{
  color: var(--text); background: var(--surface); border: 1px solid var(--border); padding: 5px 8px; min-height: 30px; box-sizing: border-box;
}}
button:not([class]):not([style]) {{
  color: var(--text); background: var(--surface2); border: 1px solid var(--border); padding: 5px 12px; min-height: 30px; cursor: pointer;
}}
button:not([class]):not([style]):hover {{ background: color-mix(in srgb, var(--text) 8%, var(--surface2)); }}
a:not([class]) {{ color: var(--accent-text); }}
{rules('.page-jump-nav a { color: var(--accent-text); border-color: var(--border); } .sys-name, .sys-sub { white-space: normal; overflow-wrap: anywhere; }')}
/* Heatmaps keep the collector's "brighter means more energy" legend, so they sit on a dark plate in both themes. */
{rules('.power-heatmap { background: ' + plate + '; border-radius: 9px; padding: 4px; } .power-heatmap th, .power-heatmap tbody th { background: ' + plate + '; color: #f2f2f2; }')}
/* Inline status colours the collector writes as literals; swap them for the AA-checked variables. */
{rules('.int-badge.ok { background: color-mix(in srgb, var(--green) 14%, var(--surface)); } .int-badge.warn { background: color-mix(in srgb, var(--amber) 14%, var(--surface)); }')}
{rules('[style*="color:#81c784"], [style*="color: #81c784"] { color: var(--green); } [style*="color:#f87171"], [style*="color: #f87171"] { color: var(--red); } [style*="color:#fbbf24"], [style*="color: #fbbf24"] { color: var(--amber); }')}
/* Bare data tables (no class) get the same quiet header and hairline rows as the collector's .data-table. */
{rules('table:not([class]) { border-collapse: collapse; width: 100%; margin: 8px 0; } table:not([class]) th { text-align: left; padding: 8px 12px; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: var(--text-light); border-bottom: 1px solid var(--border); } table:not([class]) td { padding: 8px 12px; border-bottom: 1px solid color-mix(in srgb, var(--border) 50%, transparent); font-variant-numeric: tabular-nums; }')}
/* Type scale and rhythm (6/12 px): elementary's 14 px body, tighter titles, quieter labels, tabular readings. */
{rules('body { font-size: 14px; line-height: 1.5; } .page { padding: 24px 24px 48px; } .page h1 { font-size: 1.9rem; letter-spacing: -0.02em; } .section { margin-top: 30px; } .section-head h2 { font-size: 1.25rem; letter-spacing: -0.01em; } .card { padding: 18px; border-radius: 9px; transition: box-shadow .15s ease; } .card-label { font-size: 0.68rem; font-weight: 600; letter-spacing: 0.06em; } .card-value { font-size: 1.75rem; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; } .data-table th { font-size: 0.7rem; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: var(--text-light); } .data-table td { padding: 9px 12px; font-variant-numeric: tabular-nums; } .grid-2, .grid-3, .grid-4 { gap: 12px; }')}
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-thumb {{ background: color-mix(in srgb, var(--text) 30%, transparent); border-radius: 5px;
  border: 2px solid transparent; background-clip: content-box; }}
::-webkit-scrollbar-thumb:hover {{ background: color-mix(in srgb, var(--text) 50%, transparent); background-clip: content-box; }}
:focus-visible {{ outline: 2px solid {ring}; outline-offset: 2px; }}
{rules(extra)}
::selection {{ background: color-mix(in srgb, {_hex(accent)} 40%, transparent); }}
{rules(COMPACT) if density == 'compact' else ''}
/* No motion for people who ask for none; stronger hairlines when the system asks for more contrast. */
@media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation-duration: .01ms !important; transition-duration: .01ms !important; scroll-behavior: auto !important; }} }}
@media (prefers-contrast: more) {{ :root {{ --border: {'#c8c8c8' if dark else '#6a6a6a'} !important; --text-light: {'#e0e0e0' if dark else '#444'} !important; }} }}
"""
