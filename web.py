#!/usr/bin/env python3
"""
Energy Monitor — Flask web server
Theme: techmore.github.io  (olive palette · Instrument Serif · Inter)
"""
from datetime import datetime
from flask import Flask, jsonify, render_template_string, Response, request
import logging
import os
import energy

app = Flask(__name__)

VERSION = "1.6.2"

MONTHLY_BUDGET = float(os.environ.get("MONTHLY_BUDGET", "150"))
RATE = energy.RATE_CENTS / 100

# ── Shared design tokens (mirrors techmore.github.io) ─────────────────────────
BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --olive-50:  #f7f8f4;
  --olive-100: #eef0e6;
  --olive-200: #dde1d0;
  --olive-300: #c4c9b0;
  --olive-400: #a7ae8b;
  --olive-500: #8a9269;
  --olive-600: #6e754b;
  --olive-700: #575d3d;
  --olive-800: #464a34;
  --olive-900: #3b3e2d;
  --olive-950: #1f2117;

  --stone-50:  #fafaf9;
  --stone-100: #f5f5f4;
  --stone-200: #e7e5e4;
  --stone-300: #d6d3d1;
  --stone-400: #a8a29e;
  --stone-500: #78716c;
  --stone-600: #57534e;
  --stone-700: #44403c;
  --stone-800: #292524;
  --stone-900: #1c1917;

  --bg:         var(--olive-300);
  --surface:    var(--olive-200);
  --surface2:   var(--olive-300);
  --border:     var(--olive-400);
  --text:       var(--stone-800);
  --text-light: var(--stone-500);
  --accent:     var(--olive-700);
  --accent-fg:  var(--olive-50);
  --green:      #5a8a5e;
  --red:        #c0392b;
  --amber:      #b07d2a;
  --chart1:     var(--olive-600);
  --chart2:     #5ba4b5;
}

html { scroll-behavior: smooth; }

body {
  font-family: 'Inter', system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  font-size: 15px;
}
h1,h2,h3,h4 { font-family: 'Instrument Serif', serif; line-height: 1.2; }

/* ── Selection & scrollbar ── */
::selection { background-color: var(--olive-200); color: var(--olive-800); }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--stone-100); }
::-webkit-scrollbar-thumb { background: var(--olive-400); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--olive-500); }
:focus-visible { outline: 2px solid var(--olive-500); outline-offset: 2px; }

/* ── Nav ── */
nav.topnav {
  background: rgba(247, 248, 244, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(221, 225, 208, 0.6);
  color: var(--stone-800);
  position: sticky; top: 0; z-index: 50;
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
nav.topnav .inner {
  max-width: 72rem; margin: 0 auto;
  padding: 0 1.25rem;
  display: flex; align-items: center; justify-content: space-between;
  height: 56px;
}
nav.topnav a {
  color: var(--stone-600);
  text-decoration: none;
  font-size: 0.875rem; font-weight: 500;
  padding: 0.4rem 0.75rem;
  border-radius: 6px;
  transition: background 0.15s, color 0.15s;
}
nav.topnav a:hover { background: var(--olive-100); color: var(--olive-800); }
nav.topnav a.active { background: var(--olive-600); color: #fff; }
nav.topnav .nav-links { display: flex; gap: 4px; }
nav.topnav .brand {
  font-family: 'Instrument Serif', serif;
  font-size: 1.1rem; font-weight: 700;
  color: var(--olive-900); margin-right: 0.5rem;
  text-decoration: none;
}
nav.topnav .status-dot {
  width: 8px; height: 8px; border-radius: 50%;
  display: inline-block; margin-right: 6px;
}
nav.topnav .status-dot.live  { background: var(--green); box-shadow: 0 0 0 3px rgba(90,138,94,0.25); }
nav.topnav .status-dot.stale { background: var(--amber); }
nav.topnav .status-dot.dead  { background: var(--red);   }

/* ── Layout ── */
.page { max-width: 72rem; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }

/* ── Cards ── */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
}
.card-label {
  font-size: 0.7rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-light); margin-bottom: 0.35rem;
}
.card-value {
  font-size: 2rem; font-weight: 700; line-height: 1;
  font-family: 'Instrument Serif', serif;
}
.card-value .unit { font-size: 0.9rem; font-weight: 400; color: var(--text-light); margin-left: 3px; }
.card-meta { font-size: 0.78rem; color: var(--text-light); margin-top: 0.4rem; }

.grid-4 { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px,1fr)); gap: 12px; }
.grid-3 { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px,1fr)); gap: 12px; }
.grid-2 { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px,1fr)); gap: 12px; }

/* ── Section headers ── */
.section { margin-top: 2.25rem; }
.section-head {
  display: flex; align-items: baseline; justify-content: space-between;
  border-bottom: 1px solid var(--border); padding-bottom: 0.6rem; margin-bottom: 1rem;
}
.section-head h2 { font-size: 1.5rem; color: var(--text); }
.section-head .section-sub { font-size: 0.78rem; color: var(--text-light); }

/* ── Delta badges ── */
.delta {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 0.78rem; font-weight: 600;
  padding: 2px 7px; border-radius: 20px;
  vertical-align: middle;
}
.delta.up   { background: oklch(52% 0.18 25 / 0.12); color: var(--red); }
.delta.down { background: oklch(52% 0.14 145 / 0.12); color: var(--green); }
.delta.flat { background: var(--olive-100); color: var(--text-light); }
.delta.nodata { background: var(--olive-100); color: var(--text-light); font-style: italic; }

/* ── Now panel ── */
.now-panel {
  background: var(--olive-950);
  color: var(--olive-50);
  border-radius: 16px;
  padding: 1.75rem 2rem;
  margin-top: 1.5rem;
}
.now-panel .watts-big {
  font-family: 'Instrument Serif', serif;
  font-size: 3.5rem; line-height: 1;
  color: var(--olive-50);
}
.now-panel .watts-unit { font-size: 1.1rem; color: var(--olive-300); }
.now-panel .context-row {
  display: flex; flex-wrap: wrap; gap: 1rem;
  margin-top: 1rem; padding-top: 1rem;
  border-top: 1px solid var(--olive-800);
}
.now-panel .ctx-item { flex: 1; min-width: 120px; }
.now-panel .ctx-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.07em; color: var(--olive-400); }
.now-panel .ctx-val { font-size: 1rem; font-weight: 600; color: var(--olive-100); }

/* ── Circuit bar list ── */
.circuit-bars { display: flex; flex-direction: column; gap: 8px; }
.circuit-row { display: flex; align-items: center; gap: 10px; }
.circuit-name {
  width: 130px; flex-shrink: 0;
  font-size: 0.82rem; font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.circuit-bar-wrap { flex: 1; background: var(--olive-100); border-radius: 4px; height: 10px; overflow: hidden; }
.circuit-bar      { height: 10px; border-radius: 4px; background: var(--olive-600); transition: width 0.4s; }
.circuit-bar.heat { background: var(--red); }
.circuit-bar.hot  { background: var(--amber); }
.circuit-val { width: 56px; text-align: right; font-size: 0.78rem; color: var(--text-light); }

/* ── Chart containers ── */
.chart-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
}
.chart-box h3 { font-size: 0.85rem; color: var(--text-light); margin-bottom: 1rem; font-family: 'Inter', sans-serif; font-weight: 600; }

/* ── Trend banner ── */
.trend-banner {
  display: flex; align-items: center; gap: 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px; padding: 0.85rem 1.25rem;
  font-size: 0.875rem;
}
.trend-banner .trend-icon { font-size: 1.3rem; }

/* ── Table ── */
.data-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.data-table th {
  text-align: left; padding: 9px 12px;
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.07em;
  color: var(--text-light); border-bottom: 1px solid var(--border);
  cursor: pointer; user-select: none; white-space: nowrap;
}
.data-table th:hover { color: var(--text); }
.data-table th.sort-asc::after  { content: " ↑"; }
.data-table th.sort-desc::after { content: " ↓"; }
.data-table td { padding: 9px 12px; border-bottom: 1px solid var(--border); }
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: var(--olive-50); }
.data-table a { color: var(--accent); text-decoration: none; font-weight: 500; }
.data-table a:hover { text-decoration: underline; }

/* ── Budget ring ── */
.budget-wrap { position: relative; width: 90px; height: 90px; flex-shrink: 0; }
.budget-wrap svg { transform: rotate(-90deg); }
.budget-wrap .budget-center {
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  font-family: 'Instrument Serif', serif;
}
.budget-wrap .budget-pct  { font-size: 1.25rem; font-weight: 700; line-height: 1; }
.budget-wrap .budget-label{ font-size: 0.55rem; color: var(--text-light); text-transform: uppercase; letter-spacing: 0.06em; }

/* ── Log page ── */
.log-entry {
  display: grid;
  grid-template-columns: 1fr auto auto auto;
  gap: 8px; align-items: center;
  padding: 7px 0; border-bottom: 1px solid var(--border);
  font-size: 0.82rem;
}
.log-entry:last-child { border-bottom: none; }
.log-ts   { color: var(--text-light); font-variant-numeric: tabular-nums; }
.log-kwh  { font-weight: 600; font-variant-numeric: tabular-nums; }
.log-cost { color: var(--text-light); font-variant-numeric: tabular-nums; }
.log-ch   { color: var(--text-light); font-size: 0.75rem; }

/* ── Back link ── */
.back-link { font-size: 0.85rem; color: var(--accent); text-decoration: none; display: inline-block; margin-bottom: 1.25rem; }
.back-link:hover { text-decoration: underline; }

/* ── Misc ── */
.pill {
  display: inline-block; padding: 2px 8px; border-radius: 20px;
  font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
}
.pill.projection { background: oklch(68% 0.15 75 / 0.15); color: oklch(42% 0.12 75); }

/* ── Breaker panel ── */
.panel-wrap {
  background: var(--olive-950);
  border-radius: 16px;
  padding: 1.5rem;
  margin-top: 1.5rem;
}
.panel-label {
  font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--olive-400); margin-bottom: 1rem;
  text-align: center;
}
.panel-mains {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  margin-bottom: 1.25rem;
}
.mains-card {
  background: var(--olive-800);
  border-radius: 10px; padding: 1rem 1.25rem;
}
.mains-card .mc-leg  { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--olive-400); margin-bottom: 4px; }
.mains-card .mc-w    { font-family: 'Instrument Serif', serif; font-size: 2rem; line-height: 1; color: var(--olive-50); }
.mains-card .mc-kwh  { font-size: 0.78rem; color: var(--olive-300); margin-top: 4px; }
.panel-bus {
  display: flex; align-items: center; gap: 10px; margin-bottom: 1rem;
}
.panel-bus-line {
  flex: 1; height: 2px; background: var(--olive-700);
}
.panel-bus-label {
  font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--olive-500);
}
.panel-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
}
.breaker {
  background: var(--olive-900);
  border: 1px solid var(--olive-700);
  border-radius: 8px;
  padding: 0.6rem 0.85rem;
  display: flex; align-items: center; gap: 8px;
  text-decoration: none; color: inherit;
  transition: background 0.15s, border-color 0.15s;
  min-width: 0;
}
.breaker:hover { background: var(--olive-700); border-color: var(--olive-500); }
.breaker.active-high { border-color: var(--amber); }
.breaker.active-heat { border-color: var(--red); }
.breaker-num {
  font-size: 0.6rem; color: var(--olive-500);
  width: 1.4rem; flex-shrink: 0; text-align: right;
}
.breaker-body { flex: 1; min-width: 0; }
.breaker-name {
  font-size: 0.78rem; font-weight: 500; color: var(--olive-100);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.breaker-watts {
  font-size: 0.7rem; color: var(--olive-400); margin-top: 2px;
}
.breaker-bar-wrap {
  width: 36px; flex-shrink: 0;
  background: var(--olive-800); border-radius: 3px; height: 32px;
  display: flex; align-items: flex-end; overflow: hidden;
}
.breaker-bar {
  width: 100%; background: var(--olive-500); border-radius: 3px;
  transition: height 0.4s;
}
.breaker.active-high .breaker-bar { background: var(--amber); }
.breaker.active-heat .breaker-bar { background: var(--red); }
.breaker.empty { opacity: 0.35; cursor: default; pointer-events: none; }
.breaker-note-tip {
  position: absolute; bottom: calc(100% + 6px); left: 50%;
  transform: translateX(-50%);
  background: var(--olive-950); color: var(--olive-100);
  font-size: 0.72rem; padding: 5px 10px; border-radius: 6px;
  white-space: nowrap; pointer-events: none;
  box-shadow: 0 2px 8px rgba(0,0,0,.4);
  opacity: 0; transition: opacity 0.15s;
  z-index: 10;
}
.breaker-note-tip::after {
  content: ''; position: absolute; top: 100%; left: 50%;
  transform: translateX(-50%);
  border: 5px solid transparent; border-top-color: var(--olive-950);
}
.breaker:hover .breaker-note-tip { opacity: 1; }
.breaker { position: relative; }
.breaker-amps { font-size: 0.6rem; color: var(--olive-500); margin-top: 1px; }

/* ── Panel edit page ── */
.panel-edit-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  margin-top: 1rem;
}
.slot-row {
  display: flex; gap: 8px; align-items: center;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 12px;
}
.slot-num { width: 2rem; font-size: 0.75rem; color: var(--text-light); flex-shrink: 0; font-weight: 600; }
.slot-row input, .slot-row select {
  font-size: 0.8rem; padding: 4px 6px; border-radius: 5px;
  border: 1px solid var(--border); background: var(--bg);
  color: var(--text); font-family: inherit;
}
.slot-row input.inp-label { width: 110px; }
.slot-row select { flex: 1; }
.slot-row input.inp-amps { width: 44px; }
.slot-row input.inp-note { flex: 1; }

