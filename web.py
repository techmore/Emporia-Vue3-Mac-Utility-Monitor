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

VERSION = "1.7.2"

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

/* ── Breaker safety zones ── */
.breaker-load-row {
  display: flex; align-items: center; gap: 4px;
  font-size: 0.6rem; margin-top: 3px; line-height: 1;
}
.breaker-load-bar-wrap {
  flex: 1; height: 3px; background: var(--olive-900); border-radius: 2px; overflow: hidden; position: relative;
}
.breaker-load-bar-fill {
  height: 100%; border-radius: 2px; transition: width 0.4s ease;
}
/* 80% NEC tick mark */
.breaker-load-bar-wrap::after {
  content: ''; position: absolute; top: 0; left: 80%; width: 1px; height: 100%;
  background: rgba(255,255,255,0.35);
}
.breaker-load-text { font-size: 0.58rem; white-space: nowrap; min-width: 28px; text-align: right; }

/* safety zone colours */
.load-normal   { color: #4ade80; }
.load-moderate { color: #facc15; }
.load-caution  { color: #f97316; }
.load-danger   { color: #ef4444; }
.fill-normal   { background: #4ade80; }
.fill-moderate { background: #facc15; }
.fill-caution  { background: #f97316; }
.fill-danger   { background: #ef4444; }

/* breaker border glow by safety */
.breaker.sz-moderate { border-color: rgba(250,204,21,0.45); }
.breaker.sz-caution  { border-color: rgba(249,115,22,0.6); box-shadow: 0 0 0 1px rgba(249,115,22,0.3); }
.breaker.sz-danger   { border-color: rgba(239,68,68,0.7);  box-shadow: 0 0 0 1px rgba(239,68,68,0.4); }

/* peak-user badge */
.breaker-peak-badge {
  position: absolute; top: 3px; right: 3px;
  font-size: 0.55rem; background: rgba(250,204,21,0.18);
  border: 1px solid rgba(250,204,21,0.45); border-radius: 3px;
  padding: 1px 3px; color: #facc15; line-height: 1.2;
}

/* ── Panel edit page ── */
.panel-edit-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  margin-top: 0.5rem;
}
.slot-row {
  display: grid; grid-template-columns: 28px 1fr 110px 1fr 52px 56px; gap: 5px; align-items: center;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 10px;
}
.slot-num { font-size: 0.75rem; color: var(--text-light); flex-shrink: 0; font-weight: 600; }
.slot-row input, .slot-row select {
  font-size: 0.8rem; padding: 4px 6px; border-radius: 5px;
  border: 1px solid var(--border); background: var(--bg);
  color: var(--text); font-family: inherit; width: 100%;
}
.slot-row input.inp-amps { text-align: center; }
.slot-row select.sel-poles { text-align: center; }

/* ── Settings sidebar ── */
.settings-wrap {
  display: grid;
  grid-template-columns: 196px 1fr;
  gap: 1.5rem;
  align-items: start;
}
.sys-nav {
  position: sticky; top: 72px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 0.4rem;
  display: flex; flex-direction: column; gap: 2px;
}
.sys-nav-group {
  font-size: 0.62rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.09em; color: var(--text-light);
  padding: 0.65rem 0.75rem 0.3rem;
}
.sys-link {
  display: flex; align-items: center; gap: 9px;
  padding: 0.55rem 0.75rem;
  border-radius: 7px; cursor: pointer;
  text-decoration: none; color: var(--text);
  transition: background 0.13s;
  border: none; background: none;
  width: 100%; text-align: left; font-family: inherit;
}
.sys-link:hover  { background: var(--olive-50); }
.sys-link.active { background: var(--olive-100); }
.sys-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.sys-dot.ok   { background: var(--green); box-shadow: 0 0 0 2px rgba(90,138,94,.2); }
.sys-dot.warn { background: var(--amber); }
.sys-dot.soon { background: var(--stone-300); }
.sys-name { font-size: 0.82rem; font-weight: 600; line-height: 1.2; white-space: nowrap; }
.sys-sub  { font-size: 0.68rem; color: var(--text-light); margin-top: 1px; white-space: nowrap; }
.sys-panel { display: none; }
.sys-panel.active { display: block; }
.sys-panel-head {
  display: flex; align-items: center; gap: 10px; margin-bottom: 1.25rem; flex-wrap: wrap;
}
.sys-panel-head h3 { font-size: 1.2rem; margin: 0; }
.int-badge {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.05em; padding: 3px 9px; border-radius: 20px;
}
.int-badge.ok     { background: rgba(90,138,94,.12); color: var(--green); }
.int-badge.warn   { background: rgba(176,125,42,.12); color: var(--amber); }
.int-badge.coming { background: var(--stone-100); color: var(--stone-500); }

/* Coming-soon instruction card */
.setup-steps {
  background: var(--stone-50); border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem 1.1rem; margin-bottom: 1rem;
}
.setup-steps p { font-size: 0.78rem; font-weight: 600; margin: 0 0 0.4rem; color: var(--olive-800); }
.setup-steps ol, .setup-steps ul {
  font-size: 0.78rem; color: var(--text-light);
  margin: 0; padding-left: 1.25rem; line-height: 1.85;
}
.setup-steps li code {
  background: var(--stone-100); padding: 1px 5px; border-radius: 4px; font-size: 0.75rem;
}

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
      <a href="/log" id="nav-status-link" style="display:flex; align-items:center; gap:6px; text-decoration:none; color:inherit;">
        <span class="status-dot {{ status_cls }}" id="nav-status-dot"></span>
        <span id="nav-status-label">{{ status_label }}</span>
      </a>
      <a href="https://github.com/techmore/Emporia-Vue3-Mac-Utility-Monitor" target="_blank" rel="noopener"
         title="View on GitHub"
         style="display:flex; align-items:center; color:var(--stone-400); opacity:0.7; transition:opacity 0.15s;"
         onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M12 2C6.477 2 2 6.477 2 12c0 4.418 2.865 8.166 6.839 9.489.5.092.682-.217.682-.482
                   0-.237-.009-.868-.013-1.703-2.782.604-3.369-1.342-3.369-1.342-.454-1.155-1.11-1.463-1.11-1.463
                   -.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.088 2.91.832
                   .092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683
                   -.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.578 9.578 0 0 1 12 6.836
                   a9.576 9.576 0 0 1 2.504.337c1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.202 2.394.1 2.647
                   .64.699 1.028 1.592 1.028 2.683 0 3.842-2.337 4.687-4.565 4.935.359.309.678.919.678 1.852
                   0 1.336-.012 2.415-.012 2.743 0 .267.18.579.688.481C19.138 20.163 22 16.418 22 12
                   c0-5.523-4.477-10-10-10z"/>
        </svg>
      </a>
    </div>
  </div>
</nav>
<script>
(function() {
  function fmtNavStatus(s) {
    const age = s.age_secs;
    if (s.ok && s.poller_running) {
      if (age < 90)  return { cls: 'live',  label: 'Live · ' + age + 's ago' };
      if (age < 180) return { cls: 'live',  label: 'Live · ' + Math.round(age/60) + 'm ago' };
    }
    if (s.timestamp) {
      const h = age / 3600;
      if (h < 1)  return { cls: 'stale', label: 'Stale · ' + Math.round(age/60) + 'm ago' };
      if (h < 6)  return { cls: 'stale', label: 'Stale · ' + h.toFixed(1) + 'h ago' };
      return { cls: 'dead', label: 'Offline · ' + Math.round(h) + 'h ago' };
    }
    return { cls: 'dead', label: 'Offline' };
  }
  function refreshNavStatus() {
    fetch('/api/poller-status').then(r => r.json()).then(s => {
      const dot   = document.getElementById('nav-status-dot');
      const label = document.getElementById('nav-status-label');
      if (!dot || !label) return;
      const st = fmtNavStatus(s);
      dot.className = 'status-dot ' + st.cls;
      label.textContent = st.label;
    }).catch(() => {});
  }
  refreshNavStatus();
  setInterval(refreshNavStatus, 30000);
})();
</script>
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
  <div id="weather-strip" style="display:none; margin-bottom:0.75rem;">
    <div id="weather-days" style="display:flex; gap:6px; padding:2px 0;"></div>
  </div>
  <style>
  .wx-card {
    background:var(--surface2); border:1px solid var(--border); border-radius:10px;
    padding:8px 6px; text-align:center; flex:1; min-width:0;
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
          {%- macro render_breaker(b) %}
            {% if b.channel_name %}
            <a class="breaker {{ b.cls }}" href="/circuit/{{ b.channel_name|urlencode }}">
              {% if b.is_peak %}<div class="breaker-peak-badge">⚡ top</div>{% endif %}
              <div class="breaker-num">{{ b.slot }}</div>
              <div class="breaker-body">
                <div class="breaker-name">{{ b.label }}</div>
                <div class="breaker-watts">
                  {{ "%.0f"|format(b.watts) }} W
                  {% if b.amps %}&bull; {{ b.poles }}P/{{ b.amps }}A{% endif %}
                </div>
                {% if b.load_label %}
                <div class="breaker-load-row">
                  <div class="breaker-load-bar-wrap">
                    <div class="breaker-load-bar-fill {{ b.fill_cls }}" style="width:{{ b.load_bar_w }}%"></div>
                  </div>
                  <span class="breaker-load-text {{ b.load_cls }}">{{ b.load_label }}</span>
                </div>
                {% endif %}
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
          {%- endmacro %}
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px;">
            <div style="display:flex; flex-direction:column; gap:6px;">
              {% for b in breakers_left %}{{ render_breaker(b) }}{% endfor %}
            </div>
            <div style="display:flex; flex-direction:column; gap:6px;">
              {% for b in breakers_right %}{{ render_breaker(b) }}{% endfor %}
            </div>
          </div>
        </div>

        <!-- Right: 2-column metrics grid -->
        <div style="display:flex; flex-direction:column; gap:10px;">

          <!-- Row 1: $/hr now + 24h Avg with yesterday delta -->
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div class="card">
              <div class="card-label">Cost Right Now</div>
              <div class="card-value">${{ "%.2f"|format(cost_per_hour) }}<span class="unit">/hr</span></div>
              <div class="card-meta">{{ "%.0f"|format(current_watts) }} W at ${{ "%.4f"|format(rate) }}/kWh</div>
            </div>
            <div class="card">
              <div class="card-label">Panel Balance</div>
              {% if leg_a and leg_b %}
              {% set leg_total = leg_a.watts + leg_b.watts %}
              {% set pct_a = (leg_a.watts / (leg_total or 1) * 100)|round(0)|int %}
              {% set pct_b = (leg_b.watts / (leg_total or 1) * 100)|round(0)|int %}
              <div style="display:flex; gap:6px; align-items:center; margin:4px 0;">
                <div style="flex:1; text-align:center;">
                  <div style="font-size:0.65rem; color:var(--text-light); text-transform:uppercase; letter-spacing:0.06em;">Leg A</div>
                  <div style="font-size:1.1rem; font-weight:700; color:var(--text);">{{ "%.0f"|format(leg_a.watts) }}<span style="font-size:0.7rem; font-weight:400;"> W</span></div>
                  <div style="font-size:0.7rem; color:var(--text-light);">{{ pct_a }}%</div>
                </div>
                <div style="width:1px; background:var(--border); align-self:stretch;"></div>
                <div style="flex:1; text-align:center;">
                  <div style="font-size:0.65rem; color:var(--text-light); text-transform:uppercase; letter-spacing:0.06em;">Leg B</div>
                  <div style="font-size:1.1rem; font-weight:700; color:var(--text);">{{ "%.0f"|format(leg_b.watts) }}<span style="font-size:0.7rem; font-weight:400;"> W</span></div>
                  <div style="font-size:0.7rem; color:var(--text-light);">{{ pct_b }}%</div>
                </div>
              </div>
              <div style="height:5px; background:var(--surface2); border-radius:3px; overflow:hidden;">
                <div style="height:5px; width:{{ pct_a }}%; background:var(--olive-500); border-radius:3px;"></div>
              </div>
              {% else %}
              <div class="card-value" style="font-size:1rem;">—</div>
              <div class="card-meta">no leg data</div>
              {% endif %}
            </div>
          </div>

          <!-- Row 2: 24h Cost + MTD -->
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div class="card">
              <div class="card-label">24h Cost</div>
              <div class="card-value">${{ "%.2f"|format((total_24h.total_cents or 0) / 100) }}</div>
              <div class="card-meta">{{ "%.2f"|format(total_24h.total_kwh or 0) }} kWh used</div>
            </div>
            <div class="card">
              <div class="card-label">Month-to-Date</div>
              <div class="card-value">${{ "%.2f"|format((month_comparison.this_month.total_cents or 0) / 100) if month_comparison.this_month else '0.00' }}</div>
              <div class="card-meta">{{ "%.1f"|format(month_comparison.this_month.total_kwh or 0) if month_comparison.this_month else '0' }} kWh &bull; {{ delta_month|safe }}</div>
            </div>
          </div>

          <!-- Row 3: Peak today + Budget -->
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div class="card">
              <div class="card-label">Peak Today</div>
              {% if peak_24h.peak_watts %}
              <div class="card-value" style="font-size:1.4rem;">{{ "%.0f"|format(peak_24h.peak_watts) }}<span class="unit">W</span></div>
              <div class="card-meta">at {{ peak_24h.peak_time }}</div>
              {% else %}
              <div class="card-value" style="font-size:1.4rem;">—</div>
              <div class="card-meta">no data yet</div>
              {% endif %}
            </div>
            <div class="card">
              <div class="card-label">7-Day Trend</div>
              <div class="card-value" style="font-size:1.4rem; color:{{ trend_color }};">{{ trend_dir }}</div>
              <div class="card-meta">
                {{ "%.2f"|format(trend_avg) }} kWh/day avg
                {% if slope %}· {{ "%.3f"|format(slope|abs) }} kWh/day {% if slope > 0 %}more{% else %}less{% endif %}{% endif %}
              </div>
            </div>
          </div>

          <!-- Row 4: Top active circuits mini-list -->
          <div class="card">
            <div class="card-label" style="margin-bottom:8px;">Top Active Circuits</div>
            {% for c in top_circuits[:8] %}
            <div style="display:flex; align-items:center; gap:8px; padding:3px 0;
                        border-bottom:{% if not loop.last %}1px solid var(--border){% else %}none{% endif %};">
              <div style="flex:1; font-size:0.82rem; font-weight:500; color:var(--text);
                          white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                <a href="/circuit/{{ c.channel_name|urlencode }}" style="color:inherit; text-decoration:none;">{{ c.channel_name }}</a>
              </div>
              <div style="font-size:0.82rem; font-weight:700; color:var(--text); min-width:48px; text-align:right;">{{ "%.0f"|format(c.watts) }} W</div>
              <div style="width:56px; height:5px; background:var(--surface2); border-radius:3px; flex-shrink:0;">
                <div style="height:5px; border-radius:3px; width:{{ c.pct|round(1) }}%;
                  background:{{ '#f87171' if c.pct >= 40 else ('#fbbf24' if c.pct >= 20 else 'var(--olive-500)') }};"></div>
              </div>
              <div style="font-size:0.72rem; color:var(--text-light); min-width:30px; text-align:right;">{{ "%.0f"|format(c.pct) }}%</div>
            </div>
            {% else %}
            <div style="color:var(--text-light); font-size:0.8rem; font-style:italic;">No circuit data</div>
            {% endfor %}
          </div>

          <!-- Row 5: Standby / vampire loads -->
          <div class="card">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
              <div class="card-label">Standby Loads <span style="font-weight:400; color:var(--text-light);">(1–50 W always-on)</span></div>
              {% if standby %}
              <div style="font-size:0.8rem; font-weight:700; color:var(--text);">{{ standby|length }} circuits · {{ "%.0f"|format(standby_total_w) }} W total</div>
              {% endif %}
            </div>
            {% if standby %}
            {% for s in standby[:5] %}
            <div style="display:flex; justify-content:space-between; padding:2px 0;
                        border-bottom:{% if not loop.last %}1px solid var(--border){% else %}none{% endif %};">
              <span style="font-size:0.8rem; color:var(--text);">{{ s.name }}</span>
              <span style="font-size:0.8rem; color:var(--text-light);">{{ "%.0f"|format(s.watts) }} W</span>
            </div>
            {% endfor %}
            {% if standby|length > 5 %}
            <div style="font-size:0.75rem; color:var(--text-light); margin-top:4px;">+ {{ standby|length - 5 }} more</div>
            {% endif %}
            {% else %}
            <div style="color:var(--text-light); font-size:0.8rem; font-style:italic;">No standby loads detected</div>
            {% endif %}
          </div>

          <!-- Row 6: Best day / Worst day -->
          {% if trend and trend.best_day and trend.worst_day %}
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div class="card">
              <div class="card-label">Best Day (14d)</div>
              <div class="card-value" style="font-size:1.1rem; color:#81c784;">{{ "%.1f"|format(trend.best_day.total_kwh) }}<span class="unit">kWh</span></div>
              <div class="card-meta">{{ trend.best_day.day }}</div>
            </div>
            <div class="card">
              <div class="card-label">Peak Day (14d)</div>
              <div class="card-value" style="font-size:1.1rem; color:#f87171;">{{ "%.1f"|format(trend.worst_day.total_kwh) }}<span class="unit">kWh</span></div>
              <div class="card-meta">{{ trend.worst_day.day }}</div>
            </div>
          </div>
          {% endif %}

          <!-- Row 7: This month vs last month -->
          {% if month_comparison.this_month and month_comparison.last_month %}
          {% set this_kwh = month_comparison.this_month.total_kwh or 0 %}
          {% set last_kwh = month_comparison.last_month.total_kwh or 1 %}
          {% set vs_bar = [((this_kwh / last_kwh) * 100)|round(0)|int, 100]|min %}
          <div class="card">
            <div class="card-label" style="margin-bottom:6px;">Month vs Last Month</div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:6px;">
              <div>
                <div style="font-size:0.65rem; color:var(--text-light); text-transform:uppercase; letter-spacing:0.06em;">This Month</div>
                <div style="font-size:1.1rem; font-weight:700; color:var(--text);">{{ "%.1f"|format(this_kwh) }} <span style="font-size:0.7rem; font-weight:400;">kWh</span></div>
                <div style="font-size:0.72rem; color:var(--text-light);">${{ "%.2f"|format((month_comparison.this_month.total_cents or 0) / 100) }}</div>
              </div>
              <div>
                <div style="font-size:0.65rem; color:var(--text-light); text-transform:uppercase; letter-spacing:0.06em;">Last Month</div>
                <div style="font-size:1.1rem; font-weight:700; color:var(--text);">{{ "%.1f"|format(last_kwh) }} <span style="font-size:0.7rem; font-weight:400;">kWh</span></div>
                <div style="font-size:0.72rem; color:var(--text-light);">${{ "%.2f"|format((month_comparison.last_month.total_cents or 0) / 100) }}</div>
              </div>
            </div>
            <div style="height:5px; background:var(--surface2); border-radius:3px; position:relative;">
              <div style="height:5px; width:{{ vs_bar }}%; border-radius:3px;
                background:{{ '#f87171' if this_kwh > last_kwh else '#81c784' }};"></div>
            </div>
            <div style="font-size:0.72rem; color:var(--text-light); margin-top:3px;">
              {% set diff_pct = ((this_kwh - last_kwh) / last_kwh * 100)|round(1) %}
              {{ '↑' if diff_pct > 0 else '↓' }} {{ diff_pct|abs|round(1) }}% vs last month (full)
            </div>
          </div>
          {% endif %}

          <!-- Row 8: Peak usage hours (historical pattern) -->
          {% if peak_usage.peak_hours %}
          <div class="card">
            <div class="card-label" style="margin-bottom:8px;">Busiest Hours <span style="font-weight:400; color:var(--text-light);">(30-day avg)</span></div>
            {% set max_peak = peak_usage.peak_hours[0].avg_kwh or 1 %}
            {% for h in peak_usage.peak_hours[:5] %}
            <div style="display:flex; align-items:center; gap:8px; padding:2px 0;
                        border-bottom:{% if not loop.last %}1px solid var(--border){% else %}none{% endif %};">
              <div style="min-width:52px; font-size:0.78rem; color:var(--text);">{{ h.hour_label }}</div>
              <div style="flex:1; height:5px; background:var(--surface2); border-radius:3px;">
                <div style="height:5px; border-radius:3px; width:{{ ((h.avg_kwh / max_peak) * 100)|round(1) }}%;
                  background:var(--olive-500);"></div>
              </div>
              <div style="min-width:52px; font-size:0.75rem; color:var(--text-light); text-align:right;">
                {{ "%.4f"|format(h.avg_kwh) }} kWh
              </div>
            </div>
            {% endfor %}
          </div>
          {% endif %}

          <!-- Row 9: Peak days of week -->
          {% if peak_usage.peak_days %}
          <div class="card">
            <div class="card-label" style="margin-bottom:8px;">Busiest Days <span style="font-weight:400; color:var(--text-light);">(30-day avg)</span></div>
            {% set max_day = peak_usage.peak_days[0].avg_kwh or 1 %}
            {% for d in peak_usage.peak_days[:5] %}
            <div style="display:flex; align-items:center; gap:8px; padding:2px 0;
                        border-bottom:{% if not loop.last %}1px solid var(--border){% else %}none{% endif %};">
              <div style="min-width:72px; font-size:0.78rem; color:var(--text);">{{ d.day }}</div>
              <div style="flex:1; height:5px; background:var(--surface2); border-radius:3px;">
                <div style="height:5px; border-radius:3px; width:{{ ((d.avg_kwh / max_day) * 100)|round(1) }}%;
                  background:var(--olive-600);"></div>
              </div>
              <div style="min-width:52px; font-size:0.75rem; color:var(--text-light); text-align:right;">
                {{ "%.4f"|format(d.avg_kwh) }} kWh
              </div>
            </div>
            {% endfor %}
          </div>
          {% endif %}

          <!-- Row 10: Biggest 24h load -->
          {% if biggest_circuit %}
          <div class="card">
            <div class="card-label">Biggest 24h Load</div>
            <div style="display:flex; align-items:baseline; justify-content:space-between; margin-top:4px;">
              <a href="/circuit/{{ biggest_circuit.channel_name|urlencode }}"
                 style="font-size:1.1rem; font-weight:700; color:var(--text); text-decoration:none;">
                {{ biggest_circuit.channel_name }}
              </a>
              <span style="font-size:0.82rem; color:var(--text-light);">
                {{ "%.2f"|format(biggest_circuit.total_kwh or 0) }} kWh &bull; {{ "%.0f"|format(biggest_circuit.pct or 0) }}% of total
              </span>
            </div>
            <div style="height:5px; background:var(--surface2); border-radius:3px; margin-top:6px;">
              <div style="height:5px; border-radius:3px; width:{{ biggest_circuit.pct|round(1) }}%;
                background:{{ '#f87171' if biggest_circuit.pct > 40 else ('#fbbf24' if biggest_circuit.pct > 20 else 'var(--olive-500)') }};"></div>
            </div>
          </div>
          {% endif %}

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

    <!-- Two-column breaker grid — left = odd slots, right = even slots -->
    {%- macro render_breaker_c(b) %}
      {% if b.channel_name %}
      <a class="breaker {{ b.cls }}" href="/circuit/{{ b.channel_name|urlencode }}">
        {% if b.is_peak %}<div class="breaker-peak-badge">⚡ top</div>{% endif %}
        <div class="breaker-num">{{ b.slot }}</div>
        <div class="breaker-body">
          <div class="breaker-name">{{ b.label }}</div>
          <div class="breaker-watts">
            {{ "%.0f"|format(b.watts) }} W
            {% if b.amps %}&bull; {{ b.poles }}P/{{ b.amps }}A{% endif %}
          </div>
          {% if b.load_label %}
          <div class="breaker-load-row">
            <div class="breaker-load-bar-wrap">
              <div class="breaker-load-bar-fill {{ b.fill_cls }}" style="width:{{ b.load_bar_w }}%"></div>
            </div>
            <span class="breaker-load-text {{ b.load_cls }}">{{ b.load_label }}</span>
          </div>
          {% endif %}
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
    {%- endmacro %}
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px;">
      <div style="display:flex; flex-direction:column; gap:6px;">
        {% for b in breakers_left %}{{ render_breaker_c(b) }}{% endfor %}
      </div>
      <div style="display:flex; flex-direction:column; gap:6px;">
        {% for b in breakers_right %}{{ render_breaker_c(b) }}{% endfor %}
      </div>
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

  <!-- ── Poller Health Card ─────────────────────────────────────────────── -->
  <div class="card" id="pollerHealth" style="margin-bottom:1.5rem;">
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:0.75rem; flex-wrap:wrap;">
      <h3 style="font-size:1.05rem; margin:0;">Poller Health</h3>
      <span id="ph-dot" style="display:inline-block; width:10px; height:10px; border-radius:50%; background:var(--stone-400);"></span>
      <span id="ph-label" style="font-size:0.82rem; color:var(--text-light);">Checking…</span>
      <div style="flex:1;"></div>
      <button onclick="requestReconnect()" id="reconnectBtn"
              style="padding:7px 18px; background:var(--olive-800); color:var(--olive-50);
                     border:none; border-radius:8px; font-size:0.82rem; cursor:pointer; font-family:inherit;">
        ⟳ Reconnect
      </button>
    </div>

    <!-- Status details row -->
    <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:1rem;">
      <div style="background:var(--surface2); border-radius:8px; padding:10px 14px;">
        <div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:.06em; color:var(--text-light); margin-bottom:3px;">Last Poll</div>
        <div id="ph-last" style="font-size:0.85rem; font-weight:600;">—</div>
      </div>
      <div style="background:var(--surface2); border-radius:8px; padding:10px 14px;">
        <div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:.06em; color:var(--text-light); margin-bottom:3px;">Consecutive Errors</div>
        <div id="ph-errs" style="font-size:0.85rem; font-weight:600;">—</div>
      </div>
      <div style="background:var(--surface2); border-radius:8px; padding:10px 14px;">
        <div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:.06em; color:var(--text-light); margin-bottom:3px;">Last Error</div>
        <div id="ph-err" style="font-size:0.78rem; word-break:break-word; color:var(--red);">—</div>
      </div>
    </div>

    <!-- Error / reconnect message -->
    <div id="ph-error-banner" style="display:none; padding:0.6rem 0.9rem; background:oklch(94% 0.06 25);
         border:1px solid oklch(75% 0.12 25); border-radius:8px; font-size:0.82rem;
         color:oklch(35% 0.14 25); margin-bottom:0.75rem;"></div>

    <!-- Reconnect panel (hidden until poller is detected dead / user clicks Reconnect) -->
    <div id="reconnectPanel" style="display:none; border-top:1px solid var(--border); padding-top:0.85rem; margin-top:0.5rem;">
      <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:0.6rem;">
        Enter your Emporia credentials to force a fresh re-authentication.
        Leave blank to use saved credentials.
      </p>
      <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:flex-end;">
        <label style="font-size:0.78rem; font-weight:600;">
          Email
          <input type="email" id="rcEmail" placeholder="(saved)" autocomplete="username"
                 style="display:block; margin-top:3px; padding:7px 10px; border-radius:7px;
                        border:1px solid var(--border); background:var(--bg); color:var(--text);
                        font-size:0.85rem; font-family:inherit; width:220px;">
        </label>
        <label style="font-size:0.78rem; font-weight:600;">
          Password
          <input type="password" id="rcPwd" placeholder="(saved)" autocomplete="current-password"
                 style="display:block; margin-top:3px; padding:7px 10px; border-radius:7px;
                        border:1px solid var(--border); background:var(--bg); color:var(--text);
                        font-size:0.85rem; font-family:inherit; width:180px;">
        </label>
        <button onclick="sendReconnect()"
                style="padding:8px 20px; background:var(--olive-700); color:var(--olive-50);
                       border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
          Re-authenticate →
        </button>
      </div>
      <div id="rcMsg" style="margin-top:0.6rem; font-size:0.82rem; display:none;"></div>
    </div>
  </div>

  <!-- ── Poll log table ─────────────────────────────────────────────────── -->
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
    Start poller: <code>python3 energy.py</code>
  </div>
</div>

<script>
function fmtAge(secs) {
  if (secs === null || secs === undefined) return '—';
  if (secs < 90)  return secs + 's ago';
  if (secs < 3600) return Math.round(secs/60) + 'm ago';
  return Math.round(secs/3600) + 'h ago';
}

function refreshStatus() {
  fetch('/api/poller-status').then(r=>r.json()).then(d => {
    const dot   = document.getElementById('ph-dot');
    const label = document.getElementById('ph-label');
    const last  = document.getElementById('ph-last');
    const errs  = document.getElementById('ph-errs');
    const errEl = document.getElementById('ph-err');
    const banner = document.getElementById('ph-error-banner');
    const panel  = document.getElementById('reconnectPanel');

    // Running status
    if (!d.timestamp) {
      dot.style.background = 'var(--stone-400)';
      label.textContent = 'Poller not running';
      label.style.color = 'var(--text-light)';
      panel.style.display = 'block';
    } else if (d.poller_running && d.ok) {
      dot.style.background = 'var(--green)';
      dot.style.boxShadow = '0 0 0 3px rgba(90,138,94,0.25)';
      label.textContent = 'Live';
      label.style.color = 'var(--green)';
      panel.style.display = 'none';
      banner.style.display = 'none';
    } else if (d.poller_running && !d.ok) {
      dot.style.background = 'var(--amber)';
      label.textContent = 'Polling errors';
      label.style.color = 'var(--amber)';
      panel.style.display = 'block';
    } else {
      dot.style.background = 'var(--red)';
      label.textContent = 'Offline · ' + fmtAge(d.age_secs);
      label.style.color = 'var(--red)';
      panel.style.display = 'block';
    }

    last.textContent = d.timestamp ? (d.timestamp.slice(0,19).replace('T',' ') + ' (' + fmtAge(d.age_secs) + ')') : '—';
    errs.textContent = d.consecutive_errors ?? '—';
    errs.style.color = (d.consecutive_errors > 0) ? 'var(--red)' : 'var(--text)';

    if (d.error) {
      errEl.textContent = d.error;
      banner.textContent = '⚠ ' + d.error;
      banner.style.display = 'block';
    } else {
      errEl.textContent = 'None';
      errEl.style.color = 'var(--text-light)';
      banner.style.display = 'none';
    }
  }).catch(() => {
    document.getElementById('ph-label').textContent = 'Could not reach Flask API';
  });
}

function requestReconnect() {
  document.getElementById('reconnectPanel').style.display = 'block';
}

function sendReconnect() {
  const email = document.getElementById('rcEmail').value.trim();
  const pwd   = document.getElementById('rcPwd').value;
  const msg   = document.getElementById('rcMsg');
  msg.style.display = 'none';
  fetch('/api/poller-reconnect', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({email: email || null, password: pwd || null})
  }).then(r=>r.json()).then(d => {
    msg.style.display = 'block';
    msg.style.color = d.ok ? 'var(--green)' : 'var(--red)';
    msg.textContent = d.ok ? ('✓ ' + d.message) : ('✗ ' + d.error);
    if (d.ok) setTimeout(refreshStatus, 5000);
  }).catch(e => {
    msg.style.display = 'block';
    msg.style.color = 'var(--red)';
    msg.textContent = 'Request failed: ' + e;
  });
}

refreshStatus();
setInterval(refreshStatus, 15000);
setTimeout(() => location.reload(), 30000);
</script>
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
                             "label": None, "note": None, "amps": None, "poles": 1}
    max_w = max((_watts_estimate(latest_map.get(n, 0)) for n in ordered), default=1) or 1
    # Identify top-load circuits for the "peak user" badge
    _live_watts = {n: _watts_estimate(latest_map.get(n, 0)) for n in ordered}
    _sorted_watts = sorted(_live_watts.values(), reverse=True)
    _peak_threshold = _sorted_watts[2] if len(_sorted_watts) >= 3 else (_sorted_watts[0] if _sorted_watts else 0)
    dash_breakers = []
    for slot in range(1, max(len(ordered) + 1, max(layout.keys(), default=0) + 1)):
        row   = layout.get(slot, {})
        name  = row.get("channel_name")
        rated_amps = row.get("amps")
        poles = row.get("poles") or 1
        voltage = 240 if poles == 2 else 120
        watts = _watts_estimate(latest_map.get(name, 0)) if name else 0
        bar   = min(100, watts / max_w * 100)
        # Safety zone vs rated breaker amps
        if rated_amps and rated_amps > 0 and watts > 0:
            amps_now  = watts / voltage
            load_pct  = amps_now / rated_amps * 100
            if load_pct >= 100:
                sz_cls = "sz-danger";   load_cls = "load-danger";   fill_cls = "fill-danger"
            elif load_pct >= 80:
                sz_cls = "sz-caution";  load_cls = "load-caution";  fill_cls = "fill-caution"
            elif load_pct >= 60:
                sz_cls = "sz-moderate"; load_cls = "load-moderate"; fill_cls = "fill-moderate"
            else:
                sz_cls = "";            load_cls = "load-normal";   fill_cls = "fill-normal"
            load_bar_w = min(100, load_pct)  # % width of load bar
            load_label = f"{amps_now:.1f}/{rated_amps}A"
        else:
            sz_cls = ""; load_cls = ""; fill_cls = "fill-normal"
            load_bar_w = 0; load_label = ""
        is_peak = bool(name and watts >= _peak_threshold and watts > 0)
        cls = sz_cls + (" active-heat" if bar > 75 else " active-high" if bar > 40 else "")
        dash_breakers.append({
            "slot": slot, "channel_name": name,
            "label": row.get("label") or name or "—",
            "note": row.get("note"), "amps": rated_amps, "poles": poles,
            "watts": watts, "bar_pct": bar, "cls": cls.strip(),
            "load_cls": load_cls, "fill_cls": fill_cls,
            "load_bar_w": load_bar_w, "load_label": load_label,
            "is_peak": is_peak,
        })

    # Panel column invert — read from settings.json
    import json as _json
    _cfg = {}
    try:
        with open("settings.json") as _f:
            _cfg = _json.load(_f)
    except Exception:
        pass
    _pinv = _cfg.get("panel_display", {})
    _invert_left  = _pinv.get("invert_left",  False)
    _invert_right = _pinv.get("invert_right", False)
    breakers_left  = [b for b in dash_breakers if b["slot"] % 2 == 1]
    breakers_right = [b for b in dash_breakers if b["slot"] % 2 == 0]
    if _invert_left:  breakers_left  = list(reversed(breakers_left))
    if _invert_right: breakers_right = list(reversed(breakers_right))

    peak_usage = energy.get_peak_usage()
    for h in peak_usage["peak_hours"]:
        h["hour_label"] = _format_hour(h["hour"])

    peak_24h = energy.get_peak_24h()

    mc = energy.get_month_comparison()

    # Month delta badge
    if mc["this_month"] and mc["last_month"]:
        ch = ((mc["this_month"]["total_kwh"] or 0) -
              (mc["last_month"]["total_kwh"] or 0)) / (mc["last_month"]["total_kwh"] or 1) * 100
        delta_month = _delta_badge(ch, "last month", invert=True)
    else:
        delta_month = _delta_badge(None, "last month")

    # Standby / vampire loads — circuits with a live reading between 1W and 50W
    standby = [
        {"name": name, "watts": _watts_estimate(kwh)}
        for name, kwh in latest_map.items()
        if name not in _MAINS_NAMES and name not in _SKIP_NAMES
        and not str(name).isdigit()
        and 1 <= _watts_estimate(kwh) <= 50
    ]
    standby.sort(key=lambda x: x["watts"], reverse=True)
    standby_total_w = sum(s["watts"] for s in standby)

    # $/hr at current draw
    cost_per_hour = current_watts / 1000 * RATE

    # Leg A / Leg B balance for panel sidebar
    _legs = [m for m in dash_mains if not m.get("is_total")]
    leg_a = _legs[0] if len(_legs) > 0 else None
    leg_b = _legs[1] if len(_legs) > 1 else None
    total_leg_w = (leg_a["watts"] if leg_a else 0) + (leg_b["watts"] if leg_b else 0)

    # 7-day trend label
    slope = trend.get("slope", 0) if trend else 0
    if slope > 0.05:
        trend_dir = "↑ rising"
        trend_color = "#f87171"
    elif slope < -0.05:
        trend_dir = "↓ falling"
        trend_color = "#81c784"
    else:
        trend_dir = "→ steady"
        trend_color = "var(--text-light)"
    trend_avg = trend.get("avg_kwh", 0) if trend else 0

    return _render(
        DASH_HTML,
        active_page="dashboard",
        ctx=ctx,
        current_watts=current_watts,
        cost_per_hour=cost_per_hour,
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
        peak_24h=peak_24h,
        dash_mains=dash_mains,
        dash_breakers=dash_breakers,
        breakers_left=breakers_left,
        breakers_right=breakers_right,
        panel_invert_left=_invert_left,
        panel_invert_right=_invert_right,
        trend=trend,
        standby=standby,
        standby_total_w=standby_total_w,
        leg_a=leg_a, leg_b=leg_b,
        trend_dir=trend_dir, trend_color=trend_color, trend_avg=trend_avg,
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
                              "label": None, "note": None, "amps": None, "poles": 1}

    max_w = max((_watts_estimate(latest_map.get(n, 0)) for n in all_circuits), default=1) or 1
    _live_w_c = {n: _watts_estimate(latest_map.get(n, 0)) for n in all_circuits}
    _sorted_w_c = sorted(_live_w_c.values(), reverse=True)
    _peak_thr_c = _sorted_w_c[2] if len(_sorted_w_c) >= 3 else (_sorted_w_c[0] if _sorted_w_c else 0)

    breakers = []
    for slot in range(1, panel_slots + 1):
        row = layout.get(slot, {})
        name  = row.get("channel_name")
        rated_amps = row.get("amps")
        poles = row.get("poles") or 1
        voltage = 240 if poles == 2 else 120
        watts = _watts_estimate(latest_map.get(name, 0)) if name else 0
        bar   = min(100, watts / max_w * 100)
        if rated_amps and rated_amps > 0 and watts > 0:
            amps_now  = watts / voltage
            load_pct  = amps_now / rated_amps * 100
            if load_pct >= 100:
                sz_cls = "sz-danger";   load_cls = "load-danger";   fill_cls = "fill-danger"
            elif load_pct >= 80:
                sz_cls = "sz-caution";  load_cls = "load-caution";  fill_cls = "fill-caution"
            elif load_pct >= 60:
                sz_cls = "sz-moderate"; load_cls = "load-moderate"; fill_cls = "fill-moderate"
            else:
                sz_cls = "";            load_cls = "load-normal";   fill_cls = "fill-normal"
            load_bar_w = min(100, load_pct)
            load_label = f"{amps_now:.1f}/{rated_amps}A"
        else:
            sz_cls = ""; load_cls = ""; fill_cls = "fill-normal"
            load_bar_w = 0; load_label = ""
        is_peak = bool(name and watts >= _peak_thr_c and watts > 0)
        cls = sz_cls + (" active-heat" if bar > 75 else " active-high" if bar > 40 else "")
        breakers.append({
            "slot":         slot,
            "channel_name": name,
            "label":        row.get("label") or name or "—",
            "note":         row.get("note"),
            "amps":         rated_amps,
            "poles":        poles,
            "watts":        watts,
            "bar_pct":      bar,
            "cls":          cls.strip(),
            "load_cls":     load_cls,
            "fill_cls":     fill_cls,
            "load_bar_w":   load_bar_w,
            "load_label":   load_label,
            "is_peak":      is_peak,
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

    # Split breakers into left (odd) / right (even) columns with invert support
    import json as _json2
    _cfg2 = {}
    try:
        with open("settings.json") as _f2:
            _cfg2 = _json2.load(_f2)
    except Exception:
        pass
    _pinv2 = _cfg2.get("panel_display", {})
    breakers_left  = [b for b in breakers if b["slot"] % 2 == 1]
    breakers_right = [b for b in breakers if b["slot"] % 2 == 0]
    if _pinv2.get("invert_left",  False): breakers_left  = list(reversed(breakers_left))
    if _pinv2.get("invert_right", False): breakers_right = list(reversed(breakers_right))

    return _render(CIRCUITS_HTML, active_page="circuits",
                   mains=mains, breakers=breakers,
                   breakers_left=breakers_left, breakers_right=breakers_right,
                   usage_rows=usage_rows,
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
    """Fetch 14-day forecast from Open-Meteo for zip 18947 (Pipersville, PA).
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
            "&forecast_days=14"
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


@app.route("/api/poller-status")
def api_poller_status():
    """Return the live heartbeat written by the energy.py poller process."""
    import json as _json, os as _os, time as _time
    status = energy.read_poller_status()
    # Calculate how many seconds ago the last heartbeat was
    last_ts = status.get("timestamp")
    age_secs = None
    if last_ts:
        try:
            from datetime import datetime as _dt
            age_secs = int((_dt.now() - _dt.fromisoformat(last_ts[:19])).total_seconds())
        except Exception:
            pass
    status["age_secs"] = age_secs
    # Poller is considered "running" if heartbeat is < 3 minutes old
    status["poller_running"] = age_secs is not None and age_secs < 180
    return jsonify(status)


@app.route("/api/poller-reconnect", methods=["POST"])
def api_poller_reconnect():
    """
    Trigger a reconnect in the background poller.
    Optionally accepts {email, password} to refresh credentials first.
    Writes reconnect.flag which the poller loop checks every cycle.
    """
    import json as _json, os as _os
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    pwd   = (data.get("password") or "").strip()
    # Persist new credentials if supplied
    if email:
        cfg = {}
        try:
            with open("settings.json") as _f:
                cfg = _json.load(_f)
        except Exception:
            pass
        cfg["emporia_email"] = email
        if pwd:
            cfg["emporia_password"] = pwd
        with open("settings.json", "w") as _f:
            _json.dump(cfg, _f, indent=2)
    # Write the flag file — poller checks for it each loop iteration
    try:
        with open(energy.RECONNECT_FLAG_FILE, "w") as _f:
            _json.dump({"requested_at": __import__("datetime").datetime.now().isoformat()}, _f)
        return jsonify({"ok": True, "message": "Reconnect flag set — poller will re-authenticate on next cycle (up to 60s)"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
    <span class="section-sub">Assign circuits, breaker size &amp; pole type — drives live safety indicators</span>
  </div>
  <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:1rem;">
    Slot numbers mirror physical positions (1 = top-left, 2 = top-right, alternating down).
    <strong>Amps</strong> + <strong>poles</strong> determine voltage (1P = 120 V, 2P = 240 V) and enable the
    NEC 80% safety indicators on every breaker card.
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

  <!-- column headers -->
  <div style="display:grid; grid-template-columns:28px 1fr 110px 1fr 52px 56px; gap:5px;
              font-size:0.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em;
              color:var(--text-light); padding:0 4px 6px; border-bottom:1px solid var(--border); margin-bottom:4px;">
    <span>#</span><span>Circuit</span><span>Label</span><span>Note / description</span><span style="text-align:center">Amps</span><span style="text-align:center">Poles</span>
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
      <input class="inp-note" type="text" placeholder="e.g. Bedroom outlets" value="{{ b.note or '' }}" title="Hover note">
      <input class="inp-amps" type="number" placeholder="A" value="{{ b.amps or '' }}" min="1" max="200"
             style="text-align:center;" title="Breaker rating in amps (15, 20, 30, 50…)">
      <select class="sel-poles" title="Single-pole 120V or double-pole 240V"
              style="text-align:center; padding:4px 2px;">
        <option value="1" {{ 'selected' if (b.poles or 1)==1 else '' }}>1P</option>
        <option value="2" {{ 'selected' if (b.poles or 1)==2 else '' }}>2P</option>
      </select>
    </div>
    {% endfor %}
  </div>

  <!-- Safety zone legend -->
  <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:1.5rem; padding:0.75rem 1rem;
              background:var(--surface2); border-radius:10px; font-size:0.75rem; color:var(--text-light);">
    <strong style="color:var(--text); align-self:center;">Safety zones:</strong>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#4ade80;margin-right:4px;vertical-align:middle;"></span>Normal &lt; 60%</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#facc15;margin-right:4px;vertical-align:middle;"></span>Moderate 60–80%</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#f97316;margin-right:4px;vertical-align:middle;"></span>NEC limit ≥ 80%</span>
    <span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:#ef4444;margin-right:4px;vertical-align:middle;"></span>Over rated &gt; 100%</span>
    <span style="border-left:1px solid var(--border); padding-left:16px;">⚡ = top load circuit</span>
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
    poles:        parseInt(row.querySelector('.sel-poles').value) || 1,
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
    <span class="section-sub">System integrations &amp; configuration</span>
  </div>

  <div class="settings-wrap">

    <!-- ── Sidebar nav ── -->
    <div class="sys-nav">
      <div class="sys-nav-group">Active</div>
      <button class="sys-link active" onclick="showPanel('emporia',this)">
        <span class="sys-dot {{ 'ok' if has_tokens else 'warn' }}"></span>
        <div><div class="sys-name">Emporia Vue</div><div class="sys-sub">Energy Monitor</div></div>
      </button>

      <div class="sys-nav-group" style="margin-top:0.4rem;">Planned</div>
      <button class="sys-link" onclick="showPanel('aqara',this)">
        <span class="sys-dot soon"></span>
        <div><div class="sys-name">Aqara</div><div class="sys-sub">Temp &amp; Humidity</div></div>
      </button>
      <button class="sys-link" onclick="showPanel('kasa',this)">
        <span class="sys-dot soon"></span>
        <div><div class="sys-name">Kasa</div><div class="sys-sub">Light Switches</div></div>
      </button>
      <button class="sys-link" onclick="showPanel('broan',this)">
        <span class="sys-dot soon"></span>
        <div><div class="sys-name">Broan AI ERV</div><div class="sys-sub">Ventilation</div></div>
      </button>
      <button class="sys-link" onclick="showPanel('mitsubishi',this)">
        <span class="sys-dot soon"></span>
        <div><div class="sys-name">Mitsubishi</div><div class="sys-sub">Hyper Heat HVAC</div></div>
      </button>
      <button class="sys-link" onclick="showPanel('nuts',this)">
        <span class="sys-dot soon"></span>
        <div><div class="sys-name">NUTS / UPS</div><div class="sys-sub">ESP Remote Monitor</div></div>
      </button>
    </div>

    <!-- ── Content panels ── -->
    <div>

      <!-- ════════════════════════════════ EMPORIA VUE ════════════════════════════════ -->
      <div id="panel-emporia" class="sys-panel active">
        <div class="sys-panel-head">
          <h3>Emporia Vue Energy Monitor</h3>
          {% if has_tokens %}
          <span class="int-badge ok">&#10003; Connected</span>
          {% else %}
          <span class="int-badge warn">Not Authenticated</span>
          {% endif %}
        </div>

        <!-- Account credentials -->
        <div class="card" style="margin-bottom:1rem;">
          <h3 style="font-size:1rem; margin-bottom:0.5rem;">Account Credentials</h3>
          <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:1rem;">
            {% if has_tokens %}<span style="color:var(--green);">&#10003; Authenticated</span> — tokens cached in <code>keys.json</code>. Re-enter only if your password changed or tokens expired.
            {% else %}No tokens found. Enter your Emporia email &amp; password to start live polling.{% endif %}
          </p>
          <form style="display:flex; flex-direction:column; gap:10px; max-width:400px;">
            <label style="font-size:0.82rem; font-weight:600;">Email
              <input type="email" id="credEmail" value="{{ saved_email }}"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <label style="font-size:0.82rem; font-weight:600;">Password
              <input type="password" id="credPwd" placeholder="••••••••"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
              <button type="button" id="credBtn" onclick="saveCreds()"
                      style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50); border:none;
                             border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;
                             display:flex; align-items:center; gap:7px; transition:opacity .2s;">
                Save &amp; Authenticate
              </button>
              <span id="credMsg" style="font-size:0.82rem; display:none;"></span>
            </div>
            <!-- Live reconnect progress bar -->
            <div id="credProgress" style="display:none; margin-top:0.75rem;">
              <div style="font-size:0.78rem; color:var(--text-light); margin-bottom:5px;" id="credProgressLabel">Connecting…</div>
              <div style="height:4px; background:var(--border); border-radius:2px; overflow:hidden; max-width:320px;">
                <div id="credProgressBar" style="height:100%; width:0%; background:var(--olive-600); border-radius:2px; transition:width 0.4s ease;"></div>
              </div>
            </div>
          </form>
          <p style="font-size:0.75rem; color:var(--text-light); margin-top:0.75rem;">
            Credentials stored in <code>settings.json</code>. Authenticates and restarts the poller immediately.
          </p>
        </div>

        <!-- Rate & Budget -->
        <div class="card" style="margin-bottom:1rem;">
          <h3 style="font-size:1rem; margin-bottom:0.5rem;">Rate &amp; Budget</h3>
          <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:0.25rem;">
            <label style="font-size:0.82rem; font-weight:600;">Electricity rate (¢/kWh)
              <input type="number" id="cfgRate" step="0.01" value="{{ rate_cents }}"
                     style="display:block; width:130px; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-family:inherit;">
            </label>
            <label style="font-size:0.82rem; font-weight:600;">Monthly budget ($)
              <input type="number" id="cfgBudget" step="1" value="{{ monthly_budget }}"
                     style="display:block; width:130px; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-family:inherit;">
            </label>
          </div>
          <div style="display:flex; gap:10px; align-items:center; margin-top:0.85rem;">
            <button type="button" onclick="saveConfig()"
                    style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50); border:none;
                           border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
              Save Rate &amp; Budget
            </button>
            <span id="cfgMsg" style="font-size:0.82rem; color:var(--green); display:none;">Saved ✓</span>
          </div>
          <p style="font-size:0.75rem; color:var(--text-light); margin-top:0.5rem;">Restart Flask for rate changes to take effect.</p>
        </div>

        <!-- Panel Labels -->
        <div class="card" style="margin-bottom:1rem;">
          <h3 style="font-size:1rem; margin-bottom:0.5rem;">Panel Labels</h3>
          <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:0.85rem;">
            Name each Emporia device. Labels appear in the panel header and breaker views.
          </p>
          {% for gid in known_devices %}
          <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px; flex-wrap:wrap;">
            <code style="font-size:0.72rem; background:var(--stone-100); color:var(--stone-600);
                         padding:3px 7px; border-radius:5px; flex-shrink:0;">{{ gid }}</code>
            <input type="text" class="device-label-input" data-gid="{{ gid }}"
                   value="{{ device_labels.get(gid, '') }}" placeholder="e.g. House, Barn, Garage"
                   style="flex:1; min-width:180px; max-width:260px; padding:8px 10px; border-radius:8px;
                          border:1px solid var(--border); background:var(--bg); color:var(--text);
                          font-size:0.9rem; font-family:inherit;">
          </div>
          {% else %}
          <p style="font-size:0.82rem; color:var(--text-light); font-style:italic;">No devices in database yet.</p>
          {% endfor %}
          {% if known_devices %}
          <div style="display:flex; gap:10px; align-items:center; margin-top:0.6rem;">
            <button type="button" onclick="saveDeviceLabels()"
                    style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50); border:none;
                           border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
              Save Labels
            </button>
            <span id="lblMsg" style="font-size:0.82rem; color:var(--green); display:none;">Saved ✓</span>
          </div>
          {% endif %}
        </div>

        <!-- Panel Display -->
        <div class="card" style="margin-bottom:1rem;">
          <h3 style="font-size:1rem; margin-bottom:0.5rem;">Panel Display</h3>
          <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:0.85rem;">
            Residential panels wire odd slots top-to-bottom, but some installs run even slots bottom-to-top.
            Toggle to match your physical wiring.
          </p>
          <div style="display:flex; gap:2rem; flex-wrap:wrap; margin-bottom:0.85rem;">
            <label style="display:flex; align-items:center; gap:8px; font-size:0.88rem; cursor:pointer;">
              <input type="checkbox" id="invertLeft" {% if panel_invert_left %}checked{% endif %}
                     style="width:16px; height:16px; cursor:pointer; accent-color:var(--olive-700);">
              Invert left column (odd slots)
            </label>
            <label style="display:flex; align-items:center; gap:8px; font-size:0.88rem; cursor:pointer;">
              <input type="checkbox" id="invertRight" {% if panel_invert_right %}checked{% endif %}
                     style="width:16px; height:16px; cursor:pointer; accent-color:var(--olive-700);">
              Invert right column (even slots)
            </label>
          </div>
          <div style="display:flex; gap:10px; align-items:center;">
            <button type="button" onclick="savePanelDisplay()"
                    style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50); border:none;
                           border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
              Save Display Options
            </button>
            <span id="pdMsg" style="font-size:0.82rem; color:var(--green); display:none;">Saved ✓ — reload dashboard</span>
          </div>
        </div>

        <!-- Panel Layout -->
        <div class="card">
          <h3 style="font-size:1rem; margin-bottom:0.4rem;">Panel Layout</h3>
          <p style="font-size:0.82rem; color:var(--text-light); margin-bottom:0.75rem;">
            Assign circuits to physical breaker slots, set custom labels, pole count, and hover notes.
          </p>
          <a href="/panel" style="display:inline-block; padding:9px 22px; background:var(--olive-800);
             color:var(--olive-50); border-radius:8px; font-size:0.85rem; text-decoration:none;">
            Edit Panel Layout →
          </a>
        </div>
      </div><!-- /panel-emporia -->


      <!-- ════════════════════════════════ AQARA ════════════════════════════════ -->
      <div id="panel-aqara" class="sys-panel">
        <div class="sys-panel-head">
          <h3>Aqara — Temperature &amp; Humidity</h3>
          <span class="int-badge coming">Coming Soon</span>
        </div>

        <div class="card" style="margin-bottom:1rem;">
          <p style="font-size:0.85rem; color:var(--text-light); margin-bottom:1rem;">
            Connect your <strong>Aqara Hub M3</strong> to pull temperature, humidity, and contact sensor readings
            into a dedicated tab. Uses the <strong>Aqara Open Cloud API</strong> with OAuth2.
          </p>
          <div class="setup-steps">
            <p>Setup steps (when available):</p>
            <ol>
              <li>Register at <code>developer.aqara.com</code> → create an Application</li>
              <li>Copy your <strong>App ID</strong>, <strong>App Key</strong>, and <strong>Key ID</strong> below</li>
              <li>Click <strong>Authorize</strong> to link your Aqara account via OAuth2</li>
              <li>Sensor data will appear on the <em>Aqara</em> tab</li>
            </ol>
          </div>
          <form style="display:flex; flex-direction:column; gap:10px; max-width:400px; opacity:0.45; pointer-events:none;">
            <label style="font-size:0.82rem; font-weight:600;">App ID
              <input type="text" id="aqaraAppId" value="{{ aqara_app_id }}" placeholder="axxx000000000000"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <label style="font-size:0.82rem; font-weight:600;">App Key
              <input type="password" id="aqaraAppKey" placeholder="••••••••••••••••"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <label style="font-size:0.82rem; font-weight:600;">Key ID
              <input type="text" id="aqaraKeyId" value="{{ aqara_key_id }}" placeholder="Kxxx000000000000"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <div style="display:flex; gap:10px; flex-wrap:wrap;">
              <button type="button" style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50);
                      border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
                Save Credentials
              </button>
              <button type="button" style="padding:9px 22px; background:var(--surface2); color:var(--text);
                      border:1px solid var(--border); border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
                Authorize →
              </button>
            </div>
          </form>
          <p style="font-size:0.73rem; color:var(--stone-400); margin-top:0.75rem;">
            Credentials stored locally in <code>settings.json</code>. Your Aqara password is never stored — only the OAuth token.
          </p>
        </div>
      </div><!-- /panel-aqara -->


      <!-- ════════════════════════════════ KASA ════════════════════════════════ -->
      <div id="panel-kasa" class="sys-panel">
        <div class="sys-panel-head">
          <h3>Kasa — Smart Light Switches</h3>
          <span class="int-badge coming">Coming Soon</span>
        </div>

        <div class="card" style="margin-bottom:1rem;">
          <p style="font-size:0.85rem; color:var(--text-light); margin-bottom:1rem;">
            Control and monitor <strong>TP-Link Kasa</strong> smart light switches on your local network.
            Supports local UDP discovery — no cloud account required for basic on/off control and state monitoring.
          </p>
          <div class="setup-steps">
            <p>Planned capabilities:</p>
            <ul>
              <li>Auto-discover Kasa switches on the local network (port 9999 UDP broadcast)</li>
              <li>Display on/off state, power draw (where supported), and uptime</li>
              <li>Toggle switches from the dashboard</li>
              <li>Supported devices: <code>KS200M</code>, <code>HS200</code>, <code>HS210</code>, <code>KS205</code> and compatible</li>
              <li>Optional: TP-Link cloud account for remote access outside the LAN</li>
            </ul>
          </div>
          <form style="display:flex; flex-direction:column; gap:10px; max-width:400px; opacity:0.45; pointer-events:none;">
            <label style="font-size:0.82rem; font-weight:600;">Local subnet (optional, for scan)
              <input type="text" id="kasaSubnet" value="{{ kasa_host }}" placeholder="e.g. 192.168.1.0/24"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <div style="display:flex; gap:10px;">
              <button type="button" style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50);
                      border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit;">
                Discover Devices
              </button>
            </div>
          </form>
          <p style="font-size:0.73rem; color:var(--stone-400); margin-top:0.75rem;">
            Uses <code>python-kasa</code> library for local LAN control. No cloud credentials needed for local access.
          </p>
        </div>
      </div><!-- /panel-kasa -->


      <!-- ════════════════════════════════ BROAN ERV ════════════════════════════════ -->
      <div id="panel-broan" class="sys-panel">
        <div class="sys-panel-head">
          <h3>Broan AI ERV</h3>
          <span class="int-badge coming">Coming Soon</span>
        </div>

        <div class="card" style="margin-bottom:1rem;">
          <p style="font-size:0.85rem; color:var(--text-light); margin-bottom:1rem;">
            Monitor your <strong>Broan AI Energy Recovery Ventilator</strong> — fan speed, ventilation rates,
            air quality index, and filter status. Integration method is under evaluation.
          </p>
          <div class="setup-steps">
            <p>Possible integration paths:</p>
            <ul>
              <li><strong>Broan App API</strong> — cloud-based, requires Broan account credentials</li>
              <li><strong>Local Modbus/BACnet</strong> — if ERV exposes a local protocol</li>
              <li><strong>Matter / Home Assistant bridge</strong> — if paired via a Matter-compatible hub</li>
            </ul>
          </div>
          <form style="display:flex; flex-direction:column; gap:10px; max-width:400px; opacity:0.45; pointer-events:none;">
            <label style="font-size:0.82rem; font-weight:600;">Broan Account Email
              <input type="email" placeholder="you@example.com"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <label style="font-size:0.82rem; font-weight:600;">Password
              <input type="password" placeholder="••••••••"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <button type="button" style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50);
                    border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit; width:fit-content;">
              Connect
            </button>
          </form>
        </div>
      </div><!-- /panel-broan -->


      <!-- ════════════════════════════════ MITSUBISHI ════════════════════════════════ -->
      <div id="panel-mitsubishi" class="sys-panel">
        <div class="sys-panel-head">
          <h3>Mitsubishi Hyper Heat</h3>
          <span class="int-badge coming">Coming Soon</span>
        </div>

        <div class="card" style="margin-bottom:1rem;">
          <p style="font-size:0.85rem; color:var(--text-light); margin-bottom:1rem;">
            Monitor and control your <strong>Mitsubishi Hyper Heat</strong> mini-split system —
            set point, current mode (heat/cool/auto), room temperature, and compressor status.
          </p>
          <div class="setup-steps">
            <p>Possible integration paths:</p>
            <ul>
              <li><strong>MELCloud API</strong> — Mitsubishi's cloud service (requires MELCloud account + internet)</li>
              <li><strong>Kumo Cloud</strong> — US-market cloud API (if using Kumo Touch thermostat)</li>
              <li><strong>Local CN105 serial</strong> — direct wired integration via ESP32/ESPHome using the CN105 port</li>
              <li><strong>MelCloud Python library</strong> — <code>pymelcloud</code> on PyPI</li>
            </ul>
          </div>
          <form style="display:flex; flex-direction:column; gap:10px; max-width:400px; opacity:0.45; pointer-events:none;">
            <label style="font-size:0.82rem; font-weight:600;">MELCloud / Kumo Email
              <input type="email" placeholder="you@example.com"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <label style="font-size:0.82rem; font-weight:600;">Password
              <input type="password" placeholder="••••••••"
                     style="display:block; width:100%; margin-top:4px; padding:8px 10px; border-radius:8px;
                            border:1px solid var(--border); background:var(--bg); color:var(--text); font-size:0.9rem; font-family:inherit;">
            </label>
            <button type="button" style="padding:9px 22px; background:var(--olive-800); color:var(--olive-50);
                    border:none; border-radius:8px; font-size:0.85rem; cursor:pointer; font-family:inherit; width:fit-content;">
              Connect
            </button>
          </form>
        </div>
      </div><!-- /panel-mitsubishi -->


      <!-- ════════════════════════════════ NUTS / UPS ════════════════════════════════ -->
      <div id="panel-nuts" class="sys-panel">
        <div class="sys-panel-head">
          <h3>NUTS / UPS — ESP Remote Monitor</h3>
          <span class="int-badge coming">Coming Soon</span>
        </div>

        <div class="card" style="margin-bottom:1rem;">
          <p style="font-size:0.85rem; color:var(--text-light); margin-bottom:1rem;">
            Aggregate UPS status from <strong>ESP32/ESP8266 devices</strong> running local HTTP endpoints
            (NUTS — Network UPS Tools-style). Each ESP reports battery level, load %, input/output voltage,
            and runtime remaining.
          </p>
          <div class="setup-steps">
            <p>How it will work:</p>
            <ul>
              <li>Each ESP device exposes a JSON endpoint, e.g. <code>http://192.168.1.x/ups</code></li>
              <li>Add each device's IP and a friendly name below</li>
              <li>The app polls each endpoint on a configurable interval</li>
              <li>UPS status appears on a dedicated tab with battery and load gauges</li>
            </ul>
          </div>

          <div style="margin-bottom:0.75rem;">
            <p style="font-size:0.82rem; font-weight:600; margin-bottom:0.5rem;">ESP Devices</p>
            {% if nuts_devices %}
            {% for d in nuts_devices %}
            <div style="display:flex; gap:8px; align-items:center; margin-bottom:6px; opacity:0.45;">
              <input type="text" value="{{ d.name }}" placeholder="Name"
                     style="width:140px; padding:7px 9px; border-radius:7px; border:1px solid var(--border);
                            background:var(--bg); color:var(--text); font-size:0.85rem; font-family:inherit;">
              <input type="text" value="{{ d.url }}" placeholder="http://192.168.1.x/ups"
                     style="flex:1; padding:7px 9px; border-radius:7px; border:1px solid var(--border);
                            background:var(--bg); color:var(--text); font-size:0.85rem; font-family:inherit;">
            </div>
            {% endfor %}
            {% else %}
            <div style="display:flex; gap:8px; align-items:center; margin-bottom:6px; opacity:0.45;">
              <input type="text" placeholder="Name (e.g. Server Room)"
                     style="width:140px; padding:7px 9px; border-radius:7px; border:1px solid var(--border);
                            background:var(--bg); color:var(--text); font-size:0.85rem; font-family:inherit;">
              <input type="text" placeholder="http://192.168.1.x/ups"
                     style="flex:1; padding:7px 9px; border-radius:7px; border:1px solid var(--border);
                            background:var(--bg); color:var(--text); font-size:0.85rem; font-family:inherit;">
            </div>
            {% endif %}
          </div>
          <p style="font-size:0.73rem; color:var(--stone-400);">
            No cloud required — all polling is local. Devices must be reachable on the same network as this machine.
          </p>
        </div>
      </div><!-- /panel-nuts -->

    </div><!-- /content -->
  </div><!-- /settings-wrap -->
</div>

<script>
function showPanel(id, btn) {
  document.querySelectorAll('.sys-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.sys-link').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + id).classList.add('active');
  if (btn) btn.classList.add('active');
  history.replaceState(null,'','#'+id);
}
// Restore panel from URL hash on load
(function() {
  const hash = location.hash.slice(1);
  if (hash) {
    const panel = document.getElementById('panel-' + hash);
    const btn = document.querySelector('.sys-link[onclick*=\\''+hash+'\\']');
    if (panel) showPanel(hash, btn);
  }
})();

function saveCreds() {
  const email = document.getElementById('credEmail').value.trim();
  const pwd   = document.getElementById('credPwd').value;
  const msg   = document.getElementById('credMsg');
  const btn   = document.getElementById('credBtn');
  const prog  = document.getElementById('credProgress');
  const progBar   = document.getElementById('credProgressBar');
  const progLabel = document.getElementById('credProgressLabel');

  if (!email) {
    msg.textContent = 'Email required';
    msg.style.color = 'var(--red)';
    msg.style.display = 'inline';
    return;
  }

  // Disable button while working
  btn.disabled = true;
  btn.style.opacity = '0.6';
  msg.style.display = 'none';
  prog.style.display = 'block';
  progLabel.textContent = 'Saving credentials…';
  progBar.style.width = '15%';

  // Step 1 — save credentials
  fetch('/api/settings/credentials', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email, password: pwd})
  })
  .then(r => r.json())
  .then(d => {
    if (!d.ok) throw new Error(d.error || 'Failed to save credentials');

    progLabel.textContent = 'Credentials saved — signalling poller…';
    progBar.style.width = '35%';

    // Step 2 — trigger reconnect (passes credentials so poller gets them immediately)
    return fetch('/api/poller-reconnect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password: pwd})
    }).then(r => r.json());
  })
  .then(d => {
    if (!d.ok) throw new Error(d.error || 'Reconnect request failed');

    progLabel.textContent = 'Poller reconnecting…';
    progBar.style.width = '55%';
    progBar.style.background = 'var(--olive-500)';

    // Step 3 — poll /api/poller-status until live (up to ~75s)
    let attempts = 0;
    const maxAttempts = 15;
    function checkStatus() {
      fetch('/api/poller-status').then(r => r.json()).then(s => {
        attempts++;
        const pct = 55 + Math.round((attempts / maxAttempts) * 40);
        progBar.style.width = pct + '%';

        if (s.ok && s.poller_running && s.consecutive_errors === 0) {
          // Connected!
          progBar.style.width = '100%';
          progBar.style.background = 'var(--green)';
          progLabel.style.color = 'var(--green)';
          progLabel.textContent = '✓ Connected — poller is live';
          msg.textContent = '✓ Authenticated and polling';
          msg.style.color = 'var(--green)';
          msg.style.display = 'inline';
          btn.disabled = false;
          btn.style.opacity = '1';
          setTimeout(() => { prog.style.display = 'none'; progBar.style.width = '0%'; progBar.style.background = 'var(--olive-600)'; progLabel.style.color = ''; }, 4000);
        } else if (s.ok === false && s.consecutive_errors > 0) {
          // Poller responded but has errors
          throw new Error(s.error || 'Poller reported errors after reconnect');
        } else if (attempts >= maxAttempts) {
          throw new Error('Timed out waiting for poller — check /log for status');
        } else {
          // Still waiting — try again in 5s
          setTimeout(checkStatus, 5000);
        }
      }).catch(err => {
        progBar.style.background = 'var(--red)';
        progLabel.style.color = 'var(--red)';
        progLabel.textContent = '✗ ' + err.message;
        msg.textContent = '✗ ' + err.message;
        msg.style.color = 'var(--red)';
        msg.style.display = 'inline';
        btn.disabled = false;
        btn.style.opacity = '1';
      });
    }
    setTimeout(checkStatus, 5000); // give poller ~5s to pick up the flag
  })
  .catch(err => {
    prog.style.display = 'none';
    msg.textContent = '✗ ' + err.message;
    msg.style.color = 'var(--red)';
    msg.style.display = 'inline';
    btn.disabled = false;
    btn.style.opacity = '1';
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
  }).then(r=>r.json()).then(() => {
    const m = document.getElementById('lblMsg');
    m.style.display='inline'; setTimeout(()=>m.style.display='none', 2500);
  });
}
function savePanelDisplay() {
  const invertLeft  = document.getElementById('invertLeft').checked;
  const invertRight = document.getElementById('invertRight').checked;
  fetch('/api/settings/panel-display', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({invert_left: invertLeft, invert_right: invertRight})
  }).then(r=>r.json()).then(() => {
    const m = document.getElementById('pdMsg');
    m.style.display='inline'; setTimeout(()=>m.style.display='none', 4000);
  });
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
            "poles":        row.get("poles") or 1,
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
            s.get("note"), s.get("amps"), s.get("poles", 1)
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
    aq   = cfg.get("aqara", {})
    pinv = cfg.get("panel_display", {})
    kasa = cfg.get("kasa", {})
    nuts = cfg.get("nuts", {})
    return _render(SETTINGS_HTML, active_page="settings",
                   saved_email=cfg.get("emporia_email", ""),
                   has_tokens=has_tokens,
                   rate_cents=energy.RATE_CENTS,
                   monthly_budget=MONTHLY_BUDGET,
                   known_devices=energy.get_known_devices(),
                   aqara_app_id=aq.get("app_id", ""),
                   aqara_key_id=aq.get("key_id", ""),
                   panel_invert_left=pinv.get("invert_left", False),
                   panel_invert_right=pinv.get("invert_right", False),
                   kasa_host=kasa.get("host", ""),
                   nuts_devices=nuts.get("devices", []),
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


@app.route("/api/settings/panel-display", methods=["POST"])
def api_save_panel_display():
    import json as _json
    data = request.get_json(force=True)
    cfg = {}
    try:
        with open("settings.json") as f:
            cfg = _json.load(f)
    except Exception:
        pass
    cfg["panel_display"] = {
        "invert_left":  bool(data.get("invert_left",  False)),
        "invert_right": bool(data.get("invert_right", False)),
    }
    with open("settings.json", "w") as f:
        _json.dump(cfg, f, indent=2)
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
