// The dropdown and Home 3D deliberately share the same scene builder.
import { createView } from './view.js';
let view, signature, state, selected = 'all';
const canvas = document.querySelector('canvas'), labels = document.querySelector('#labels');
const picker = document.querySelector('select');
const format = value => Number.isFinite(value) ? `${value.toFixed(1)} °F` : 'No recent reading';
function update() {
  const sensors = (state.temperature_sensors || []).map(row => ({...row,
    stale: !state.cache_fresh || row.stale,
    metrics: {...row.metrics, temperature_f: !state.cache_fresh || row.stale ? null : row.metrics?.temperature_f}}));
  const rooms = state.house_preview.rooms.map(room => {
    const readings = sensors.filter(row => row.placement?.room_id === room.id && Number.isFinite(row.metrics?.temperature_f));
    return {...room, temperature_f: readings.length ? readings.reduce((sum,row)=>sum+row.metrics.temperature_f,0)/readings.length : null};
  });
  view.update(rooms, 'temperature_f', format, sensors);
  const list = document.querySelector('#rooms'); list.replaceChildren();
  for (const room of rooms.filter(room => selected === 'all' || (room.level || state.house_preview.levels[0].id) === selected)) {
    const row = document.createElement('div');
    const name = document.createElement('span'); name.textContent = room.name;
    const reading = document.createElement('strong'); reading.textContent = format(room.temperature_f);
    row.append(name,reading); list.append(row);
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
      view = createView(canvas, labels, plan, room => window.webkit?.messageHandlers.history.postMessage(room), {floorOpacity: Number(document.querySelector('#opacity').value) / 100});
      view.setLevel(selected); signature = next;
    }
    update();
    document.querySelector('#status').textContent = 'Drag to rotate · scroll to zoom · click a room for sensor history';
    window.nativeHouseReady = true;
  } catch (error) {
    document.querySelector('#status').textContent = `3D unavailable: ${error.message}. Use Floor plan.`;
    window.nativeHouseError = error.message;
  }
};
picker.addEventListener('change', () => { selected=picker.value; view?.setLevel(selected); update(); });
document.querySelector('button').addEventListener('click', () => view?.reset());

document.querySelector('#opacity').addEventListener('input', event => view?.setFloorOpacity(Number(event.target.value) / 100));