/* ── Usage spreadsheet ── */
.usage-pct-bar {
  display: inline-block; height: 6px; border-radius: 3px;
  background: var(--olive-400); vertical-align: middle; margin-right: 6px;
}

/* ── Animations ── */
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(16px); }
  to   { opacity: 1; transform: translateY(0); }
}
.animate-fade-in { animation: fadeInUp 0.5s ease-out forwards; }

.hover-lift {
  transition: transform 0.25s cubic-bezier(0.4,0,0.2,1), box-shadow 0.25s cubic-bezier(0.4,0,0.2,1);
}
.hover-lift:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px -6px rgba(0,0,0,.10);
}

/* ── Skeleton loader ── */
@keyframes skeleton { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
.skeleton {
  background: linear-gradient(90deg, var(--olive-100) 25%, var(--olive-50) 50%, var(--olive-100) 75%);
  background-size: 200% 100%;
  animation: skeleton 1.5s infinite;
  border-radius: 6px;
}

/* ── Gradient text ── */
.gradient-text {
  background: linear-gradient(135deg, var(--olive-700) 0%, var(--olive-500) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* ── Glass surface ── */
.glass {
  background: rgba(247, 248, 244, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}

/* ── Reduced motion ── */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
"""

NAV_HTML = """
<nav class="topnav">
  <div class="inner">
    <div style="display:flex; align-items:center; gap:0.25rem;">
      <a href="/" class="brand" style="display:flex; align-items:center; gap:0.5rem; text-decoration:none;">
        <span style="width:28px; height:28px; background:var(--olive-600); border-radius:7px; display:inline-flex; align-items:center; justify-content:center; font-size:1rem; color:#fff; flex-shrink:0;">⚡</span>
        <span style="font-family:'Instrument Serif',serif; font-size:1rem; font-weight:700; color:var(--olive-900);">Energy</span>
      </a>
    </div>
    <div class="nav-links">
      <a href="/" class="{{ 'active' if active_page == 'dashboard' else '' }}">Dashboard</a>
      <a href="/circuits" class="{{ 'active' if active_page == 'circuits' else '' }}">Circuits</a>
      <a href="/trends" class="{{ 'active' if active_page == 'trends' else '' }}">Trends</a>
      <a href="/log" class="{{ 'active' if active_page == 'log' else '' }}">Log</a>
      <a href="/import" class="{{ 'active' if active_page == 'import' else '' }}">Import</a>
      <a href="/aqara" class="{{ 'active' if active_page == 'aqara' else '' }}">Aqara</a>
      <a href="/settings" class="{{ 'active' if active_page == 'settings' else '' }}">Settings</a>
    </div>
    <div style="font-size:0.8rem; color: var(--stone-500); display:flex; align-items:center; gap:10px;">
      <span style="font-size:0.65rem; color:var(--stone-400); font-variant-numeric:tabular-nums;">v{{ version }}</span>
      <a href="/log" style="display:flex; align-items:center; gap:6px; text-decoration:none; color:inherit;">
        <span class="status-dot {{ status_cls }}"></span>
        {{ status_label }}
      </a>
    </div>
  </div>
</nav>
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_hour(h: str) -> str:
    hour = int(h)
    if hour == 0:   return "12 AM"
    if hour < 12:   return f"{hour} AM"
    if hour == 12:  return "12 PM"
    return f"{hour - 12} PM"


def _status(last_ts: str):
    """Return (css_class, label, hours_old) from the most recent timestamp."""
    if last_ts == "N/A":
        return "dead", "No data", 999
    try:
        dt = datetime.fromisoformat(last_ts[:19])
        h  = (datetime.now() - dt).total_seconds() / 3600
        if h < 0.1:  return "live",  f"Live · {int(h*60)}m ago",  h
        if h < 1:    return "live",  f"Live · {int(h*60)}m ago",  h
        if h < 6:    return "stale", f"Stale · {h:.1f}h ago",     h
        return "dead", f"Offline · {h:.0f}h ago", h
    except Exception:
        return "dead", "Unknown", 999


def _delta_badge(pct, label: str, invert: bool = False) -> str:
    """Return an HTML delta badge. invert=True means lower is better."""
    if pct is None:
        return f'<span class="delta nodata">no prior data</span>'
    arrow = "↑" if pct > 0 else "↓"
    if invert:
        cls = "up" if pct > 0 else ("flat" if pct == 0 else "down")
    else:
        cls = "down" if pct < 0 else ("flat" if pct == 0 else "up")
    return f'<span class="delta {cls}">{arrow} {abs(pct):.1f}% vs {label}</span>'


def _watts_estimate(kwh_per_minute: float) -> float:
    """Convert kWh reading (1-minute scale) to approximate watts."""
    return kwh_per_minute * 60 * 1000


def _render(template: str, **ctx):
    return render_template_string(
        NAV_HTML + "\n<style>" + BASE_CSS + "</style>\n" + template,
        **ctx
    )


# ── Dashboard template ────────────────────────────────────────────────────────

DASH_HTML = """
<div class="page">

  <!-- ── Weather Strip ─────────────────────────────────────────────────── -->
  <div id="weather-strip" style="display:none; margin-bottom:0.75rem; overflow-x:auto;">
    <div id="weather-days" style="display:flex; gap:8px; min-width:max-content; padding:2px 0;"></div>
  </div>
  <style>
  .wx-card {
    background:var(--surface2); border:1px solid var(--border); border-radius:10px;
    padding:8px 12px; text-align:center; min-width:72px; flex-shrink:0;
  }
  .wx-card.wx-today { background:var(--olive-800); color:var(--olive-50); border-color:var(--olive-700); }
  .wx-card.wx-today .wx-label, .wx-card.wx-today .wx-lo { color:var(--olive-300); }
  .wx-label { font-size:0.65rem; text-transform:uppercase; letter-spacing:0.06em; color:var(--olive-700); margin-bottom:3px; }
  .wx-icon  { font-size:1.4rem; line-height:1; margin-bottom:2px; }
  .wx-hi    { font-size:0.95rem; font-weight:700; color:var(--olive-950); }
  .wx-lo    { font-size:0.75rem; color:var(--olive-700); }
  .wx-now   { font-size:0.7rem; margin-top:2px; opacity:0.8; }
  .wx-hvac  { height:3px; border-radius:2px; margin-top:5px; width:100%; }
  .wx-hvac.heat { background:rgba(220,60,40,0.65); }
  .wx-hvac.cool { background:rgba(60,140,220,0.65); }
  </style>
  <script>
  (function(){
    const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    const icons = {0:'☀️',1:'🌤️',2:'⛅',3:'☁️',45:'🌫️',48:'🌫️',51:'🌦️',53:'🌦️',55:'🌧️',
      61:'🌧️',63:'🌧️',65:'🌧️',71:'🌨️',73:'🌨️',75:'🌨️',77:'🌨️',80:'🌦️',81:'🌧️',82:'🌧️',
      85:'🌨️',86:'🌨️',95:'⛈️',96:'⛈️',99:'⛈️'};
    fetch('/api/weather').then(r=>r.json()).then(data=>{
      if(!data.days || !data.days.length) return;
      // Use browser's local date so Today/Tomorrow roll over at midnight automatically
      const todayStr = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD in local time
      const container = document.getElementById('weather-days');

      // Pre-compute estimated cold/hot hours per day using half-day approximation:
      // If max < 50 → 24h cold; if min < 50 <= max → 12h cold; else 0
      // If min > 80 → 24h hot;  if max > 80 >= min → 12h hot;  else 0
      function coldHours(d) {
        if (d.temp_max < 50) return 24;
        if (d.temp_min < 50) return 12;
        return 0;
      }
      function hotHours(d) {
        if (d.temp_min > 80) return 24;
        if (d.temp_max > 80) return 12;
        return 0;
      }

      data.days.forEach((d, i)=>{
        const dayDate = new Date(d.date + 'T12:00:00'); // noon to avoid DST edge cases
        const diff = Math.round((dayDate - new Date(todayStr + 'T12:00:00')) / 86400000);
        const label = diff === 0 ? 'Today' : (diff === 1 ? 'Tomorrow' : DAYS[dayDate.getDay()]);
        const isToday = diff === 0;

        // 48-hour HVAC pressure: this day + next day
        const next = data.days[i + 1] || d; // if last day, double-count current
        const totalCold = coldHours(d) + coldHours(next);
        const totalHot  = hotHours(d)  + hotHours(next);
        // Show indicator if >= 24 of the 48h window is cold or hot (≥50% of window)
        const hvacClass = totalCold >= 24 ? 'heat' : (totalHot >= 24 ? 'cool' : '');

        const el = document.createElement('div');
        el.className = 'wx-card' + (isToday ? ' wx-today' : '');
        el.innerHTML = `
          <div class="wx-label">${label}</div>
          <div class="wx-icon">${icons[d.weathercode] || '🌡️'}</div>
          <div class="wx-hi">${Math.round(d.temp_max)}°</div>
          <div class="wx-lo">${Math.round(d.temp_min)}°</div>
          ${isToday && data.current_temp !== null ? `<div class="wx-now">${Math.round(data.current_temp)}° now</div>` : ''}
          ${hvacClass ? `<div class="wx-hvac ${hvacClass}" title="${hvacClass==='heat'?'Heating likely ~'+totalCold+'h':'Cooling likely ~'+totalHot+'h'} over 48h"></div>` : '<div class="wx-hvac"></div>'}
        `;
        container.appendChild(el);
      });
      document.getElementById('weather-strip').style.display='';
    }).catch(()=>{});
  })();
  </script>

  <!-- ── Now Panel ─────────────────────────────────────────────────────── -->
  <div class="now-panel">
    <div style="display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:1rem;">
      <div>
        <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--olive-400); margin-bottom:0.35rem;">
          Right Now &mdash; {{ ctx.window_minutes }}-min window
        </div>
        <div class="watts-big">
          {{ "%.1f"|format(current_watts) }}
          <span class="watts-unit">W</span>
        </div>
        <div style="font-size:0.85rem; color:var(--olive-300); margin-top:0.3rem;">
          {{ "%.3f"|format(ctx.current_kwh or 0) }} kWh &bull;
          ${{ "%.3f"|format((ctx.current_kwh or 0) * 30 * 24 * rate) }} projected/mo
        </div>
      </div>

      <!-- Budget ring -->
      <div style="display:flex; align-items:center; gap:1.25rem;">
        <div class="budget-wrap">
          <svg width="90" height="90" viewBox="0 0 90 90">
            <circle cx="45" cy="45" r="38" fill="none" stroke="oklch(28% 0.035 110)" stroke-width="8"/>
            <circle cx="45" cy="45" r="38" fill="none"
              stroke="{{ '#FF453A' if budget_pct > 100 else '#9ca865' }}"
              stroke-width="8"
              stroke-dasharray="{{ [budget_pct/100*239, 239]|min|round(1) }} 239"
              stroke-linecap="round"/>
          </svg>
          <div class="budget-center">
            <div class="budget-pct" style="color:{{ '#FF453A' if budget_pct > 100 else 'var(--text)' }}">
              {{ "%.0f"|format(budget_pct) }}%
            </div>
            <div class="budget-label">budget</div>
          </div>
        </div>
        <div>
          <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.07em; color:var(--olive-400);">Monthly</div>
          <div style="font-size:1.4rem; font-family:'Instrument Serif',serif; color:var(--olive-50);">
            ${{ "%.2f"|format(monthly_projected) }}
          </div>
          <div style="font-size:0.75rem; color:var(--olive-400);">projected of ${{ budget|int }}/mo</div>
        </div>
      </div>
    </div>

    <!-- Context row -->
    <div class="context-row">
      <div class="ctx-item">
        <div class="ctx-label">Yesterday same window</div>
        <div class="ctx-val">
          {% if ctx.yesterday_kwh %}
            {{ "%.3f"|format(ctx.yesterday_kwh) }} kWh
            {{ delta_yd|safe }}
          {% else %}
            <span style="color:var(--olive-500); font-size:0.8rem; font-style:italic;">no data yet</span>
          {% endif %}
        </div>
      </div>
      <div class="ctx-item">
        <div class="ctx-label">Last week same window</div>
        <div class="ctx-val">
          {% if ctx.last_week_kwh %}
            {{ "%.3f"|format(ctx.last_week_kwh) }} kWh
            {{ delta_wk|safe }}
          {% else %}
            <span style="color:var(--olive-500); font-size:0.8rem; font-style:italic;">no data yet</span>
          {% endif %}
        </div>
      </div>
      <div class="ctx-item">
        <div class="ctx-label">Last month same window</div>
        <div class="ctx-val">
          {% if ctx.last_month_kwh %}
            {{ "%.3f"|format(ctx.last_month_kwh) }} kWh
            {{ delta_mo|safe }}
          {% else %}
            <span style="color:var(--olive-500); font-size:0.8rem; font-style:italic;">no data yet</span>
          {% endif %}
        </div>
      </div>
      <div class="ctx-item">
        <div class="ctx-label">Rate</div>
        <div class="ctx-val">${{ "%.4f"|format(rate) }}/kWh</div>
      </div>
    </div>
  </div>

  <!-- ── Active Circuits (multi-view) ─────────────────────────────────── -->
  <div class="section">
    <div class="section-head">
      <h2>Active Circuits</h2>
      <div style="display:flex; align-items:center; gap:10px;">
        <span class="section-sub">Last {{ ctx.window_minutes }} min</span>
        <div class="view-toggle" id="viewToggle">
          <button class="vt-btn" data-view="panel" title="Breaker panel">⊞</button>
          <button class="vt-btn" data-view="bars" title="Bar list">≡</button>
          <button class="vt-btn" data-view="grid" title="Card grid">▦</button>
        </div>
      </div>
    </div>

    <!-- Bars view -->
    <div id="view-bars" style="display:none;">
      <div class="card">
        <div class="circuit-bars">
          {% for c in top_circuits %}
          {% set bar_cls = 'heat' if c.pct > 40 else ('hot' if c.pct > 20 else '') %}
          <div class="circuit-row">
            <a class="circuit-name" href="/circuit/{{ c.channel_name|urlencode }}" title="{{ c.channel_name }}">{{ c.channel_name }}</a>
            <div class="circuit-bar-wrap">
              <div class="circuit-bar {{ bar_cls }}" style="width:{{ c.pct|round(1) }}%"></div>
            </div>
            <div class="circuit-val">{{ "%.0f"|format(c.watts) }} W</div>
          </div>
          {% endfor %}
        </div>
      </div>
    </div>

    <!-- Panel view: 50% panel + 50% metrics sidebar -->
    <div id="view-panel" style="display:none;">
      <div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px; align-items:start;">

        <!-- Left: breaker panel -->
        <div class="panel-wrap" style="margin-top:0;">
          {% if dash_mains %}
          <div class="panel-label">{{ panel_label }} — Live</div>
          {% for m in dash_mains %}
          {% if m.is_total %}
          <div class="mains-card" style="margin-bottom:10px; background:var(--olive-700);">
            <div class="mc-leg">{{ m.label }}</div>
            <div class="mc-w" style="font-size:2.2rem;">{{ "%.0f"|format(m.watts) }} <span style="font-size:0.9rem;color:var(--olive-300)">W</span>
              <span style="font-size:0.8rem; color:var(--olive-300); margin-left:10px;">${{ "%.4f"|format(m.cost_24h / (m.kwh_24h or 1)) }}/kWh</span>
            </div>
            <div class="mc-kwh">{{ "%.2f"|format(m.kwh_24h) }} kWh today &bull; <strong>${{ "%.2f"|format(m.cost_24h) }}</strong></div>
          </div>
          <div class="panel-mains" style="margin-bottom:10px;">
          {% elif loop.last %}
            <div class="mains-card">
              <div class="mc-leg">{{ m.label }}</div>
              <div class="mc-w">{{ "%.0f"|format(m.watts) }} <span style="font-size:1rem;color:var(--olive-400)">W</span></div>
              <div class="mc-kwh">{{ "%.2f"|format(m.kwh_24h) }} kWh &bull; ${{ "%.2f"|format(m.cost_24h) }}</div>
            </div>
          </div>
          {% else %}
            <div class="mains-card">
              <div class="mc-leg">{{ m.label }}</div>
              <div class="mc-w">{{ "%.0f"|format(m.watts) }} <span style="font-size:1rem;color:var(--olive-400)">W</span></div>
              <div class="mc-kwh">{{ "%.2f"|format(m.kwh_24h) }} kWh &bull; ${{ "%.2f"|format(m.cost_24h) }}</div>
            </div>
          {% endif %}
          {% endfor %}
          <div class="panel-bus"><div class="panel-bus-line"></div><div class="panel-bus-label">Bus bar</div><div class="panel-bus-line"></div></div>
          {% endif %}
          <div class="panel-grid">
            {% for b in dash_breakers %}
            {% if b.channel_name %}
            <a class="breaker {{ b.cls }}" href="/circuit/{{ b.channel_name|urlencode }}">
              <div class="breaker-num">{{ b.slot }}</div>
              <div class="breaker-body">
                <div class="breaker-name">{{ b.label }}</div>
                <div class="breaker-watts">{{ "%.0f"|format(b.watts) }} W</div>
              </div>
              <div class="breaker-bar-wrap"><div class="breaker-bar" style="height:{{ b.bar_pct }}%"></div></div>
              {% if b.note %}<div class="breaker-note-tip">{{ b.note }}</div>{% endif %}
            </a>
            {% else %}
            <div class="breaker empty">
              <div class="breaker-num">{{ b.slot }}</div>
              <div class="breaker-body"><div class="breaker-name" style="color:var(--olive-700)">—</div></div>
            </div>
            {% endif %}
            {% endfor %}
          </div>
        </div>

        <!-- Right: 2-column metrics grid -->
        <div style="display:flex; flex-direction:column; gap:10px;">

          <!-- Row 1: Live + Avg -->
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div class="card">
              <div class="card-label">Live</div>
              <div class="card-value" style="font-size:1.6rem;">{{ "%.0f"|format(current_watts) }}<span class="unit">W</span></div>
              <div class="card-meta">right now</div>
            </div>
            <div class="card">
              <div class="card-label">24h Avg</div>
              <div class="card-value" style="font-size:1.6rem;">{{ "%.0f"|format((total_24h.total_kwh or 0) * 1000 / 24) }}<span class="unit">W</span></div>
              <div class="card-meta">{{ "%.2f"|format(total_24h.total_kwh or 0) }} kWh</div>
            </div>
          </div>

          <!-- Row 2: 24h Cost + MTD -->
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div class="card">
              <div class="card-label">24h Cost</div>
              <div class="card-value">${{ "%.2f"|format((total_24h.total_cents or 0) / 100) }}</div>
              <div class="card-meta">${{ "%.4f"|format(rate) }}/kWh</div>
            </div>
            <div class="card">
              <div class="card-label">Month-to-Date</div>
              <div class="card-value">${{ "%.2f"|format((month_comparison.this_month.total_cents or 0) / 100) if month_comparison.this_month else '0.00' }}</div>
              <div class="card-meta">{{ "%.1f"|format(month_comparison.this_month.total_kwh or 0) if month_comparison.this_month else '0' }} kWh</div>
            </div>
          </div>

          <!-- Row 3: Projected + Budget -->
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div class="card">
              <div class="card-label">Projected Month</div>
              <div class="card-value">${{ "%.2f"|format(monthly_projected) }}</div>
              <div class="card-meta">estimated at current rate</div>
            </div>
            <div class="card">
              <div class="card-label">Budget</div>
              <div class="card-value" style="font-size:1.4rem;">{{ "%.0f"|format(budget_pct) }}<span class="unit">%</span></div>
              <div style="height:6px; background:var(--surface2); border-radius:3px; margin:4px 0;">
                <div style="height:6px; border-radius:3px; width:{{ budget_pct }}%;
                  background:{{ 'var(--red-400,#f87171)' if budget_pct > 90 else ('var(--amber-400,#fbbf24)' if budget_pct > 70 else 'var(--olive-500)') }};"></div>
              </div>
              <div class="card-meta">of ${{ budget }} budget</div>
            </div>
          </div>

          <!-- Row 4: Biggest Load (full width) -->
          <div class="card">
            <div class="card-label">Biggest Load (24h)</div>
            <div style="display:flex; align-items:baseline; gap:8px;">
              <div class="card-value" style="font-size:1.3rem;">{{ biggest_circuit.channel_name if biggest_circuit else '—' }}</div>
              <div class="card-meta">{{ "%.2f"|format(biggest_circuit.total_kwh or 0) }} kWh &bull; {{ "%.0f"|format(biggest_circuit.pct or 0) }}% of total</div>
            </div>
          </div>

          <!-- Row 5: Top active circuits mini-list -->
          <div class="card">
            <div class="card-label" style="margin-bottom:8px;">Top Active Circuits</div>
            {% for c in top_circuits[:5] %}
            <div style="display:flex; align-items:center; gap:8px; padding:3px 0;
                        border-bottom:{% if not loop.last %}1px solid var(--border){% else %}none{% endif %};">
              <div style="flex:1; font-size:0.82rem; font-weight:500; color:var(--text);
                          white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                <a href="/circuit/{{ c.channel_name|urlencode }}" style="color:inherit; text-decoration:none;">{{ c.channel_name }}</a>
              </div>
              <div style="font-size:0.82rem; font-weight:700; color:var(--text); min-width:48px; text-align:right;">{{ "%.0f"|format(c.watts) }} W</div>
              <div style="width:60px; height:5px; background:var(--surface2); border-radius:3px; flex-shrink:0;">
                <div style="height:5px; border-radius:3px; width:{{ c.pct|round(1) }}%;
                  background:{{ 'var(--olive-400)' if c.pct < 20 else ('var(--amber-500,#f59e0b)' if c.pct < 40 else 'var(--red-400,#f87171)') }};"></div>
              </div>
              <div style="font-size:0.72rem; color:var(--text-light); min-width:32px; text-align:right;">{{ "%.0f"|format(c.pct) }}%</div>
            </div>
            {% else %}
            <div style="color:var(--text-light); font-size:0.8rem; font-style:italic;">No circuit data</div>
            {% endfor %}
          </div>

        </div><!-- /right -->
      </div>
    </div>

    <!-- Grid view -->
    <div id="view-grid" style="display:none;">
      <div class="grid-3">
        {% for c in top_circuits %}
        <a href="/circuit/{{ c.channel_name|urlencode }}" style="text-decoration:none;">
          <div class="card" style="display:flex; flex-direction:column; gap:4px;">
            <div class="card-label">{{ c.channel_name }}</div>
            <div class="card-value" style="font-size:1.5rem;">{{ "%.0f"|format(c.watts) }}<span class="unit">W</span></div>
            <div class="card-meta">{{ "%.1f"|format(c.pct) }}% of load</div>
          </div>
        </a>
        {% endfor %}
      </div>
    </div>
  </div>

  <style>
  .view-toggle { display:flex; gap:3px; background:var(--surface2); border-radius:8px; padding:3px; }
  .vt-btn {
    background:none; border:none; cursor:pointer; padding:4px 9px;
    border-radius:6px; font-size:1rem; color:var(--text-light);
    transition: background 0.15s, color 0.15s;
  }
  .vt-btn.active { background:var(--olive-800); color:var(--olive-50); }
  .vt-btn:hover:not(.active) { background:var(--border); }
  </style>
  <script>
  (function(){
    const views = ['bars','panel','grid'];
    const saved = localStorage.getItem('circuitView') || 'panel';
    function show(v) {
      views.forEach(n => {
        document.getElementById('view-'+n).style.display = n===v ? '' : 'none';
        document.querySelector('[data-view="'+n+'"]').classList.toggle('active', n===v);
      });
      // Panel view has inline KPI sidebar — hide the standalone 24h section to avoid duplication
      const s24 = document.getElementById('section-24h');
      if (s24) s24.style.display = v === 'panel' ? 'none' : '';
      localStorage.setItem('circuitView', v);
    }
    document.querySelectorAll('.vt-btn').forEach(btn => {
      btn.addEventListener('click', () => show(btn.dataset.view));
    });
    show(saved);
  })();
  </script>

  <!-- ── KPI row ────────────────────────────────────────────────────────── -->
  <div class="section" id="section-24h">
    <div class="section-head">
      <h2>24-Hour Summary</h2>
      <span class="section-sub">Since midnight yesterday</span>
    </div>
    <div class="grid-4">
      <div class="card">
        <div class="card-label">Usage</div>
        <div class="card-value">{{ "%.2f"|format(total_24h.total_kwh or 0) }}<span class="unit">kWh</span></div>
        <div class="card-meta">{{ "%.0f"|format((total_24h.total_kwh or 0) * 1000 / 24) }} W avg</div>
      </div>
      <div class="card">
        <div class="card-label">Cost</div>
        <div class="card-value">${{ "%.2f"|format((total_24h.total_cents or 0) / 100) }}</div>
        <div class="card-meta">at ${{ "%.4f"|format(rate) }}/kWh</div>
      </div>
      <div class="card">
        <div class="card-label">Biggest Load</div>
        <div class="card-value" style="font-size:1.4rem;">{{ biggest_circuit.channel_name if biggest_circuit else '—' }}</div>
        <div class="card-meta">{{ "%.2f"|format(biggest_circuit.total_kwh or 0) }} kWh ({{ "%.0f"|format(biggest_circuit.pct or 0) }}% of total)</div>
      </div>
      <div class="card">
        <div class="card-label">Month-to-Date</div>
        <div class="card-value">${{ "%.2f"|format((month_comparison.this_month.total_cents or 0) / 100) if month_comparison.this_month else '$0.00' }}</div>
        <div class="card-meta">
          {{ "%.1f"|format(month_comparison.this_month.total_kwh or 0) }} kWh this month
          {% if month_comparison.last_month %}
          &bull; {{ delta_month|safe }}
          {% endif %}
        </div>
      </div>
    </div>
  </div>

  <!-- ── Trend banner ─────────────────────────────────────────────────── -->
  {% if trend.slope is not none %}
  <div class="section">
    <div class="trend-banner">
      <span class="trend-icon">{{ '📈' if trend.slope > 0.1 else ('📉' if trend.slope < -0.1 else '➡️') }}</span>
      <span>
        <strong>{{ '14-day trend: rising' if trend.slope > 0.1 else ('14-day trend: falling' if trend.slope < -0.1 else '14-day trend: stable') }}</strong>
        &mdash;
        usage changing {{ '%+.2f'|format(trend.slope) }} kWh/day.
        Avg {{ "%.1f"|format(trend.avg_kwh) }} kWh/day.
        Best: <strong>{{ trend.best_day.day }}</strong> ({{ "%.1f"|format(trend.best_day.total_kwh) }} kWh) &bull;
        Peak: <strong>{{ trend.worst_day.day }}</strong> ({{ "%.1f"|format(trend.worst_day.total_kwh) }} kWh)
      </span>
    </div>
  </div>
  {% endif %}

  <!-- ── Charts row ────────────────────────────────────────────────────── -->
  <div class="section">
    <div class="grid-2">
      <div class="chart-box">
        <h3>Daily Usage — Last 30 Days</h3>
        <canvas id="dailyChart" height="180"></canvas>
      </div>
      <div class="chart-box">
        <h3>Hourly Pattern — Last 7 Days</h3>
        <canvas id="hourlyChart" height="180"></canvas>
      </div>
    </div>
  </div>

  <!-- ── Month comparison ──────────────────────────────────────────────── -->
  <div class="section">
    <div class="section-head">
      <h2>Month Comparison</h2>
    </div>
    <div class="grid-3">
      <div class="card">
        <div class="card-label">This Month</div>
        <div class="card-value">{{ "%.1f"|format(month_comparison.this_month.total_kwh or 0) }}<span class="unit">kWh</span></div>
        <div class="card-meta">${{ "%.2f"|format((month_comparison.this_month.total_cents or 0) / 100) }}</div>
      </div>
      <div class="card">
        <div class="card-label">Last Month</div>
        <div class="card-value">{{ "%.1f"|format(month_comparison.last_month.total_kwh or 0) }}<span class="unit">kWh</span></div>
        <div class="card-meta">${{ "%.2f"|format((month_comparison.last_month.total_cents or 0) / 100) }}</div>
      </div>
      {% if month_comparison.this_month and month_comparison.last_month %}
      <div class="card">
        <div class="card-label">Change</div>
        {% set ch = ((month_comparison.this_month.total_kwh or 0) - (month_comparison.last_month.total_kwh or 0)) / (month_comparison.last_month.total_kwh or 1) * 100 %}
        <div class="card-value" style="font-size:2rem; color:{{ 'var(--green)' if ch < 0 else 'var(--red)' }}">
          {{ '%+.1f'|format(ch) }}%
        </div>
        <div class="card-meta">{{ '↓ lower' if ch < 0 else '↑ higher' }} than last month</div>
      </div>
      {% endif %}
    </div>
  </div>

  <!-- ── Peak times ────────────────────────────────────────────────────── -->
  <div class="section">
    <div class="section-head">
      <h2>Peak Times</h2>
      <span class="section-sub">30-day average</span>
    </div>
    <div class="grid-2">
      <div class="card">
        <div class="card-label" style="margin-bottom:0.75rem;">Peak Hours</div>
        {% for h in peak_usage.peak_hours[:5] %}
        <div style="display:flex; justify-content:space-between; align-items:center; padding:5px 0; border-bottom:1px solid var(--border);">
          <span style="font-weight:500;">{{ h.hour_label }}</span>
          <span style="font-size:0.8rem; color:var(--text-light);">{{ "%.3f"|format(h.avg_kwh) }} kWh avg</span>
        </div>
        {% endfor %}
      </div>
      <div class="card">
        <div class="card-label" style="margin-bottom:0.75rem;">Peak Days</div>
        {% for d in peak_usage.peak_days[:5] %}
        <div style="display:flex; justify-content:space-between; align-items:center; padding:5px 0; border-bottom:1px solid var(--border);">
          <span style="font-weight:500;">{{ d.day }}</span>
          <span style="font-size:0.8rem; color:var(--text-light);">{{ "%.3f"|format(d.avg_kwh) }} kWh avg</span>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <div style="margin-top:2.5rem; padding-top:1rem; border-top:1px solid var(--border); font-size:0.75rem; color:var(--text-light);">
    Energy Monitor &bull; Updated: {{ last_updated }} &bull; ${{ "%.4f"|format(rate) }}/kWh
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const daily  = {{ daily_json|tojson }};
const hourly = {{ hourly_json|tojson }};

function oliveChart(id, labels, data, color) {
  const s = getComputedStyle(document.documentElement);
  const textLight = s.getPropertyValue('--olive-600').trim() || '#666';
  const borderCol = s.getPropertyValue('--olive-200').trim() || '#ddd';
  new Chart(document.getElementById(id), {
    type: 'bar',
    data: {
      labels,
      datasets: [{ data, backgroundColor: color, borderRadius: 3, borderSkipped: false }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false }, tooltip: {
        callbacks: { label: ctx => ` ${ctx.raw.toFixed(2)} kWh` }
      }},
      scales: {
        x: { ticks: { color: textLight, font: { size: 10 }, maxRotation: 45 }, grid: { color: borderCol } },
        y: { beginAtZero: true, ticks: { color: textLight, font: { size: 10 } }, grid: { color: borderCol } }
      }
    }
  });
}

oliveChart('dailyChart',
  daily.map(d => d.day.slice(5)),
  daily.map(d => d.total_kwh),
  'oklch(35% 0.045 110)');

oliveChart('hourlyChart',
  hourly.map(d => d.hour.slice(11,16)),
  hourly.map(d => d.total_kwh),
  'oklch(42% 0.055 110)');

// Auto-refresh the page every 60 s so "right now" stays current
setTimeout(() => location.reload(), 60000);
</script>
"""


# ── Circuits list template ────────────────────────────────────────────────────

CIRCUITS_HTML = """
<div class="page">

  <!-- ── Panel totals ──────────────────────────────────────────────────── -->
  <div class="panel-wrap">
    <div class="panel-label">{{ panel_label }}</div>
    {% for m in mains %}
    {% if m.is_total %}
    <div class="mains-card" style="margin-bottom:10px; background:var(--olive-700);">
      <div class="mc-leg">{{ m.label }}</div>
      <div class="mc-w" style="font-size:2.5rem;">{{ "%.0f"|format(m.watts) }} <span style="font-size:1rem;color:var(--olive-300)">W</span>
        <span style="font-size:0.9rem; color:var(--olive-300); margin-left:12px;">${{ "%.4f"|format(m.cost_24h / (m.kwh_24h or 1)) }}/kWh</span>
      </div>
      <div class="mc-kwh">{{ "%.2f"|format(m.kwh_24h) }} kWh today &bull; <strong>${{ "%.2f"|format(m.cost_24h) }}</strong></div>
    </div>
    <div class="panel-mains" style="margin-bottom:10px;">
    {% elif loop.last %}
      <div class="mains-card">
        <div class="mc-leg">{{ m.label }}</div>
        <div class="mc-w">{{ "%.0f"|format(m.watts) }} <span style="font-size:1rem;color:var(--olive-400)">W</span></div>
        <div class="mc-kwh">{{ "%.2f"|format(m.kwh_24h) }} kWh today &bull; ${{ "%.2f"|format(m.cost_24h) }}</div>
      </div>
    </div>
    {% else %}
      <div class="mains-card">
        <div class="mc-leg">{{ m.label }}</div>
        <div class="mc-w">{{ "%.0f"|format(m.watts) }} <span style="font-size:1rem;color:var(--olive-400)">W</span></div>
        <div class="mc-kwh">{{ "%.2f"|format(m.kwh_24h) }} kWh today &bull; ${{ "%.2f"|format(m.cost_24h) }}</div>
      </div>
    {% endif %}
    {% endfor %}

    <div class="panel-bus">
      <div class="panel-bus-line"></div>
      <div class="panel-bus-label">Bus bar &bull; {{ panel_slots }} slots</div>
      <div class="panel-bus-line"></div>
    </div>

    <!-- Two-column breaker grid — slot 1 top-left, slot 2 top-right, etc. -->
    <div class="panel-grid">
      {% for b in breakers %}
      {% if b.channel_name %}
      <a class="breaker {{ b.cls }}" href="/circuit/{{ b.channel_name|urlencode }}">
        <div class="breaker-num">{{ b.slot }}</div>
        <div class="breaker-body">
          <div class="breaker-name">{{ b.label }}</div>
          <div class="breaker-watts">{{ "%.0f"|format(b.watts) }} W{% if b.amps %} &bull; {{ b.amps }}A{% endif %}</div>
          {% if b.amps %}<div class="breaker-amps"></div>{% endif %}
        </div>
        <div class="breaker-bar-wrap">
          <div class="breaker-bar" style="height:{{ b.bar_pct }}%"></div>
        </div>
        {% if b.note %}
        <div class="breaker-note-tip">{{ b.note }}</div>
        {% endif %}
      </a>
      {% else %}
      <div class="breaker empty">
        <div class="breaker-num">{{ b.slot }}</div>
        <div class="breaker-body">
          <div class="breaker-name" style="color:var(--olive-700)">—</div>
        </div>
      </div>
      {% endif %}
      {% endfor %}
    </div>

    <div style="text-align:right; margin-top:0.75rem;">
      <a href="/panel" style="font-size:0.75rem; color:var(--olive-400); text-decoration:none;">
        ✏️ Edit panel layout
      </a>
    </div>
  </div>

  <!-- ── Usage spreadsheet ─────────────────────────────────────────────── -->
  <div class="section-head section" style="margin-top:2rem;">
    <h2>Usage by Period</h2>
    <span class="section-sub">Click any column header to sort</span>
  </div>
  <div class="card" style="padding:0; overflow:hidden;">
    <table class="data-table" id="usageTable">
      <thead>
        <tr>
          <th data-col="0">Circuit</th>
          <th data-col="1" class="sort-desc">Today kWh</th>
          <th data-col="2">Today $</th>
          <th data-col="3">Week kWh</th>
          <th data-col="4">Week $</th>
          <th data-col="5">Month kWh</th>
          <th data-col="6">Month $</th>
        </tr>
      </thead>
      <tbody>
        {% for r in usage_rows %}
        <tr>
          <td><a href="/circuit/{{ r.channel_name|urlencode }}">{{ r.display_name|e }}</a></td>
          <td>
            <span class="usage-pct-bar" style="width:{{ r.day_bar }}px"></span>{{ "%.2f"|format(r.day_kwh) }}
          </td>
          <td>${{ "%.2f"|format(r.day_cost) }}</td>
          <td>
            <span class="usage-pct-bar" style="width:{{ r.week_bar }}px"></span>{{ "%.2f"|format(r.week_kwh) }}
          </td>
          <td>${{ "%.2f"|format(r.week_cost) }}</td>
          <td>
            <span class="usage-pct-bar" style="width:{{ r.month_bar }}px"></span>{{ "%.2f"|format(r.month_kwh) }}
          </td>
          <td>${{ "%.2f"|format(r.month_cost) }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<script>
(function(){
  const t = document.getElementById('usageTable');
  if(!t) return;
  let col=1, asc=false;
  function applySort(c, a) {
    t.querySelectorAll('th').forEach(h => h.classList.remove('sort-asc','sort-desc'));
    t.querySelectorAll('th')[c].classList.add(a?'sort-asc':'sort-desc');
    const tb = t.tBodies[0];
    Array.from(tb.rows).sort((ra,rb) => {
      const av = ra.cells[c].textContent.replace(/[$\u2007 ]/g,'').trim();
      const bv = rb.cells[c].textContent.replace(/[$\u2007 ]/g,'').trim();
      const an = parseFloat(av), bn = parseFloat(bv);
      const cmp = isNaN(an) ? av.localeCompare(bv) : an-bn;
      return a ? cmp : -cmp;
    }).forEach(r => tb.appendChild(r));
  }
  t.querySelectorAll('th[data-col]').forEach(th => {
    th.style.cursor='pointer';
    th.addEventListener('click', () => {
      const c = parseInt(th.dataset.col);
      asc = (col===c) ? !asc : false; col=c;
      applySort(c, asc);
    });
  });
  applySort(col, asc);
})();
</script>
"""

# ── Circuit detail template ───────────────────────────────────────────────────

CIRCUIT_HTML = """
<div class="page">
  <a class="back-link" href="/circuits">&#8592; All Circuits</a>
  <h1 style="font-size:2rem; margin-bottom:1rem;">{{ circuit_name }}</h1>

  <div class="periods" style="display:flex; gap:8px; margin-bottom:1.5rem; flex-wrap:wrap;">
    {% for p, label in [('hour','Hour'),('day','Day'),('week','Week'),('month','Month'),('year','Year')] %}
    <a href="/circuit/{{ circuit_url }}/{{ p }}"
       style="padding:7px 16px; border-radius:8px; font-size:0.85rem; text-decoration:none;
              background:{{ 'var(--olive-950)' if period==p else 'var(--surface)' }};
              color:{{ 'var(--olive-50)' if period==p else 'var(--text)' }};
              border:1px solid {{ 'var(--olive-950)' if period==p else 'var(--border)' }};">
      {{ label }}
    </a>
    {% endfor %}
  </div>

  <!-- Context comparison for this circuit -->
  <div class="grid-4" style="margin-bottom:1.5rem;">
    <div class="card">
      <div class="card-label">Total kWh</div>
      <div class="card-value">{{ "%.2f"|format(total.total_kwh or 0) }}</div>
    </div>
    <div class="card">
      <div class="card-label">Total Cost</div>
      <div class="card-value">${{ "%.2f"|format((total.total_cents or 0)/100) }}</div>
    </div>
    <div class="card">
      <div class="card-label">Readings</div>
      <div class="card-value">{{ total.readings or 0 }}</div>
      <div class="card-meta">{{ "%.1f"|format(60 / (poll_interval)) }} polls/hr</div>
    </div>
    <div class="card">
      <div class="card-label">Date Range</div>
      <div class="card-value" style="font-size:1rem; padding-top:4px;">
        {{ total.first_reading[:10] if total.first_reading else 'N/A' }}<br>
        <span style="color:var(--text-light)">to</span>
        {{ total.last_reading[:10] if total.last_reading else 'N/A' }}
      </div>
    </div>
  </div>

  <!-- Yesterday/last week comparison -->
  {% if ctx %}
  <div class="grid-3" style="margin-bottom:1.5rem;">
    <div class="card">
      <div class="card-label">Yesterday same window</div>
      <div class="card-value" style="font-size:1.5rem;">
        {% if ctx.yesterday_kwh %}{{ "%.3f"|format(ctx.yesterday_kwh) }} kWh{% else %}&mdash;{% endif %}
      </div>
      <div class="card-meta">{{ delta_yd|safe }}</div>
    </div>
    <div class="card">
      <div class="card-label">Last week same window</div>
      <div class="card-value" style="font-size:1.5rem;">
        {% if ctx.last_week_kwh %}{{ "%.3f"|format(ctx.last_week_kwh) }} kWh{% else %}&mdash;{% endif %}
      </div>
      <div class="card-meta">{{ delta_wk|safe }}</div>
    </div>
    <div class="card">
      <div class="card-label">Last month same window</div>
      <div class="card-value" style="font-size:1.5rem;">
        {% if ctx.last_month_kwh %}{{ "%.3f"|format(ctx.last_month_kwh) }} kWh{% else %}&mdash;{% endif %}
      </div>
      <div class="card-meta">{{ delta_mo|safe }}</div>
    </div>
  </div>
  {% endif %}

  <div class="chart-box">
    <h3>Usage — {{ period|capitalize }} view</h3>
    <canvas id="usageChart" height="180"></canvas>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const d = {{ chart_json|tojson }};
const s = getComputedStyle(document.documentElement);
new Chart(document.getElementById('usageChart'), {
  type: 'bar',
  data: {
    labels: d.map(x => x.period),
    datasets: [{ data: d.map(x => x.total_kwh),
      backgroundColor: 'oklch(35% 0.045 110)', borderRadius:3 }]
  },
  options: {
    responsive:true,
    plugins:{ legend:{display:false}, tooltip:{callbacks:{label:c=>` ${c.raw.toFixed(3)} kWh`}}},
    scales:{
      x:{ticks:{color:s.getPropertyValue('--olive-600').trim(), font:{size:10}},
         grid:{color:s.getPropertyValue('--olive-200').trim()}},
      y:{beginAtZero:true, ticks:{color:s.getPropertyValue('--olive-600').trim(), font:{size:10}},
         grid:{color:s.getPropertyValue('--olive-200').trim()}}
    }
  }
});
</script>
"""


# ── Trends page template ──────────────────────────────────────────────────────

TRENDS_HTML = """
<div class="page">
  <div class="section-head" style="margin-top:1.5rem;">
    <h2>Trends</h2>
    <span class="section-sub">14-day usage analysis</span>
  </div>

  {% if trend.slope is not none %}
  <div class="trend-banner" style="margin-bottom:1.5rem;">
    <span class="trend-icon">{{ '📈' if trend.slope > 0.1 else ('📉' if trend.slope < -0.1 else '➡️') }}</span>
    <span>
      Usage is <strong>{{ 'rising' if trend.slope > 0.1 else ('falling' if trend.slope < -0.1 else 'stable') }}</strong>
      &mdash; {{ '%+.2f'|format(trend.slope) }} kWh/day.
      Daily average: <strong>{{ "%.1f"|format(trend.avg_kwh) }} kWh</strong>
      (${{ "%.2f"|format(trend.avg_kwh * rate) }}/day).
    </span>
  </div>

  <div class="grid-4" style="margin-bottom:1.5rem;">
    <div class="card">
      <div class="card-label">14-Day Avg</div>
      <div class="card-value">{{ "%.1f"|format(trend.avg_kwh) }}<span class="unit">kWh/day</span></div>
      <div class="card-meta">${{ "%.2f"|format(trend.avg_kwh * rate) }}/day</div>
    </div>
    <div class="card">
      <div class="card-label">Trend</div>
      <div class="card-value" style="font-size:1.4rem; color:{{ 'var(--red)' if trend.slope > 0.1 else ('var(--green)' if trend.slope < -0.1 else 'var(--text-light)') }}">
        {{ '%+.2f'|format(trend.slope) }}<span class="unit">kWh/day</span>
      </div>
    </div>
    <div class="card">
      <div class="card-label">Best Day</div>
      <div class="card-value" style="font-size:1.2rem;">{{ trend.best_day.day[-5:] }}</div>
      <div class="card-meta" style="color:var(--green);">{{ "%.1f"|format(trend.best_day.total_kwh) }} kWh</div>
    </div>
    <div class="card">
      <div class="card-label">Peak Day</div>
      <div class="card-value" style="font-size:1.2rem;">{{ trend.worst_day.day[-5:] }}</div>
      <div class="card-meta" style="color:var(--red);">{{ "%.1f"|format(trend.worst_day.total_kwh) }} kWh</div>
    </div>
  </div>
  {% endif %}

  <div class="grid-2">
    <div class="chart-box">
      <h3>Daily Usage — 14 Days</h3>
      <canvas id="trendChart" height="200"></canvas>
    </div>
    <div class="chart-box">
      <h3>Hourly Pattern — 7 Days</h3>
      <canvas id="hourlyChart" height="200"></canvas>
    </div>
  </div>

  <!-- Month comparison -->
  <div class="section-head" style="margin-top:2rem;">
    <h2>Month Comparison</h2>
  </div>
  <div class="grid-3">
    <div class="card">
      <div class="card-label">This Month</div>
      <div class="card-value">{{ "%.1f"|format(mc.this_month.total_kwh or 0) }}<span class="unit">kWh</span></div>
      <div class="card-meta">${{ "%.2f"|format((mc.this_month.total_cents or 0)/100) }}</div>
    </div>
    <div class="card">
      <div class="card-label">Last Month</div>
      <div class="card-value">{{ "%.1f"|format(mc.last_month.total_kwh or 0) }}<span class="unit">kWh</span></div>
      <div class="card-meta">${{ "%.2f"|format((mc.last_month.total_cents or 0)/100) }}</div>
    </div>
    {% if mc.this_month and mc.last_month %}
    <div class="card">
      <div class="card-label">Change</div>
      {% set ch = ((mc.this_month.total_kwh or 0)-(mc.last_month.total_kwh or 0))/(mc.last_month.total_kwh or 1)*100 %}
      <div class="card-value" style="font-size:1.8rem; color:{{ 'var(--green)' if ch<0 else 'var(--red)' }}">{{ '%+.1f'|format(ch) }}%</div>
      <div class="card-meta">vs last month</div>
    </div>
    {% endif %}
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const trendData  = {{ trend_json|tojson }};
const hourlyData = {{ hourly_json|tojson }};
const s = getComputedStyle(document.documentElement);
const textLight = s.getPropertyValue('--olive-600').trim();
const gridCol   = s.getPropertyValue('--olive-200').trim();

function mkChart(id, labels, data, color) {
  new Chart(document.getElementById(id), {
    type:'bar',
    data:{ labels, datasets:[{ data, backgroundColor:color, borderRadius:3 }] },
    options:{
      responsive:true,
      plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>` ${c.raw.toFixed(2)} kWh`}}},
      scales:{
        x:{ticks:{color:textLight, font:{size:10}, maxRotation:45}, grid:{color:gridCol}},
        y:{beginAtZero:true, ticks:{color:textLight, font:{size:10}}, grid:{color:gridCol}}
      }
    }
  });
}

mkChart('trendChart',
  trendData.map(d=>d.day.slice(5)),
  trendData.map(d=>d.total_kwh),
  'oklch(35% 0.045 110)');

mkChart('hourlyChart',
  hourlyData.map(d=>d.hour.slice(11,16)),
  hourlyData.map(d=>d.total_kwh),
  'oklch(42% 0.055 110)');
</script>
"""


# ── Log page template ─────────────────────────────────────────────────────────

LOG_HTML = """
<div class="page">
  <div class="section-head" style="margin-top:1.5rem;">
    <h2>Poller Log</h2>
    <span class="section-sub">Last {{ entries|length }} poll cycles &bull; auto-refreshes every 30s</span>
  </div>
  <div class="card" style="font-family: 'SF Mono', 'Menlo', monospace;">
    <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-light); display:grid; grid-template-columns:1fr auto auto auto; gap:8px; padding:0 0 8px; border-bottom:1px solid var(--border); margin-bottom:4px;">
      <span>Timestamp</span><span>Total kWh</span><span>Cost</span><span>Channels</span>
    </div>
    {% for e in entries %}
    <div class="log-entry">
      <span class="log-ts">{{ e.timestamp[:19] }}</span>
      <span class="log-kwh">{{ "%.4f"|format(e.total_kwh or 0) }} kWh</span>
      <span class="log-cost">${{ "%.4f"|format((e.total_cents or 0)/100) }}</span>
      <span class="log-ch">{{ e.channel_count }} ch</span>
    </div>
    {% endfor %}
    {% if not entries %}
    <div style="padding:1rem; color:var(--text-light); text-align:center; font-style:italic;">
      No poll data yet. Is the poller running?
    </div>
    {% endif %}
  </div>
  <div style="margin-top:1rem; font-size:0.78rem; color:var(--text-light);">
    Log file: <code>/tmp/energymonitor-poller.log</code>
    &bull; Start poller: <code>PYTHONUNBUFFERED=1 python3 -u energy.py</code>
  </div>
</div>
<script>setTimeout(() => location.reload(), 30000);</script>
"""


IMPORT_HTML = """
<div class="page">
  <div class="section-head" style="margin-top:1.5rem;">
    <h2>Import CSV</h2>
    <span class="section-sub">Load historical data from an Emporia energy export</span>
  </div>

  <div class="card" style="max-width:600px;">
    <p style="margin-bottom:1rem; color:var(--text-light); font-size:0.9rem;">
      Select one or more <code>.csv</code> files exported from the Emporia app
      (any resolution: 1MIN, 15MIN, 1H, 1DAY). Duplicate rows are skipped automatically.
    </p>
    <form id="import-form" enctype="multipart/form-data">
      <label style="display:block; margin-bottom:0.5rem; font-weight:600;">CSV file(s)</label>
      <input type="file" id="csv-files" name="files" multiple accept=".csv"
             style="display:block; margin-bottom:1rem; font-size:0.9rem;">
      <button type="submit" class="btn-primary" id="import-btn">Import</button>
    </form>
    <div id="import-results" style="margin-top:1.25rem;"></div>
  </div>

  <div class="card" style="max-width:600px; margin-top:1.25rem;">
    <h3 style="margin-bottom:0.75rem; font-size:1rem;">How to export from Emporia</h3>
    <ol style="padding-left:1.2rem; line-height:1.8; font-size:0.88rem; color:var(--text-light);">
      <li>Open the Emporia app on iOS or Android.</li>
      <li>Tap <strong>Usage</strong>, pick a time range, then tap <strong>Export</strong>.</li>
      <li>Choose <em>CSV</em> format and save or share the file here.</li>
    </ol>
  </div>
</div>

<style>
.btn-primary {
  background: var(--accent);
  color: var(--accent-fg);
  border: none;
  border-radius: 6px;
  padding: 0.5rem 1.25rem;
  font-size: 0.9rem;
  cursor: pointer;
  font-family: inherit;
}
.btn-primary:disabled { opacity: 0.5; cursor: default; }
.import-result { padding: 0.75rem; border-radius: 6px; margin-bottom: 0.5rem; font-size: 0.88rem; }
.import-result.ok  { background: oklch(94% 0.06 145); color: oklch(30% 0.12 145); }
.import-result.err { background: oklch(94% 0.06 25);  color: oklch(30% 0.14 25);  }
</style>

<script>
document.getElementById('import-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const files = document.getElementById('csv-files').files;
  if (!files.length) return;
  const btn = document.getElementById('import-btn');
  const results = document.getElementById('import-results');
  btn.disabled = true;
  btn.textContent = 'Importing…';
  results.innerHTML = '';

  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await fetch('/api/import-csv', { method: 'POST', body: fd });
      const d = await r.json();
      const cls = d.errors ? 'err' : 'ok';
      results.innerHTML +=
        `<div class="import-result ${cls}">
          <strong>${file.name}</strong>: imported ${d.imported}, skipped ${d.skipped}, errors ${d.errors}
          ${d.message ? ' — ' + d.message : ''}
        </div>`;
    } catch (ex) {
      results.innerHTML +=
        `<div class="import-result err"><strong>${file.name}</strong>: network error — ${ex}</div>`;
    }
  }
  btn.disabled = false;
  btn.textContent = 'Import';
});
</script>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

def _common():
    """Values needed by every page (status indicator + device labels)."""
    latest = energy.get_latest()
    last   = latest[0]["timestamp"] if latest else "N/A"
    cls, label, _ = _status(last)
    device_labels = energy.get_device_labels()
    named = [v for v in device_labels.values() if v.strip()]
    panel_label = " · ".join(named) if named else "Service Panel"
    return {
        "last_updated": last, "status_cls": cls, "status_label": label,
        "version": VERSION, "device_labels": device_labels, "panel_label": panel_label,
    }


_MAINS_NAMES = {"Mains_A", "Mains_B", "Mains_C", "Main"}
_SKIP_NAMES  = {"Balance"}


@app.route("/")
def index():
    com = _common()
    ctx = energy.get_now_vs_context(60)
    trend = energy.get_trend(14)

    # Current watts from the most recent poll
    main_now = next((r for r in ctx["latest"] if r["channel_name"] == "Main"), None)
    current_watts = _watts_estimate(main_now["usage_kwh"]) if main_now else 0

    # Circuit bars — exclude Main/Balance, annotate with live watts
    latest_map = {r["channel_name"]: r["usage_kwh"] for r in ctx["latest"]}
    top_circuits = []
    for c in ctx["circuits"]:
        name = c["channel_name"]
        if name in _MAINS_NAMES or name in ("Balance",) or str(name).isdigit():
            continue
        watts_now = _watts_estimate(latest_map.get(name, 0))
        top_circuits.append({**c, "watts": watts_now,
                              "pct": (c["kwh"] / (ctx["current_kwh"] or 1)) * 100})
    top_circuits.sort(key=lambda x: x["watts"], reverse=True)
    top_circuits = top_circuits[:12]

    # 24h summary (Main channel only)
    summary_24 = energy.get_summary(24)
    total_24h  = next((r for r in summary_24 if r["channel_name"] == "Main"),
                      {"total_kwh": sum(r["total_kwh"] for r in summary_24),
                       "total_cents": sum(r["total_cents"] for r in summary_24)})
    circuits_24 = [r for r in summary_24 if r["channel_name"] not in _MAINS_NAMES
                   and r["channel_name"] != "Balance" and not str(r["channel_name"]).isdigit()]
    total_kwh_24 = total_24h["total_kwh"] or 1
    for r in circuits_24:
        r["pct"] = r["total_kwh"] / total_kwh_24 * 100
    biggest_circuit = max(circuits_24, key=lambda r: r["total_kwh"]) if circuits_24 else None
    if biggest_circuit:
        biggest_circuit["pct"] = biggest_circuit["total_kwh"] / total_kwh_24 * 100

    monthly_projected = (total_24h["total_kwh"] or 0) * 30 * RATE
    budget_pct = (monthly_projected / MONTHLY_BUDGET * 100) if MONTHLY_BUDGET else 0

    # Mains cards + breaker panel for dashboard panel view
    sum_24h_map = {r["channel_name"]: r for r in summary_24}
    # Mains: Total first (from live Main channel), then Leg A / Leg B
    leg_labels = {"Mains_A": "Leg A", "Mains_B": "Leg B", "Mains_C": "Leg C"}
    dash_mains = []
    # Total row — prefer live Main channel for watts, sum legs for kWh
    leg_kwh   = sum(sum_24h_map[n]["total_kwh"]   for n in ("Mains_A","Mains_B") if n in sum_24h_map)
    leg_cents = sum(sum_24h_map[n]["total_cents"]  for n in ("Mains_A","Mains_B") if n in sum_24h_map)
    total_watts = _watts_estimate(latest_map.get("Main", 0)) or (
        sum(_watts_estimate(latest_map.get(n, 0)) for n in ("Mains_A","Mains_B")))
    dash_mains.append({
        "label": "Total", "is_total": True,
        "watts": total_watts,
        "kwh_24h": leg_kwh or sum_24h_map.get("Main", {}).get("total_kwh", 0),
        "cost_24h": (leg_cents or sum_24h_map.get("Main", {}).get("total_cents", 0)) / 100,
    })
    for name in ("Mains_A", "Mains_B", "Mains_C"):
        if name not in sum_24h_map:
            continue
        r = sum_24h_map[name]
        dash_mains.append({
            "label":    leg_labels[name],
            "is_total": False,
            "watts":    _watts_estimate(latest_map.get(name, 0)),
            "kwh_24h":  r["total_kwh"],
            "cost_24h": r["total_cents"] / 100,
        })

    layout   = {row["slot"]: row for row in energy.get_panel_layout()}
    ordered  = sorted(
        [n for n in sum_24h_map if n not in _MAINS_NAMES and n not in _SKIP_NAMES
         and not str(n).isdigit()],
        key=lambda n: (n.startswith("Circuit_"), n)
    )
    if not layout:
        for i, name in enumerate(ordered):
            layout[i + 1] = {"slot": i+1, "channel_name": name,
                             "label": None, "note": None, "amps": None}
    max_w = max((_watts_estimate(latest_map.get(n, 0)) for n in ordered), default=1) or 1
    dash_breakers = []
    for slot in range(1, max(len(ordered) + 1, max(layout.keys(), default=0) + 1)):
        row   = layout.get(slot, {})
        name  = row.get("channel_name")
        watts = _watts_estimate(latest_map.get(name, 0)) if name else 0
        bar   = min(100, watts / max_w * 100)
        cls   = "active-heat" if bar > 75 else "active-high" if bar > 40 else ""
        dash_breakers.append({
            "slot": slot, "channel_name": name,
            "label": row.get("label") or name or "—",
            "note": row.get("note"), "amps": row.get("amps"),
            "watts": watts, "bar_pct": bar, "cls": cls,
        })

    peak_usage = energy.get_peak_usage()
    for h in peak_usage["peak_hours"]:
        h["hour_label"] = _format_hour(h["hour"])

    mc = energy.get_month_comparison()

    # Month delta badge
    if mc["this_month"] and mc["last_month"]:
        ch = ((mc["this_month"]["total_kwh"] or 0) -
              (mc["last_month"]["total_kwh"] or 0)) / (mc["last_month"]["total_kwh"] or 1) * 100
        delta_month = _delta_badge(ch, "last month", invert=True)
    else:
        delta_month = _delta_badge(None, "last month")

    return _render(
        DASH_HTML,
        active_page="dashboard",
        ctx=ctx,
        current_watts=current_watts,
        top_circuits=top_circuits,
        total_24h=total_24h,
        biggest_circuit=biggest_circuit,
        monthly_projected=monthly_projected,
        budget=int(MONTHLY_BUDGET),
        budget_pct=min(budget_pct, 100),
        rate=RATE,
        daily_json=energy.get_daily_data(30),
        hourly_json=energy.get_hourly_data(7),
        month_comparison={"this_month": mc["this_month"], "last_month": mc["last_month"]},
        peak_usage=peak_usage,
        dash_mains=dash_mains,
        dash_breakers=dash_breakers,
        trend=trend,
        delta_yd=_delta_badge(ctx["vs_yesterday_pct"],  "yesterday", invert=True),
        delta_wk=_delta_badge(ctx["vs_last_week_pct"],  "last week",  invert=True),
        delta_mo=_delta_badge(ctx["vs_last_month_pct"], "last month", invert=True),
        delta_month=delta_month,
        **com,
    )


@app.route("/circuits")
def circuits_page():
    com = _common()
    latest_map = {r["channel_name"]: r["usage_kwh"] for r in energy.get_latest()}

    # Per-period summaries
    sum_24h  = {r["channel_name"]: r for r in energy.get_summary(24)}
    sum_week = {r["channel_name"]: r for r in energy.get_summary(24 * 7)}
    sum_mon  = {r["channel_name"]: r for r in energy.get_summary(24 * 30)}

    # ── Mains cards — Total first, then Leg A / Leg B ─────────────────────
    leg_labels  = {"Mains_A": "Leg A", "Mains_B": "Leg B", "Mains_C": "Leg C"}
    leg_kwh     = sum(sum_24h[n]["total_kwh"]   for n in ("Mains_A","Mains_B") if n in sum_24h)
    leg_cents   = sum(sum_24h[n]["total_cents"]  for n in ("Mains_A","Mains_B") if n in sum_24h)
    total_watts = _watts_estimate(latest_map.get("Main", 0)) or \
                  sum(_watts_estimate(latest_map.get(n, 0)) for n in ("Mains_A","Mains_B"))
    mains = [{
        "label": "Total", "is_total": True,
        "watts": total_watts,
        "kwh_24h":  leg_kwh  or sum_24h.get("Main", {}).get("total_kwh", 0),
        "cost_24h": (leg_cents or sum_24h.get("Main", {}).get("total_cents", 0)) / 100,
    }]
    for name in ("Mains_A", "Mains_B", "Mains_C"):
        if name not in sum_24h:
            continue
        r = sum_24h[name]
        mains.append({
            "label": leg_labels[name], "is_total": False,
            "watts":    _watts_estimate(latest_map.get(name, 0)),
            "kwh_24h":  r["total_kwh"],
            "cost_24h": r["total_cents"] / 100,
        })

    # ── Circuit slots — use saved panel layout if present ─────────────────
    layout = {row["slot"]: row for row in energy.get_panel_layout()}

    # Determine panel size: largest saved slot rounded up to next 20, min 20
    max_saved = max(layout.keys(), default=0)
    panel_slots = max(20, ((max_saved + 19) // 20) * 20)

    # All known circuit names for fallback auto-population
    all_circuits = sorted([
        n for n in sum_24h
        if n not in _MAINS_NAMES and n not in _SKIP_NAMES
           and not str(n).isdigit()
    ], key=lambda n: (n.startswith("Circuit_"), n))

    # If no layout saved yet, auto-assign circuits to slots in order
    if not layout:
        for i, name in enumerate(all_circuits):
            layout[i + 1] = {"slot": i + 1, "channel_name": name,
                              "label": None, "note": None, "amps": None}

    max_w = max((_watts_estimate(latest_map.get(n, 0)) for n in all_circuits), default=1) or 1

    breakers = []
    for slot in range(1, panel_slots + 1):
        row = layout.get(slot, {})
        name  = row.get("channel_name")
        watts = _watts_estimate(latest_map.get(name, 0)) if name else 0
        bar   = min(100, watts / max_w * 100)
        cls   = "active-heat" if bar > 75 else "active-high" if bar > 40 else ""
        breakers.append({
            "slot":         slot,
            "channel_name": name,
            "label":        row.get("label") or name or "—",
            "note":         row.get("note"),
            "amps":         row.get("amps"),
            "watts":        watts,
            "bar_pct":      bar,
            "cls":          cls,
        })

    # ── Usage table rows ──────────────────────────────────────────────────
    max_day   = max((sum_24h.get(n,  {}).get("total_kwh", 0) for n in all_circuits), default=1) or 1
    max_week  = max((sum_week.get(n, {}).get("total_kwh", 0) for n in all_circuits), default=1) or 1
    max_month = max((sum_mon.get(n,  {}).get("total_kwh", 0) for n in all_circuits), default=1) or 1

    usage_rows = []
    for name in all_circuits:
        d  = sum_24h.get(name,  {})
        w  = sum_week.get(name, {})
        m  = sum_mon.get(name,  {})
        dk  = d.get("total_kwh", 0);  dc = d.get("total_cents", 0)
        wk  = w.get("total_kwh", 0);  wc = w.get("total_cents", 0)
        mk  = m.get("total_kwh", 0);  mc = m.get("total_cents", 0)
        # Find saved label for this circuit
        saved_label = next((r.get("label") for r in layout.values()
                            if r.get("channel_name") == name and r.get("label")), None)
        usage_rows.append({
            "channel_name": name,
            "display_name": saved_label or name,
            "day_kwh":   dk, "day_cost":   dc / 100,
            "week_kwh":  wk, "week_cost":  wc / 100,
            "month_kwh": mk, "month_cost": mc / 100,
            "day_bar":   int(dk / max_day   * 48),
            "week_bar":  int(wk / max_week  * 48),
            "month_bar": int(mk / max_month * 48),
        })
    usage_rows.sort(key=lambda r: r["day_kwh"], reverse=True)

    return _render(CIRCUITS_HTML, active_page="circuits",
                   mains=mains, breakers=breakers, usage_rows=usage_rows,
                   panel_slots=panel_slots, **com)


@app.route("/circuit/<path:circuit_name>")
@app.route("/circuit/<path:circuit_name>/<period>")
def circuit_detail(circuit_name, period="day"):
    from urllib.parse import unquote, quote
    circuit_name = unquote(circuit_name)
    circuit_url  = quote(circuit_name, safe="")
    com = _common()

    data = energy.get_circuit_data(circuit_name, period)

    # Per-circuit context comparison using same window as the period
    window_map = {"hour": 60, "day": 60*24, "week": 60*24*7, "month": 60*24*30}
    wmin = window_map.get(period, 60*24)
    ctx = energy.get_now_vs_context(wmin)
    # Override circuit-level context (main get_now_vs_context queries Main channel)
    # Build a simple circuit-specific context
    from datetime import datetime, timedelta
    import sqlite3 as _sq
    _conn = energy._connect()
    _c    = _conn.cursor()
    _now  = datetime.now()
    def _ckt_kwh(offset_days):
        start = (_now - timedelta(days=offset_days, minutes=wmin)).isoformat()
        end   = (_now - timedelta(days=offset_days)).isoformat()
        _c.execute("SELECT SUM(usage_kwh) FROM readings WHERE channel_name=? AND timestamp BETWEEN ? AND ?",
                   (circuit_name, start, end))
        row = _c.fetchone()
        return row[0] if row and row[0] is not None else None
    ckt_ctx = {
        "current_kwh":    _ckt_kwh(0),
        "yesterday_kwh":  _ckt_kwh(1),
        "last_week_kwh":  _ckt_kwh(7),
        "last_month_kwh": _ckt_kwh(30),
        "vs_yesterday_pct":  energy._delta_pct(_ckt_kwh(0), _ckt_kwh(1)),
        "vs_last_week_pct":  energy._delta_pct(_ckt_kwh(0), _ckt_kwh(7)),
        "vs_last_month_pct": energy._delta_pct(_ckt_kwh(0), _ckt_kwh(30)),
    }
    _conn.close()

    return _render(
        CIRCUIT_HTML,
        active_page="circuits",
        circuit_name=circuit_name,
        circuit_url=circuit_url,
        period=period,
        total=data["total"],
        chart_json=data["data"],
        ctx=ckt_ctx,
        poll_interval=energy.POLL_INTERVAL,
        delta_yd=_delta_badge(ckt_ctx["vs_yesterday_pct"],  "yesterday", invert=True),
        delta_wk=_delta_badge(ckt_ctx["vs_last_week_pct"],  "last week",  invert=True),
        delta_mo=_delta_badge(ckt_ctx["vs_last_month_pct"], "last month", invert=True),
        **com,
    )


@app.route("/trends")
def trends_page():
    com   = _common()
    trend = energy.get_trend(14)
    mc    = energy.get_month_comparison()
    return _render(
        TRENDS_HTML,
        active_page="trends",
        trend=trend,
        trend_json=trend["daily"],
        hourly_json=energy.get_hourly_data(7),
        mc={"this_month": mc["this_month"], "last_month": mc["last_month"]},
        rate=RATE,
        **com,
    )


@app.route("/log")
def log_page():
    com     = _common()
    entries = energy.get_log_entries(200)
    return _render(LOG_HTML, active_page="log", entries=entries, **com)


# ── REST API ──────────────────────────────────────────────────────────────────

@app.route("/api/weather")
def api_weather():
    """Fetch 10-day forecast from Open-Meteo for zip 18947 (Pipersville, PA).
    Cached for 30 min to avoid hammering the free API."""
    import urllib.request, json as _json, time as _time
    cache = getattr(api_weather, "_cache", None)
    if cache and _time.time() - cache["ts"] < 1800:
        return jsonify(cache["data"])
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=40.42538&longitude=-75.13934"
            "&daily=weathercode,temperature_2m_max,temperature_2m_min"
            "&current_weather=true"
            "&temperature_unit=fahrenheit"
            "&forecast_days=10"
            "&timezone=America%2FNew_York"
        )
        with urllib.request.urlopen(url, timeout=8) as resp:
            raw = _json.loads(resp.read())
        daily = raw.get("daily", {})
        current = raw.get("current_weather", {})
        days = [
            {
                "date":        daily["time"][i],
                "weathercode": daily["weathercode"][i],
                "temp_max":    daily["temperature_2m_max"][i],
                "temp_min":    daily["temperature_2m_min"][i],
            }
            for i in range(len(daily.get("time", [])))
        ]
        result = {"days": days, "current_temp": current.get("temperature")}
        api_weather._cache = {"ts": _time.time(), "data": result}
        return jsonify(result)
    except Exception as e:
        return jsonify({"days": [], "current_temp": None, "error": str(e)})


@app.route("/api/version")
def api_version():
    return jsonify({"version": VERSION})

@app.route("/api/summary")
def api_summary():
    return jsonify(energy.get_summary(24))

@app.route("/api/daily")
def api_daily():
    return jsonify(energy.get_daily_data(30))

@app.route("/api/hourly")
def api_hourly():
    return jsonify(energy.get_hourly_data(7))

@app.route("/api/latest")
def api_latest():
    return jsonify(energy.get_latest())

@app.route("/api/context")
def api_context():
    return jsonify(energy.get_now_vs_context(60))

@app.route("/api/trend")
def api_trend():
    return jsonify(energy.get_trend(14))

@app.route("/api/month-comparison")
def api_month_comparison():
    return jsonify(energy.get_month_comparison())

@app.route("/api/peak-usage")
def api_peak_usage():
    return jsonify(energy.get_peak_usage())

@app.route("/api/circuit/<path:circuit_name>")
@app.route("/api/circuit/<path:circuit_name>/<period>")
def api_circuit(circuit_name, period="day"):
    from urllib.parse import unquote
    return jsonify(energy.get_circuit_data(unquote(circuit_name), period))


PANEL_EDIT_HTML = """
<div class="page">
  <div class="section-head" style="margin-top:1.5rem;">
    <h2>Panel Layout Editor</h2>
    <span class="section-sub">Assign circuits to physical breaker slots, add labels &amp; notes</span>
  </div>
  <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:1rem;">
    Slot numbers mirror the physical breaker positions (1 = top-left, 2 = top-right, alternating down).
    Leave <em>Circuit</em> blank for a spare/empty slot.
  </p>

  <div style="display:flex; gap:12px; margin-bottom:1rem; flex-wrap:wrap; align-items:center;">
    <label style="font-size:0.82rem; color:var(--text-light);">
      Panel size:
      <select id="panelSize" style="margin-left:6px; font-size:0.82rem; padding:4px 8px; border-radius:5px; border:1px solid var(--border); background:var(--bg); color:var(--text);">
        <option value="20" {{ 'selected' if panel_slots==20 else '' }}>20 slots</option>
        <option value="40" {{ 'selected' if panel_slots==40 else '' }}>40 slots</option>
      </select>
    </label>
    <button onclick="saveLayout()" style="padding:8px 20px; background:var(--olive-800); color:var(--olive-50); border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
      Save Layout
    </button>
    <span id="saveMsg" style="font-size:0.82rem; color:var(--green); display:none;">Saved ✓</span>
  </div>

  <div class="panel-edit-grid" id="editGrid">
    {% for b in breakers %}
    <div class="slot-row" data-slot="{{ b.slot }}">
      <div class="slot-num">{{ b.slot }}</div>
      <select class="sel-circuit" title="Circuit channel">
        <option value="">— empty —</option>
        {% for ch in all_channels %}
        <option value="{{ ch }}" {{ 'selected' if b.channel_name == ch else '' }}>{{ ch }}</option>
        {% endfor %}
      </select>
      <input class="inp-label" type="text" placeholder="Label" value="{{ b.label if b.label and b.label != b.channel_name else '' }}" title="Display label override">
      <input class="inp-note" type="text" placeholder="Note (e.g. Bedroom outlets, 20A)" value="{{ b.note or '' }}" title="Hover note">
      <input class="inp-amps" type="number" placeholder="A" value="{{ b.amps or '' }}" min="1" max="200" title="Breaker amps">
    </div>
    {% endfor %}
  </div>
</div>
<script>
function saveLayout() {
  const rows = document.querySelectorAll('.slot-row');
  const slots = Array.from(rows).map(row => ({
    slot:         parseInt(row.dataset.slot),
    channel_name: row.querySelector('.sel-circuit').value || null,
    label:        row.querySelector('.inp-label').value.trim() || null,
    note:         row.querySelector('.inp-note').value.trim() || null,
    amps:         parseInt(row.querySelector('.inp-amps').value) || null,
  }));
  fetch('/api/panel-layout', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({slots})
  }).then(r => r.json()).then(() => {
    const m = document.getElementById('saveMsg');
    m.style.display = 'inline';
    setTimeout(() => m.style.display = 'none', 2500);
  });
}
// Show/hide slots based on panel size selector
document.getElementById('panelSize').addEventListener('change', function() {
  const n = parseInt(this.value);
  document.querySelectorAll('.slot-row').forEach(r => {
    r.style.display = parseInt(r.dataset.slot) <= n ? '' : 'none';
  });
});
</script>
"""

SETTINGS_HTML = """
<div class="page">
  <div class="section-head" style="margin-top:1.5rem;">
    <h2>Settings</h2>
    <span class="section-sub">Emporia credentials &amp; app configuration</span>
  </div>

  <!-- Credentials -->
  <div class="card" style="margin-bottom:1.5rem;">
    <h3 style="font-size:1.1rem; margin-bottom:0.25rem;">Emporia Account</h3>
    <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:1rem;">
      {% if has_tokens %}
      <span style="color:var(--green);">&#10003; Authenticated</span> — tokens cached in <code>keys.json</code>.
      Re-enter credentials only if you change your password or tokens expire.
      {% else %}
      No tokens found. Enter your Emporia email &amp; password to start live polling.
      {% endif %}
    </p>
    <form id="credForm" style="display:flex; flex-direction:column; gap:10px; max-width:420px;">
      <label style="font-size:0.82rem; font-weight:600;">
        Email
        <input type="email" id="credEmail" value="{{ saved_email }}"
               style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                      border:1px solid var(--border); background:var(--bg); color:var(--text);
                      font-size:0.9rem; font-family:inherit;">
      </label>
      <label style="font-size:0.82rem; font-weight:600;">
        Password
        <input type="password" id="credPwd" placeholder="••••••••"
               style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                      border:1px solid var(--border); background:var(--bg); color:var(--text);
                      font-size:0.9rem; font-family:inherit;">
      </label>
      <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        <button type="button" onclick="saveCreds()"
                style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50);
                       border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
          Save &amp; Authenticate
        </button>
        <span id="credMsg" style="font-size:0.82rem; display:none;"></span>
      </div>
    </form>
    <p style="font-size:0.75rem; color:var(--text-light); margin-top:0.75rem;">
      Credentials are stored locally in <code>settings.json</code>. The live poller must be restarted after changes.
    </p>
  </div>

  <!-- Rate & Budget -->
  <div class="card" style="margin-bottom:1.5rem;">
    <h3 style="font-size:1.1rem; margin-bottom:0.25rem;">Rate &amp; Budget</h3>
    <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:0.75rem;">
      <label style="font-size:0.82rem; font-weight:600;">
        Electricity rate (¢/kWh)
        <input type="number" id="cfgRate" step="0.01" value="{{ rate_cents }}"
               style="display:block; width:120px; margin-top:4px; padding:8px 10px; border-radius:8px;
                      border:1px solid var(--border); background:var(--bg); color:var(--text); font-family:inherit;">
      </label>
      <label style="font-size:0.82rem; font-weight:600;">
        Monthly budget ($)
        <input type="number" id="cfgBudget" step="1" value="{{ monthly_budget }}"
               style="display:block; width:120px; margin-top:4px; padding:8px 10px; border-radius:8px;
                      border:1px solid var(--border); background:var(--bg); color:var(--text); font-family:inherit;">
      </label>
    </div>
    <button type="button" onclick="saveConfig()" style="margin-top:0.85rem; padding:9px 22px;
            background:var(--olive-800); color:var(--olive-50); border:none; border-radius:8px;
            font-size:0.85rem; cursor:pointer; font-family:inherit;">
      Save Rate &amp; Budget
    </button>
    <span id="cfgMsg" style="font-size:0.82rem; color:var(--green); margin-left:10px; display:none;">Saved ✓</span>
    <p style="font-size:0.75rem; color:var(--text-light); margin-top:0.5rem;">Restart Flask for changes to take effect.</p>
  </div>

  <!-- Panel Labels -->
  <div class="card" style="margin-bottom:1.5rem;">
    <h3 style="font-size:1.1rem; margin-bottom:0.25rem;">Panel Labels</h3>
    <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:1rem;">
      Name each Emporia device. Labels appear in the panel header and breaker views.
    </p>
    {% for gid in known_devices %}
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap:wrap;">
      <code style="font-size:0.72rem; background:var(--stone-100); color:var(--stone-600);
                   padding:3px 7px; border-radius:5px; flex-shrink:0;">{{ gid }}</code>
      <input type="text" class="device-label-input" data-gid="{{ gid }}"
             value="{{ device_labels.get(gid, '') }}"
             placeholder="e.g. House, Barn, Garage"
             style="flex:1; min-width:180px; max-width:280px; padding:8px 10px; border-radius:8px;
                    border:1px solid var(--border); background:var(--bg); color:var(--text);
                    font-size:0.9rem; font-family:inherit;">
    </div>
    {% else %}
    <p style="font-size:0.82rem; color:var(--text-light); font-style:italic;">
      No devices found in the database yet.
    </p>
    {% endfor %}
    {% if known_devices %}
    <div style="display:flex; gap:10px; align-items:center; margin-top:0.5rem;">
      <button type="button" onclick="saveDeviceLabels()"
              style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50);
                     border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
        Save Labels
      </button>
      <span id="lblMsg" style="font-size:0.82rem; color:var(--green); display:none;">Saved ✓</span>
    </div>
    {% endif %}
  </div>

  <!-- Aqara Integration -->
  <div class="card" style="margin-bottom:1.5rem;">
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:0.25rem;">
      <h3 style="font-size:1.1rem; margin:0;">Aqara Integration</h3>
      <span style="font-size:0.7rem; padding:2px 8px; background:var(--stone-200); color:var(--stone-600);
                   border-radius:20px; font-weight:600; letter-spacing:0.04em;">COMING SOON</span>
    </div>
    <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:1rem;">
      Connect your <strong>Aqara Hub M3</strong> to display temperature &amp; humidity sensor readings.<br>
      Requires a free Aqara developer account — sign-up is currently unavailable but will open soon.
    </p>

    <div style="background:var(--stone-50); border:1px solid var(--border); border-radius:10px; padding:1rem; margin-bottom:1rem;">
      <p style="font-size:0.78rem; font-weight:600; margin:0 0 0.5rem; color:var(--olive-800);">Setup steps (when available):</p>
      <ol style="font-size:0.78rem; color:var(--text-light); margin:0; padding-left:1.25rem; line-height:1.8;">
        <li>Register at <code>developer.aqara.com</code> → create an app</li>
        <li>Copy your <strong>App ID</strong>, <strong>App Key</strong>, and <strong>Key ID</strong> below</li>
        <li>Click <strong>Authorize</strong> to link your Aqara account via OAuth</li>
        <li>Navigate to the <a href="/aqara" style="color:var(--olive-700);">Aqara tab</a> to see live sensor data</li>
      </ol>
    </div>

    <form id="aqaraCredForm" style="display:flex; flex-direction:column; gap:10px; max-width:420px; opacity:0.5; pointer-events:none;">
      <label style="font-size:0.82rem; font-weight:600;">
        App ID
        <input type="text" id="aqaraAppId" value="{{ aqara_app_id }}" placeholder="e.g. axxx000000000000"
               style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                      border:1px solid var(--border); background:var(--bg); color:var(--text); font-family:inherit; font-size:0.9rem;">
      </label>
      <label style="font-size:0.82rem; font-weight:600;">
        App Key
        <input type="password" id="aqaraAppKey" placeholder="••••••••••••••••"
               style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                      border:1px solid var(--border); background:var(--bg); color:var(--text); font-family:inherit; font-size:0.9rem;">
      </label>
      <label style="font-size:0.82rem; font-weight:600;">
        Key ID
        <input type="text" id="aqaraKeyId" value="{{ aqara_key_id }}" placeholder="e.g. Kxxx000000000000"
               style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                      border:1px solid var(--border); background:var(--bg); color:var(--text); font-family:inherit; font-size:0.9rem;">
      </label>
      <div style="display:flex; gap:10px; flex-wrap:wrap;">
        <button type="button" onclick="saveAqaraCreds()"
                style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50);
                       border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
          Save Credentials
        </button>
        <button type="button" onclick="authorizeAqara()"
                style="padding:9px 22px; background:var(--surface2); color:var(--text);
                       border:1px solid var(--border); border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
          Authorize →
        </button>
        <span id="aqaraMsg" style="font-size:0.82rem; display:none; align-self:center;"></span>
      </div>
    </form>
    <p style="font-size:0.73rem; color:var(--stone-400); margin-top:0.75rem;">
      Credentials are stored locally in <code>settings.json</code>. Your Aqara password is never stored — only the OAuth access token.
    </p>
  </div>

  <!-- Panel layout link -->
  <div class="card">
    <h3 style="font-size:1.1rem; margin-bottom:0.4rem;">Panel Layout</h3>
    <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:0.75rem;">
      Assign circuits to physical breaker slots, add custom labels and hover notes.
    </p>
    <a href="/panel" style="display:inline-block; padding:9px 22px; background:var(--olive-800);
       color:var(--olive-50); border-radius:8px; font-size:0.85rem; text-decoration:none;">
      Edit Panel Layout →
    </a>
  </div>
