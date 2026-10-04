(() => {
  const dialog = document.getElementById('circuit-history');
  if (!dialog || location.pathname.startsWith('/circuit/')) return;
  const byId = id => document.getElementById(id);
  const status = byId('ch-status');
  const content = byId('ch-content');
  let enabled = localStorage.getItem('circuitPreview') !== 'off';
  let circuit = null, payload = null, selectedDays = 1, controller = null, opener = null;
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'ch-toggle';
  toggle.textContent = 'Circuit quick view';
  const toolbar = document.querySelector('.circuit-toolbar') || document.querySelector('.page .section-head');
  function updateToggle() {
    toggle.setAttribute('aria-pressed', String(enabled));
    toggle.title = enabled ? 'Circuit links open a preview; turn off for full pages' : 'Circuit links open full pages; turn on for previews';
  }
  updateToggle();
  if (toolbar) toolbar.append(toggle);
  toggle.addEventListener('click', () => {
    enabled = !enabled;
    localStorage.setItem('circuitPreview', enabled ? 'on' : 'off');
    updateToggle();
  });
  function close() { controller?.abort(); dialog.close(); }
  byId('ch-close').addEventListener('click', close);
  dialog.addEventListener('close', () => {
    controller?.abort();
    const target = opener?.isConnected ? opener : [...document.querySelectorAll('a[href]')].find(link => link.href === opener?.href);
    (target || toggle).focus();
  });
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) close();
  });
  function formatUsage(value) { return value == null ? 'No data' : `${Number(value).toFixed(3)} kWh`; }
  function drawChart(series) {
    const chart = byId('ch-chart');
    chart.replaceChildren();
    const data = byId('ch-data');
    data.replaceChildren();
    const maximum = Math.max(...series.map(row => row.total_kwh || 0), 0.001);
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 720 170');
    svg.setAttribute('aria-hidden', 'true');
    const width = 680 / Math.max(series.length, 1);
    series.forEach((row, index) => {
      const rect = document.createElementNS(ns, 'rect');
      const height = row.total_kwh == null ? 3 : Math.max(2, row.total_kwh / maximum * 120);
      rect.setAttribute('x', String(20 + index * width));
      rect.setAttribute('y', String(130 - height));
      rect.setAttribute('width', String(Math.max(1, width - 3)));
      rect.setAttribute('height', String(height));
      rect.setAttribute('class', row.total_kwh == null ? 'ch-gap' : 'ch-bar');
      const title = document.createElementNS(ns, 'title');
      title.textContent = `${row.period}${row.partial_bucket ? " (partial period)" : ""}: ${formatUsage(row.total_kwh)}`;
      rect.append(title); svg.append(rect);
      const tr = document.createElement('tr');
      for (const text of [row.period, row.total_kwh == null ? 'No recorded data' : Number(row.total_kwh).toFixed(3)]) {
        const td = document.createElement('td'); td.textContent = text; tr.append(td);
      }
      data.append(tr);
    });
    for (const [index, anchor] of [[0, 'start'], [series.length - 1, 'end']]) {
      const label = document.createElementNS(ns, 'text');
      label.setAttribute('x', anchor === 'start' ? '20' : '700');
      label.setAttribute('y', '157'); label.setAttribute('text-anchor', anchor);
      label.textContent = series[index]?.period || ''; svg.append(label);
    }
    chart.append(svg);
    chart.setAttribute('aria-label', `Recorded energy by ${selectedDays === 1 ? 'hour' : 'day'}; ${series.filter(row => row.total_kwh == null).length} periods without data. Chart values are available below.`);
  }
  function render() {
    if (!payload) return;
    const window = payload.windows.find(row => row.days === selectedDays);
    byId('ch-usage').textContent = formatUsage(window.total_kwh);
    byId('ch-cost').textContent = window.total_cents == null ? 'No data' : `$${(window.total_cents / 100).toFixed(2)}`;
    byId('ch-trend').textContent = window.change_pct == null ? 'Collecting history' : `${window.change_pct > 0 ? '↑' : window.change_pct < 0 ? '↓' : '→'} ${Math.abs(window.change_pct).toFixed(1)}%`;
    byId('ch-history-note').textContent = `${window.readings.toLocaleString()} readings in the last ${selectedDays} ${selectedDays === 1 ? 'day' : 'days'}. ${window.change_pct == null ? 'A trend needs sufficiently sampled current and previous periods.' : `Compared with the previous ${selectedDays}-day period.`}`;
    byId('ch-power').textContent = payload.live_watts == null ? 'Live power unavailable' : `${Math.round(payload.live_watts).toLocaleString()} W · minute average`;
    byId('ch-periods').querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.days) === selectedDays)));
    drawChart(window.series);
  }
  async function load() {
    controller?.abort(); controller = new AbortController();
    const current = controller;
    payload = null;
    status.textContent = 'Loading history…'; content.hidden = true;
    try {
      const response = await fetch(`/api/circuit-history/${encodeURIComponent(circuit)}`, {signal:current.signal});
      if (!response.ok) throw new Error('History could not be loaded. Use Refresh to try again.');
      const result = await response.json();
      if (current !== controller || !dialog.open) return;
      payload = result; render(); content.hidden = false;
      status.textContent = result.last_reading ? `Last recorded: ${result.last_reading.replace('T', ' ').slice(0, 19)}` : 'No history recorded for this circuit yet.';
    } catch (error) {
      if (error.name === 'AbortError' || current !== controller) return;
      status.textContent = error.message;
      // Keep a retry action available when loading fails.
      content.hidden = false;
      byId('ch-chart').replaceChildren(); byId('ch-data').replaceChildren();
      ['ch-usage','ch-cost','ch-trend','ch-power','ch-history-note'].forEach(id => byId(id).textContent = '');
    }
  }
  byId('ch-periods').addEventListener('click', event => {
    const button = event.target.closest('button[data-days]');
    if (button) { selectedDays = Number(button.dataset.days); render(); }
  });
  byId('ch-refresh').addEventListener('click', load);
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!enabled || !link || link.id === 'ch-full-page' || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/circuit/')) return;
    event.preventDefault(); opener = link;
    // The source links encode a whole channel name, including embedded slashes.
    circuit = decodeURIComponent(url.pathname.slice('/circuit/'.length));
    payload = null; selectedDays = 1;
    byId('circuit-history-title').textContent = link.classList.contains('breaker') ? link.querySelector('.breaker-name')?.textContent || circuit : link.textContent.trim() || circuit;
    byId('ch-full-page').href = link.href;
    if (!dialog.open) dialog.showModal();
    load();
  });
})();
