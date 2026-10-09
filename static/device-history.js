/* Recorded device comparisons: bounded buckets, stable legends and explicit gaps. */
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const hours = {'4h': 4, '24h': 24, '7d': 168, '14d': 336, '30d': 720};
  const formatTime = value => new Date(value).toLocaleString([], {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function svgNode(tag, attributes, text) {
    const element = document.createElementNS(NS, tag);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    if (text !== undefined) element.textContent = text;
    return element;
  }
  document.querySelectorAll('[data-history-kind]').forEach(root => {
    const find = name => root.querySelector(`[data-history-${name}]`);
    const windowSelect = find('window'), metricSelect = find('metric'), unitSelect = find('unit');
    let data = null, selected = null, ending = null, controller = null, sequence = 0;
    function color(series) {
      return getComputedStyle(document.documentElement).getPropertyValue(
        `--history-color-${series.color_index + 1}`).trim();
    }
    function metric() { return data.metrics.find(item => item.id === metricSelect.value); }
    function unit() {
      return metric().unit === 'temperature' ? `°${unitSelect.value}` : metric().unit;
    }
    function value(point) {
      const raw = point[metricSelect.value];
      if (typeof raw !== 'number' || !Number.isFinite(raw)) return null;
      return metric().unit === 'temperature' && unitSelect.value === 'F' ? raw * 1.8 + 32 : raw;
    }
    function localInput(date) {
      const pad = number => String(number).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    function legend() {
      const fragment = document.createDocumentFragment();
      for (const series of data.series) {
        const label = node('label', undefined, 'history-legend-item');
        const checkbox = node('input'); checkbox.type = 'checkbox';
        checkbox.checked = selected.has(series.id);
        checkbox.addEventListener('change', () => {
          if (checkbox.checked) selected.add(series.id); else selected.delete(series.id);
          render();
        });
        const swatch = node('span', undefined, 'history-swatch');
        swatch.style.backgroundColor = color(series);
        label.append(checkbox, swatch, node('span', series.name)); fragment.append(label);
      }
      find('legend').replaceChildren(fragment);
    }
    function visible() { return data.series.filter(series => selected.has(series.id)); }
    function render() {
      if (!data) return;
      const series = visible(), all = series.flatMap(item => item.points.map(value)).filter(v => v !== null);
      const chart = find('chart'); chart.replaceChildren();
      chart.setAttribute('aria-label', `${metric().label} comparison, ${unit()}, ${series.map(item => item.name).join(', ')}`);
      chart.append(svgNode('title', {}, `${metric().label} · ${unit()}`));
      const start = Date.parse(data.start), end = Date.parse(data.end);
      const x = stamp => 65 + 865 * (Math.min(end, Math.max(start, stamp)) - start) / (end - start);
      let lower = all.length ? Math.min(...all) : 0, upper = all.length ? Math.max(...all) : 1;
      if (metric().unit.startsWith('%')) { lower = 0; upper = 100; }
      else {
        const padding = Math.max(metric().unit === 'temperature' ? 1 : 0.1, (upper - lower) * 0.1);
        lower -= padding; upper += padding;
        if (metric().unit === 'ms') lower = 0;
      }
      const y = reading => 255 - 230 * (reading - lower) / (upper - lower);
      for (let tick = 0; tick <= 4; tick++) {
        const reading = lower + (upper - lower) * tick / 4;
        chart.append(svgNode('line', {x1: 65, x2: 930, y1: y(reading), y2: y(reading), stroke: 'var(--border)'}));
        chart.append(svgNode('text', {x: 56, y: y(reading) + 4, 'text-anchor': 'end', fill: 'var(--text-light)', 'font-size': 12}, reading.toFixed(1)));
        const stamp = start + (end - start) * tick / 4;
        chart.append(svgNode('text', {x: x(stamp), y: 279, 'text-anchor': tick === 0 ? 'start' : tick === 4 ? 'end' : 'middle', fill: 'var(--text-light)', 'font-size': 11}, formatTime(stamp)));
      }
      for (const item of series) {
        let segment = [], previous = null;
        const flush = () => {
          if (segment.length > 1) chart.append(svgNode('polyline', {
            points: segment.join(' '), fill: 'none', stroke: color(item), 'stroke-width': 2.5,
            'stroke-dasharray': `${12 + item.color_index * 2} 6`,
            'stroke-dashoffset': item.color_index * 5,
          }));
          segment = [];
        };
        for (const point of item.points) {
          const reading = value(point);
          if (reading === null || (previous !== null && point.bucket !== previous + 1)) flush();
          previous = point.bucket;
          if (reading === null) continue;
          const px = x(Date.parse(point.timestamp)), py = y(reading);
          segment.push(`${px},${py}`);
          const dot = svgNode('circle', {cx: px, cy: py, r: 3, fill: color(item)});
          dot.append(svgNode('title', {}, `${item.name} · ${formatTime(point.timestamp)} · ${reading.toFixed(1)} ${unit()} · ${point.valid_samples}/${point.samples} valid observations`));
          chart.append(dot);
        }
        flush();
      }
      find('range').textContent = `${formatTime(data.start)} – ${formatTime(data.end)} · ${data.bucket_seconds / 60}-minute bucket means · your local time`;
      find('status').textContent = all.length ? `${series.length} selected devices · gaps indicate missing observations.` : 'No recorded values for the selected devices and metric in this window.';
      const summaries = document.createDocumentFragment();
      for (const item of series) {
        const card = node('section', undefined, 'history-device-summary');
        card.style.borderTopColor = color(item);
        card.append(node('h4', item.name));
        const summary = item.summary, coverage = 100 * summary.covered_seconds / ((end - start) / 1000);
        card.append(node('p', `${(summary.enabled_seconds / 3600).toFixed(2)} h enabled estimate`, 'history-summary-value'));
        card.append(node('p', `${summary.valid_samples}/${summary.samples} valid observations · ${coverage.toFixed(1)}% enabled-state interval coverage`, 'card-meta'));
        if (summary.last_observation) card.append(node('p', `Last recorded: ${formatTime(summary.last_observation)}`, 'card-meta'));
        const modes = Object.entries(summary.modes || {});
        if (modes.length) card.append(node('p', modes.map(([mode, count]) => `${mode}: ${count} observations`).join(' · '), 'card-meta'));
        summaries.append(card);
      }
      find('summaries').replaceChildren(summaries);
      find('value-heading').textContent = `${metric().label} (${unit()})`;
      const rows = document.createDocumentFragment();
      let count = 0;
      for (const item of series) for (const point of item.points) {
        if (count++ >= 200) continue;
        const row = node('tr'), reading = value(point);
        [formatTime(point.timestamp), item.name, reading === null ? 'Unknown' : reading.toFixed(2), `${point.valid_samples}/${point.samples}`].forEach(text => row.append(node('td', text)));
        rows.append(row);
      }
      find('table').replaceChildren(rows);
    }
    async function load() {
      if (controller) controller.abort();
      controller = new AbortController(); const active = ++sequence;
      find('status').textContent = 'Loading recorded history…';
      root.setAttribute('aria-busy', 'true');
      const query = new URLSearchParams({window: windowSelect.value});
      if (ending) query.set('end', ending);
      try {
        const response = await fetch(`/api/${root.dataset.historyKind}/history?${query}`, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw Error('Recorded history is unavailable for this window.');
        const incoming = await response.json(); if (active !== sequence) return;
        data = incoming;
        if (selected === null) selected = new Set(data.series.map(item => item.id));
        const priorMetric = metricSelect.value;
        metricSelect.replaceChildren(...data.metrics.map(item => {
          const option = node('option', item.label); option.value = item.id; return option;
        }));
        if (data.metrics.some(item => item.id === priorMetric)) metricSelect.value = priorMetric;
        find('end').value = localInput(new Date(data.end));
        legend(); render();
      } catch (error) {
        if (error.name === 'AbortError' || active !== sequence) return;
        data = null; find('chart').replaceChildren(); find('table').replaceChildren(); find('summaries').replaceChildren();
        find('status').textContent = 'Could not load recorded history. Refresh to retry.';
      } finally { if (active === sequence) root.removeAttribute('aria-busy'); }
    }
    windowSelect.addEventListener('change', load);
    metricSelect.addEventListener('change', render);
    if (unitSelect) unitSelect.addEventListener('change', render);
    find('previous').addEventListener('click', () => {
      ending = new Date(Date.parse(ending || (data && data.end) || new Date().toISOString()) - hours[windowSelect.value] * 3600000).toISOString(); load();
    });
    find('latest').addEventListener('click', () => { ending = null; load(); });
    find('refresh').addEventListener('click', load);
    find('end').addEventListener('change', event => {
      const stamp = new Date(event.target.value);
      if (!Number.isFinite(stamp.getTime()) || stamp > new Date()) {
        find('status').textContent = 'Choose a valid window end in the past.'; return;
      }
      ending = stamp.toISOString(); load();
    });
    find('export').addEventListener('click', () => {
      if (!data) return;
      const csvCell = text => `"${String(typeof text === 'string' && /^[=+\-@]/.test(text) ? "'" + text : text).replaceAll('"', '""')}"`;
      const rows = [['Bucket start UTC', 'Device', 'Device ID', `${metric().label} (${unit()})`, 'Valid observations', 'Total observations']];
      for (const item of visible()) for (const point of item.points) rows.push([
        point.timestamp, item.name, item.id, value(point) === null ? '' : value(point), point.valid_samples, point.samples,
      ]);
      const url = URL.createObjectURL(new Blob(['\ufeff' + rows.map(row => row.map(csvCell).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'}));
      const anchor = node('a'); anchor.href = url; anchor.download = `${root.dataset.historyKind}-${data.window}-history.csv`;
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    load();
    const timer = setInterval(() => { if (!document.hidden && ending === null) load(); }, 60000);
    window.addEventListener('pagehide', () => { clearInterval(timer); if (controller) controller.abort(); }, {once: true});
  });
})();