</div>
<script>
function saveCreds() {
  const email = document.getElementById('credEmail').value.trim();
  const pwd   = document.getElementById('credPwd').value;
  const msg   = document.getElementById('credMsg');
  if (!email) { msg.textContent='Email required'; msg.style.color='var(--red)'; msg.style.display='inline'; return; }
  fetch('/api/settings/credentials', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({email, password: pwd})
  }).then(r=>r.json()).then(d => {
    msg.textContent = d.ok ? 'Saved — restart the poller to apply' : ('Error: ' + d.error);
    msg.style.color = d.ok ? 'var(--green)' : 'var(--red)';
    msg.style.display = 'inline';
  });
}
function saveConfig() {
  const rate   = parseFloat(document.getElementById('cfgRate').value);
  const budget = parseFloat(document.getElementById('cfgBudget').value);
  fetch('/api/settings/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({rate_cents: rate, monthly_budget: budget})
  }).then(r=>r.json()).then(() => {
    const m = document.getElementById('cfgMsg');
    m.style.display='inline'; setTimeout(()=>m.style.display='none', 2500);
  });
}
function saveDeviceLabels() {
  const labels = {};
  document.querySelectorAll('.device-label-input').forEach(inp => {
    labels[inp.dataset.gid] = inp.value.trim();
  });
  fetch('/api/settings/device-labels', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(labels)
  }).then(r=>r.json()).then(d => {
    const m = document.getElementById('lblMsg');
    m.style.display='inline'; setTimeout(()=>m.style.display='none', 2500);
  });
}
function saveAqaraCreds() {
  // Placeholder: will POST to /api/settings/aqara when developer signup is available
  const msg = document.getElementById('aqaraMsg');
  msg.textContent = 'Aqara integration not yet active — developer signup opens soon.';
  msg.style.color = 'var(--stone-500)';
  msg.style.display = 'inline';
}
function authorizeAqara() {
  // Placeholder: will open OAuth flow at developer.aqara.com when available
  const msg = document.getElementById('aqaraMsg');
  msg.textContent = 'OAuth flow coming soon.';
  msg.style.color = 'var(--stone-500)';
  msg.style.display = 'inline';
}
</script>
"""

@app.route("/panel")
def panel_edit_page():
    com = _common()
    layout = {row["slot"]: row for row in energy.get_panel_layout()}
    max_saved = max(layout.keys(), default=0)
    panel_slots = max(20, ((max_saved + 19) // 20) * 20)
    all_channels = sorted([
        r["channel_name"] for r in energy.get_summary(24 * 30)
        if r["channel_name"] not in _MAINS_NAMES and r["channel_name"] not in _SKIP_NAMES
           and not str(r["channel_name"]).isdigit()
    ])
    breakers = []
    for slot in range(1, panel_slots + 1):
        row = layout.get(slot, {})
        name = row.get("channel_name")
        breakers.append({
            "slot":         slot,
            "channel_name": name or "",
            "label":        row.get("label") or "",
            "note":         row.get("note") or "",
            "amps":         row.get("amps") or "",
        })
    return _render(PANEL_EDIT_HTML, active_page="settings",
                   breakers=breakers, all_channels=all_channels,
                   panel_slots=panel_slots, **com)


@app.route("/api/panel-layout", methods=["POST"])
def api_panel_layout():
    data = request.get_json(force=True)
    for s in data.get("slots", []):
        energy.save_panel_slot(
            s["slot"], s.get("channel_name"), s.get("label"),
            s.get("note"), s.get("amps")
        )
    return jsonify({"ok": True})


AQARA_HTML = """
<div class="page">
  <div class="section-head" style="margin-top:1.5rem;">
    <h2>Aqara Sensors</h2>
    <span class="section-sub">Temperature &amp; humidity — Hub M3</span>
  </div>

  {% if not aqara_configured %}
  <!-- Not configured state -->
  <div class="card" style="text-align:center; padding:3rem 2rem;">
    <div style="font-size:3rem; margin-bottom:1rem;">🌡️</div>
    <h3 style="font-size:1.3rem; margin-bottom:0.5rem;">Aqara not yet connected</h3>
    <p style="font-size:0.88rem; color:var(--text-light); max-width:480px; margin:0 auto 1.5rem;">
      Aqara developer sign-up is temporarily unavailable. Once it reopens, you can connect
      your <strong>Hub M3</strong> and your temperature &amp; humidity sensors will appear here automatically.
    </p>
    <div style="background:var(--stone-50); border:1px solid var(--border); border-radius:10px;
                padding:1.25rem; max-width:520px; margin:0 auto 1.5rem; text-align:left;">
      <p style="font-size:0.8rem; font-weight:600; margin:0 0 0.6rem; color:var(--olive-800);">
        When developer.aqara.com sign-up opens:
      </p>
      <ol style="font-size:0.8rem; color:var(--text-light); margin:0; padding-left:1.25rem; line-height:2;">
        <li>Create a developer account &amp; app → copy <strong>App ID</strong>, <strong>App Key</strong>, <strong>Key ID</strong></li>
        <li>Enter credentials in <a href="/settings" style="color:var(--olive-700);">Settings → Aqara Integration</a></li>
        <li>Click <strong>Authorize</strong> to link your account via OAuth</li>
        <li>Return here — sensors will appear automatically</li>
      </ol>
    </div>
    <a href="/settings" style="display:inline-block; padding:9px 24px; background:var(--olive-800);
       color:var(--olive-50); border-radius:8px; font-size:0.85rem; text-decoration:none;">
      Go to Settings →
    </a>
  </div>

  {% else %}
  <!-- Configured: show sensor grid -->
  {% if aqara_error %}
  <div style="padding:0.75rem 1rem; background:#fef2f2; border:1px solid #fca5a5;
              border-radius:8px; font-size:0.82rem; color:#b91c1c; margin-bottom:1rem;">
    API error: {{ aqara_error }}
  </div>
  {% endif %}

  {% if sensors %}
  <div class="grid-4" style="margin-bottom:1.5rem;">
    {% for s in sensors %}
    <div class="card" style="position:relative;">
      {% if not s.online %}
      <span style="position:absolute; top:10px; right:10px; font-size:0.65rem; padding:2px 7px;
                   background:var(--stone-200); color:var(--stone-500); border-radius:20px;">offline</span>
      {% endif %}
      <div class="card-label">{{ s.name }}</div>
      {% if s.temperature is not none %}
      <div class="card-value" style="font-size:2rem;">
        {{ "%.1f"|format(s.temperature) }}<span class="unit">°C</span>
        <span style="font-size:1rem; color:var(--text-light); margin-left:4px;">
          / {{ "%.1f"|format(s.temperature * 9/5 + 32) }}°F
        </span>
      </div>
      {% else %}
      <div class="card-value" style="color:var(--stone-400);">—</div>
      {% endif %}
      {% if s.humidity is not none %}
      <div class="card-meta">💧 {{ "%.1f"|format(s.humidity) }}% RH</div>
      {% endif %}
      {% if s.battery is not none %}
      <div style="margin-top:0.5rem; font-size:0.72rem; color:var(--stone-400);">
        🔋 {{ s.battery }}%
        <div style="height:3px; background:var(--border); border-radius:2px; margin-top:3px;">
          <div style="height:3px; border-radius:2px; width:{{ s.battery }}%;
               background:{{ 'var(--green)' if s.battery > 30 else '#f59e0b' if s.battery > 15 else '#ef4444' }};"></div>
        </div>
      </div>
      {% endif %}
      <div style="margin-top:0.5rem; font-size:0.68rem; color:var(--stone-400);">{{ s.model }}</div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="card" style="text-align:center; padding:2rem; color:var(--text-light); font-style:italic;">
    No TH sensors found. Make sure your sensors are paired to the Hub M3.
  </div>
  {% endif %}
  {% endif %}

  <p style="font-size:0.72rem; color:var(--stone-400); margin-top:1rem;">
    Data via <strong>Aqara Cloud OpenAPI v3</strong> &bull; Hub: M3 &bull; Region: USA &bull;
    Refreshes on page load
  </p>
