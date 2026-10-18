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
export function placeTemperatureLabels(items, width, height, house = null) {
  if (house) return placeOutsideHouse(items, width, height, house);
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

function placeOutsideHouse(items, width, height, house) {
  const placed = [], gap = 12;
  const collision = a => placed.some(b => a.x < b.x+b.w+6 && a.x+a.w+6 > b.x && a.y < b.y+b.h+6 && a.y+a.h+6 > b.y);
  const outside = a => a.x+a.w <= house.left-gap || a.x >= house.right+gap || a.y+a.h <= house.top-gap || a.y >= house.bottom+gap;
  for (const item of [...items].sort((a,b)=>(b.priority||0)-(a.priority||0) || a.y-b.y)) {
    const options = [];
    const left = house.left-item.w-gap, right = house.right+gap;
    for (const x of [left,right]) {
      if (x<6 || x+item.w>width-6) continue;
      for(let step=0;step<12;step++)for(const sign of step?[1,-1]:[1]){
        const y=Math.max(6,Math.min(height-item.h-6,item.y-item.h/2+sign*step*(item.h+8)));
        const candidate={...item,x,y};
        if(outside(candidate)&&!collision(candidate))options.push(candidate);
      }
    }
    for(const y of [house.top-item.h-gap,house.bottom+gap]){
      if(y<6 || y+item.h>height-6)continue;
      for(let step=0;step<12;step++)for(const sign of step?[1,-1]:[1]){
        const x=Math.max(6,Math.min(width-item.w-6,item.x-item.w/2+sign*step*(item.w+8)));
        const candidate={...item,x,y};
        if(outside(candidate)&&!collision(candidate))options.push(candidate);
      }
    }
    options.sort((a,b)=>Math.hypot(a.x+a.w/2-item.x,a.y+a.h/2-item.y)-Math.hypot(b.x+b.w/2-item.x,b.y+b.h/2-item.y));
    if(options.length)placed.push(options[0]);
  }
  return placed;
}
