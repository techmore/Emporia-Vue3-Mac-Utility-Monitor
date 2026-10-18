// The dropdown and Home 3D deliberately share the same scene builder.
import { createView } from './view.js';
import { readingState, temperatureSummary, appendTemperatureTrend } from './temperature-display.js';
let view, signature, state, selected = 'all';
const canvas = document.querySelector('canvas'), labels = document.querySelector('#labels');
const picker = document.querySelector('select');
const format = value => Number.isFinite(value) ? `${value.toFixed(1)} °F` : 'No recent reading';
function update() {
  const sensors = (state.temperature_sensors || []).map(row => ({...row,
    display: readingState(row, state.cache_fresh),
    stale: !state.cache_fresh || row.stale,
    metrics: {...row.metrics, temperature_f: !state.cache_fresh || row.stale ? null : row.metrics?.temperature_f}}));
  const rooms = state.house_preview.rooms.map(room => {
    const readings = sensors.filter(row => row.placement?.room_id === room.id && Number.isFinite(row.metrics?.temperature_f));
    return {...room, temperature_f: readings.length ? readings.reduce((sum,row)=>sum+row.metrics.temperature_f,0)/readings.length : null};
  });
  view.update(rooms, 'temperature_f', format, sensors, state.temperature_history);
  const visible=(state.temperature_sensors || []).filter(row=>selected==='all' || row.level===selected);
  const summary=temperatureSummary(visible,state.cache_fresh);
  document.querySelector('#overview').textContent=summary.recent ?
    `${summary.min.toFixed(1)}–${summary.max.toFixed(1)} °F · recent sensor range` : 'No recent temperature readings';
  document.querySelector('#counts').textContent=`${summary.recent} recent · ${summary.stale} last recorded · ${summary.missing} missing${visible.some(row=>!row.placement)?` · ${visible.filter(row=>!row.placement).length} unplaced`:""}`;
  document.querySelector('#sensor-count').textContent=`All ${visible.length} sensors · readings & history`;
  const list = document.querySelector('#rooms'); list.replaceChildren();
  for (const row of [...visible].sort((a,b)=>{
    const x=readingState(a,state.cache_fresh),y=readingState(b,state.cache_fresh);
    return (x.status==='recent'?0:1)-(y.status==='recent'?0:1) || (y.value ?? -Infinity)-(x.value ?? -Infinity) || a.name.localeCompare(b.name);
  })) {
    const display=readingState(row,state.cache_fresh);
    const button=document.createElement('button');button.className='sensor-row';button.dataset.band=display.band;
    button.dataset.ice=String(display.status==='recent'&&display.value<68);button.dataset.heat=String(display.status==='recent'&&display.value>78);
    const names=document.createElement('span');names.className='sensor-names';
    const name=document.createElement('b');name.textContent=row.room_name || row.name;
    const detail=document.createElement('small');detail.textContent=(row.placement?'':'Unplaced · ')+row.name;
    names.append(name,detail);appendTemperatureTrend(names,state.temperature_history,row,140);
    const reading=document.createElement('span');reading.className='sensor-reading';
    const value=document.createElement('strong');value.textContent=format(display.value);
    const status=document.createElement('small');status.textContent=display.status==='recent'?'Recent observation':display.status==='stale'?'Last recorded · not current':'No recorded temperature';
    reading.append(value,status);button.append(names,reading);button.title=row.timestamp || 'Time unknown';
    button.addEventListener('click',()=>openSensor(row));list.append(button);
  }

}
window.renderHouse = data => {
  state = data;
  try {
    const plan = {...data.house_preview};
    plan.wallHeight ??= 2.6; plan.interiorWallHeight ??= 1.25;
    plan.camera = {...plan.camera, elevation: Math.min(75, (plan.camera?.elevation ?? 47) + 12)};
    plan.rooms = plan.rooms.map(room => ({...room, floor: room.floor || 'oak'}));
    plan.stairs = (plan.stairs || []).map(stair => ({...stair, steps: stair.steps || 16}));
    const next = JSON.stringify(plan);
    if (next !== signature) {
      view?.dispose();
      picker.replaceChildren();
      for (const level of [{id:'all',name:'Whole home'}, ...plan.levels]) {
        const option = document.createElement('option'); option.value=level.id; option.textContent=level.name; picker.append(option);
      }
      if (![...picker.options].some(option => option.value === selected)) selected = 'all';
      picker.value = selected;
      view = createView(canvas, labels, plan, room => window.webkit?.messageHandlers.history.postMessage(room), {floorOpacity: Number(document.querySelector('#opacity').value) / 100, gableAttic:true, compactTemperatures:true, outsideTemperatureLabels:true, onSensorPick:openSensor});
      view.setLevel(selected); signature = next;
    }
    update();
    document.querySelector('#status').textContent = 'Drag to rotate · scroll to zoom · select a temperature for its sensor history';
    window.nativeHouseReady = true;
  } catch (error) {
    document.querySelector('#status').textContent = `3D unavailable: ${error.message}. Use Floor plan.`;
    window.nativeHouseError = error.message;
  }
};
picker.addEventListener('change', () => { selected=picker.value; view?.setLevel(selected); update(); });
document.querySelector('button').addEventListener('click', () => view?.reset());

document.querySelector('#opacity').addEventListener('input', event => view?.setFloorOpacity(Number(event.target.value) / 100));

function openSensor(row) { if(row) window.webkit?.messageHandlers.sensor.postMessage(JSON.stringify({source:row.source,sensor_id:row.sensor_id})); }
