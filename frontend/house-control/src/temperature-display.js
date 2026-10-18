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

export function temperatureSparkline(values, times, width=100, height=24) {
  const rows=values.map((value,i)=>({value,time:Date.parse(times[i])}));
  const valid=rows.filter(row=>Number.isFinite(row.value)&&Number.isFinite(row.time));
  if(!valid.length)return {paths:[],dots:[],min:null,max:null};
  const min=Math.min(...valid.map(row=>row.value)),max=Math.max(...valid.map(row=>row.value));
  const start=Date.parse(times[0]),end=Date.parse(times.at(-1));
  const low=min-1,span=max-min+2;
  const x=time=>2+(width-4)*(time-start)/Math.max(1,end-start),y=value=>height-2-(height-4)*(value-low)/span;
  const point=(time,value)=>`${x(time).toFixed(2)},${y(value).toFixed(2)}`;
  const band=value=>value<68?'cold':value>78?'hot':'normal';
  const groups=new Map(),dots=[];
  for(let i=0;i<rows.length;i++){
    const a=rows[i],b=rows[i+1];if(!Number.isFinite(a.value)||!Number.isFinite(a.time))continue;
    if(!b||!Number.isFinite(b.value)||!Number.isFinite(b.time)){
      if(!i||!Number.isFinite(rows[i-1].value))dots.push({x:x(a.time),y:y(a.value),band:band(a.value)});
      continue;
    }
    const fractions=[0,1,...[68,78].filter(mark=>mark>Math.min(a.value,b.value)&&mark<Math.max(a.value,b.value)).map(mark=>(mark-a.value)/(b.value-a.value))].sort((a,b)=>a-b);
    for(let j=1;j<fractions.length;j++){
      const f=fractions[j-1],g=fractions[j],value=(a.value+(b.value-a.value)*(f+g)/2),key=band(value);
      const path=`M${point(a.time+(b.time-a.time)*f,a.value+(b.value-a.value)*f)}L${point(a.time+(b.time-a.time)*g,a.value+(b.value-a.value)*g)}`;
      groups.set(key,(groups.get(key)||'')+path);
    }
  }
  return {paths:[...groups].map(([band,path])=>({band,path})),dots,min,max};
}

export function appendTemperatureTrend(node, history, row, width=100) {
  const series=history?.series?.find(series=>series.source===row.source&&series.sensor_id===row.sensor_id);
  const chart=temperatureSparkline(series?.values || [],history?.times || [],width,24);
  const wrap=document.createElement('span');wrap.className='temperature-trend';
  if(chart.min==null){wrap.textContent='No 24h observations';node.append(wrap);return;}
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`0 0 ${width} 24`);svg.setAttribute('width',width);svg.setAttribute('height',24);svg.setAttribute('aria-hidden','true');
  for(const {band,path} of chart.paths){const line=document.createElementNS(svg.namespaceURI,'path');line.setAttribute('d',path);line.setAttribute('class',band);svg.append(line);}
  for(const dot of chart.dots){const circle=document.createElementNS(svg.namespaceURI,'circle');circle.setAttribute('cx',dot.x);circle.setAttribute('cy',dot.y);circle.setAttribute('r',2);circle.setAttribute('class',dot.band);svg.append(circle);}
  const caption=document.createElement('small');
  const cached=Date.now()/1000-(history.fetched_at || 0)>600;
  caption.textContent=`24h${cached?' · cached':''} · ${chart.min.toFixed(1)}–${chart.max.toFixed(1)}°F`;
  wrap.append(svg,caption);wrap.title='Recorded online collector observations; gaps are unknown';node.append(wrap);
}