</div>
"""


@app.route("/aqara")
def aqara_page():
    import aqara as _aqara
    com = _common()
    configured = _aqara.is_configured()
    sensors, error = [], None
    if configured:
        try:
            sensors = _aqara.get_sensors()
        except Exception as e:
            error = str(e)
    cfg = _aqara._load_aqara_config()
    return _render(AQARA_HTML, active_page="aqara",
                   aqara_configured=configured,
                   sensors=sensors,
                   aqara_error=error,
                   aqara_app_id=cfg.get("app_id", ""),
                   aqara_key_id=cfg.get("key_id", ""),
                   **com)


@app.route("/settings")
def settings_page():
    import json as _json, os as _os
    com = _common()
    cfg = {}
    try:
        with open("settings.json") as f:
            cfg = _json.load(f)
    except Exception:
        pass
    has_tokens = _os.path.exists("keys.json")
    aq = cfg.get("aqara", {})
    return _render(SETTINGS_HTML, active_page="settings",
                   saved_email=cfg.get("emporia_email", ""),
                   has_tokens=has_tokens,
                   rate_cents=energy.RATE_CENTS,
                   monthly_budget=MONTHLY_BUDGET,
                   known_devices=energy.get_known_devices(),
                   aqara_app_id=aq.get("app_id", ""),
                   aqara_key_id=aq.get("key_id", ""),
                   **com)


@app.route("/api/settings/credentials", methods=["POST"])
def api_save_credentials():
    import json as _json, os as _os
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip()
    pwd   = (data.get("password") or "").strip()
    if not email:
        return jsonify({"ok": False, "error": "Email required"}), 400
    cfg = {}
    try:
        with open("settings.json") as f:
            cfg = _json.load(f)
    except Exception:
        pass
    cfg["emporia_email"] = email
    if pwd:
        cfg["emporia_password"] = pwd
        # Delete cached tokens so poller re-authenticates on restart
        if _os.path.exists("keys.json"):
            _os.remove("keys.json")
    with open("settings.json", "w") as f:
        _json.dump(cfg, f, indent=2)
    return jsonify({"ok": True})


@app.route("/api/settings/config", methods=["POST"])
def api_save_config():
    import json as _json
    data = request.get_json(force=True)
    cfg = {}
    try:
        with open("settings.json") as f:
            cfg = _json.load(f)
    except Exception:
        pass
    if data.get("rate_cents") is not None:
        cfg["rate_cents"] = float(data["rate_cents"])
    if data.get("monthly_budget") is not None:
        cfg["monthly_budget"] = float(data["monthly_budget"])
    with open("settings.json", "w") as f:
        _json.dump(cfg, f, indent=2)
    return jsonify({"ok": True})


@app.route("/api/settings/device-labels", methods=["POST"])
def api_save_device_labels():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Expected object"}), 400
    energy.save_device_labels(data)
    return jsonify({"ok": True})


@app.route("/import")
def import_page():
    com = _common()
    return _render(IMPORT_HTML, active_page="import", **com)


@app.route("/api/import-csv", methods=["POST"])
def api_import_csv():
    import tempfile, os
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded"}), 400
    suffix = os.path.splitext(f.filename or "")[1] or ".csv"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name
    try:
        result = energy.import_emporia_csv(tmp_path)
    except Exception as exc:
        result = {"imported": 0, "skipped": 0, "errors": 1, "message": str(exc)}
    finally:
        os.unlink(tmp_path)
    return jsonify(result)


if __name__ == "__main__":
    print(f"Energy Monitor — http://localhost:5001")
    print(f"Rate: ${RATE:.4f}/kWh  Budget: ${MONTHLY_BUDGET:.0f}/mo")
    n = energy.migrate_channel_names()
    if n:
        print(f"[startup] migrated {n} channel name(s)")
    app.run(debug=False, host="0.0.0.0", port=5001)
