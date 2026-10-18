// Presentation rules shared by native scene badges and its complete sensor list.
export function readingState(row, cacheFresh) {
  const value = row.metrics?.temperature_f;
  const known = Number.isFinite(value);
  const status = !known ? 'missing' : !cacheFresh || row.stale ? 'stale' : 'recent';
  const band = status !== 'recent' ? 'unknown' : value < 70 ? 'cold' : value <= 75 ? 'comfortable' : value < 78 ? 'warm' : 'hot';
  return {value: known ? value : null, status, band};
}
export function temperatureSummary(sensors, cacheFresh) {
  const rows = sensors.map(row => readingState(row, cacheFresh));
  const recent = rows.filter(row => row.status === 'recent').map(row => row.value);
  return {recent: recent.length, stale: rows.filter(row => row.status === 'stale').length,
    missing: rows.filter(row => row.status === 'missing').length,
    min: recent.length ? Math.min(...recent) : null, max: recent.length ? Math.max(...recent) : null};
}
// Bounded, deterministic callouts; lower-priority overflow remains in the sensor list.
export function placeTemperatureLabels(items, width, height) {
  const placed = [];
  for (const item of [...items].sort((a,b) => (b.priority || 0) - (a.priority || 0))) {
    if (item.w > width - 12 || item.h > height - 12) continue;
    let best;
    for (let ring=0;ring<10 && !best;ring++) for(let side=0;side<(ring ? 12 : 1);side++) {
      const angle=side*Math.PI/6;
      const x=Math.max(6,Math.min(width-item.w-6,item.x-item.w/2+Math.cos(angle)*ring*20));
      const y=Math.max(6,Math.min(height-item.h-6,item.y-item.h-14+Math.sin(angle)*ring*18));
      if(!placed.some(p=>x<p.x+p.w+8 && x+item.w+8>p.x && y<p.y+p.h+8 && y+item.h+8>p.y)) {
        best={...item,x,y}; break;
      }
    }
    if(best) placed.push(best);
  }
  return placed;
}
