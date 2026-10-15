const W=960, H=270, LEFT=64, RIGHT=20, TOP=18, BOTTOM=48;
const CHANNELS=['Main','Mains_A','Mains_B'];

export function segments(values) {
  const result=[];
  let current=[];
  for(let index=0;index<values.length;index++) {
    if(values[index]==null) {
      if(current.length) result.push(current);
      current=[];
    } else current.push([index,values[index]]);
  }
  if(current.length) result.push(current);
  return result;
}

export function geometry(history) {
  if(history.interval_seconds!==60 || !Number.isFinite(Date.parse(history.start))
    || Date.parse(history.end)-Date.parse(history.start)!==86400000
    || history.series?.length!==3) throw new Error('Invalid mains chart window');
  const all=[];
  history.series.forEach((row,index)=>{
    if(row.channel_name!==CHANNELS[index] || row.values.length!==1440
      || row.values.some(value=>value!==null && !Number.isFinite(value))) throw new Error('Invalid mains chart values');
    all.push(...row.values.filter(value=>value!==null));
  });
  const min=Math.min(0,...all), max=Math.max(1,...all);
  const x=index=>LEFT+index/1440*(W-LEFT-RIGHT);
  const y=value=>TOP+(max-value)/(max-min)*(H-TOP-BOTTOM);
  return {min,max,x,y,empty:!all.length,paths:history.series.map(row=>segments(row.values)
    .map(points=>points.map(([index,value],n)=>`${n?'L':'M'}${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(' ')
      +(points.length===1?' l0.01,0':'')).join(' '))};
}

export function renderMainsChart(root,history) {
  root.hidden=!history;
  if(!history) return;
  root.replaceChildren();
  const text=(tag,value)=>{const node=document.createElement(tag);node.textContent=value;return node;};
  let g;
  try {g=geometry(history);} catch {
    root.append(text('p','Mains history chart unavailable: invalid recorded history.'));return;
  }
  root.append(text('h3','Mains history · last 24 hours'),text('p',
    'Measured one-minute average power, not stacked. Blank intervals are missing data, not zero. Times are local.'));
  const legend=text('div','');legend.className='home3d-mains-legend';
  history.series.forEach(row=>{
    const item=text('span',row.name);item.className=`mains-line-${row.channel_name}`;legend.append(item);
  });
  root.append(legend);
  if(g.empty) {root.append(text('p','No recorded mains intervals in this 24-hour window.'));return;}
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  svg.setAttribute('role','img');
  svg.setAttribute('aria-label','24-hour Total, Leg A and Leg B power history. Missing minutes break each line. Use the minute slider for exact values.');
  const add=(tag,attrs,value)=>{
    const node=document.createElementNS(svg.namespaceURI,tag);
    for(const [key,val] of Object.entries(attrs)) node.setAttribute(key,val);
    if(value!=null) node.textContent=value;
    svg.append(node);return node;
  };
  for(let n=0;n<=4;n++) {
    const value=g.min+(g.max-g.min)*n/4, y=g.y(value);
    add('line',{x1:LEFT,x2:W-RIGHT,y1:y,y2:y,class:'mains-grid'});
    add('text',{x:LEFT-8,y:y+4,'text-anchor':'end'},`${Math.round(value).toLocaleString()} W`);
  }
  const start=Date.parse(history.start), time=index=>new Date(start+index*60000);
  for(let n=0;n<=4;n++) add('text',{
    x:g.x(n*360),y:H-24,'text-anchor':n===0?'start':n===4?'end':'middle',
  },time(n*360).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}));
  history.series.forEach((row,index)=>add('path',{
    d:g.paths[index],class:`mains-line mains-line-${row.channel_name}`,
    'data-channel':row.channel_name,'data-segments':segments(row.values).length,
  }));
  root.append(svg);
  const label=text('label','Inspect minute '), slider=document.createElement('input');
  slider.type='range';slider.min='0';slider.max='1439';slider.step='1';
  slider.value=root.dataset.minute || '1439';slider.setAttribute('aria-label','Mains history minute');
  label.append(slider);root.append(label);
  const readout=text('p','');readout.className='home3d-mains-readout';root.append(readout);
  const inspect=index=>{
    root.dataset.minute=String(index);slider.value=String(index);
    const stamp=time(index).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
    readout.textContent=`${stamp} · `+history.series.map(row=>
      `${row.name}: ${row.values[index]===null?'Missing':`${Math.round(row.values[index]).toLocaleString()} W`}`).join(' · ');
    slider.setAttribute('aria-valuetext',readout.textContent);
  };
  slider.addEventListener('input',()=>inspect(Number(slider.value)));
  svg.addEventListener('pointermove',event=>{
    const rect=svg.getBoundingClientRect(), x=(event.clientX-rect.left)/rect.width*W;
    inspect(Math.max(0,Math.min(1439,Math.round((x-LEFT)/(W-LEFT-RIGHT)*1440))));
  });
  inspect(Number(slider.value));
  root.append(text('small',`Window: ${time(0).toLocaleString()} – ${new Date(history.end).toLocaleString()}. Recorded history remains visible if the live feed disconnects.`));
}
