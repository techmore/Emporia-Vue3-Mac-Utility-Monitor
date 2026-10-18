import { createView } from './view.js';
import { createEditor } from './editor.js';
import { retainRoomBindings } from './plan-edit.js';
import { sensorReading } from './sensors.js';
import { renderMainsChart } from './mains-chart.js';
import { levelsFor, visibleRoomIds, levelName } from './levels.js';
import { roomTemperatures, historicalSensors, temperatureBand } from './temperatures.js';

const $ = id => document.getElementById(`home3d-${id}`);
const format = (value, metric) => value == null ? 'Unknown' : metric === 'temperature_f' ? `${value.toFixed(1)} °F` : metric === 'watts'
  ? `${Math.round(value).toLocaleString()} W` : metric === 'kwh_24h'
    ? `${value.toFixed(2)} kWh` : `$${value.toFixed(2)}`;
let config, state, view, timer, dirty = false, busy = false, needsReload = false, signature;
let generation = 0, saving = false;
let selectedLevel = new URLSearchParams(location.search).get('level') || '';
let temperatureHistory=null,temperatureIndex=null,temperatureEnd=null,temperatureRequest=0;
if(new URLSearchParams(location.search).get('metric')==='temperature_f')$('metric').value='temperature_f';
const editorRoot = document.getElementById('home3d-editor');
const editor = createEditor(editorRoot, {
  onSave: async (plan, bindings, sensors) => {
    const ok = await save(plan, retainRoomBindings(collectBindings(), plan), sensors);
    if (!ok) editorRoot.querySelector('[data-editor="status"]').textContent = $('save-status').textContent;
    return ok;
  },
  onDirty: value => { $('save').disabled = value || needsReload || !dirty; },
});

async function api(path, options = {}) {
  const response = await fetch(`/api/house-control/${path}`, {
    cache: 'no-store', signal: AbortSignal.timeout(10000), ...options,
  });
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Collector did not return data. Check your connection or sign in again.');
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Collector request failed (${response.status})`);
  return payload;
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function renderState() {
  const visible=visibleRoomIds(config.plan,selectedLevel);
  const metric = $('metric').value;
  const historical=metric==='temperature_f' && temperatureHistory && temperatureIndex!==null;
  const displayedSensors=historical?historicalSensors(temperatureHistory,temperatureIndex):state.sensors;
  const displayedRooms=roomTemperatures(state.rooms,displayedSensors);
  $('temperature-controls').hidden=metric!=='temperature_f';
  $('temperature-time-label').textContent=historical
    ? `Recorded ${new Date(temperatureHistory.times[temperatureIndex]).toLocaleString()} · ${temperatureHistory.seconds/60}-minute observation means; gaps are unknown`
    : 'Live recorded temperatures · Scroll the timeline to look back';
  $('status').textContent = `${state.online ? 'Collector live' : 'Collector offline or readings stale'} · `
    + `Whole home: ${format(state.current_watts, 'watts')} · Last reading: ${state.last_reading || 'none'}`;
  if(state.sensor_sync) {
    const live=state.sensor_sync.ok && state.online;
    const label=document.getElementById('nav-status-label');
    const dot=document.getElementById('nav-status-dot');
    if(label) label.textContent=live?'SER8 live':'SER8 power stale or unavailable';
    if(dot) dot.className=`status-dot ${live?'live':'stale'}`;
    $('status').textContent = `Local preview · SER8 feed ${state.sensor_sync.ok?'connected':'unavailable; retained values are stale'}`
      + ` · Whole home: ${format(state.current_watts,'watts')} · Power ${state.online?'recent':'stale or unavailable'}`
      + ` · Last reading: ${state.last_reading || 'none'} · Last refresh: ${state.sensor_sync.last_success || 'none'}`;
  }
  const mains=$('mains');mains.replaceChildren();
  renderMainsChart($('mains-history'),state.mains_history);
  $('mains-section').hidden=metric==='temperature_f' || !state.mains?.length;
  $('mains-history').hidden=metric==='temperature_f';
  $('room-title').textContent=metric==='temperature_f'?'Room temperatures':'Mapped circuit totals';
  $('room-description').textContent=metric==='temperature_f'
    ? 'Only placed room sensors supply temperature colors. Rooms without a reading stay unknown.'
    : 'Whole circuits assigned to a room; not measured room consumption. Shared circuits cannot be split between rooms.';
  $('recording-basis').hidden=metric==='temperature_f';
  for(const row of state.mains || []) {
    const card=node('article','','home3d-sensor');
    card.append(node('strong',row.name),node('span',format(row.watts,'watts')),
      node('small',`${format(row.kwh_24h,'kwh_24h')} · ${format(row.cost_24h,'cost_24h')} recorded / 24h`),
      node('small',`Last value: ${row.timestamp}${row.watts==null?' · live power unavailable':''}`),
      node('small',row.source || 'SER8 recorded data'));
    if(row.observed_minutes!=null) card.append(node('small',
      `Coverage: ${row.observed_minutes}/${row.expected_minutes} minutes · ${row.complete?'complete 24 hours':'partial recorded total'}`));
    mains.append(card);
  }
  $('recording-basis').textContent = state.circuit_history_basis || 'Circuit totals are recorded observations; gaps are not estimated.';
  const container = $('rooms');
  container.replaceChildren();
  for (const room of displayedRooms.filter(room=>visible.has(room.id))) {
    const button = node('button', '', 'home3d-room');
    button.type = 'button';
    button.dataset.roomId = room.id;
    if(metric==='temperature_f')button.dataset.band=temperatureBand(room.temperature_f) || 'unknown';
    button.append(node('strong', room.name), node('span', format(room[metric], metric)),
      ...(metric==='temperature_f'?[node('small',room.temperature_sensor_count?`${room.temperature_sensor_count} room sensor(s) · ${historical?'recorded observation':'recent observation'}`:'No fresh room reading · no temperature inferred')]:[
      node('small', `${room.mapped_circuits} mapped · ${room.reporting_circuits} reporting`),
      node('small', `${format(room.kwh_24h, 'kwh_24h')} · ${format(room.cost_24h, 'cost_24h')} recorded / 24h`)]));
    button.addEventListener('click', () => selectRoom(room.id));
    container.append(button);
  }
  const sensors=$('sensors');sensors.replaceChildren();
  if(!state.sensors.length)sensors.append(node('p','No recorded sensors yet. Existing Aqara, climate and EcoQube observations appear here automatically.'));
  for(const row of displayedSensors.filter(row=>!row.placement || visible.has(row.placement.room_id))){
    const card=node('article','','home3d-sensor');
    card.dataset.sensorSource=row.source;
    const room=config.plan.rooms.find(room=>room.id===row.placement?.room_id);
    card.append(node('strong',row.name),node('span',sensorReading(row)),
      node('small',`${row.source} · ${room?.name || 'Unplaced'} · ${historical?'History bucket':row.stale?'Last recorded (stale)':'Recent observation'}${row.placement?.mount==='pole-base'?' · pole base':''}`),
      node('small',row.timestamp ? new Date(row.timestamp).toLocaleString() : 'No observation available'),
      ...(row.measurement_time_known===false ? [node('small','Controller observation; sensor measurement time unknown')] : []));
    sensors.append(card);
  }
  editor.refreshSensors(state.sensors);
  view?.update(displayedRooms, metric, format, displayedSensors);
}

async function loadTemperatures() {
  const request=++temperatureRequest;
  const params=new URLSearchParams({window:$('temperature-window').value});
  if(temperatureEnd)params.set('end',temperatureEnd);
  try {
    const history=await api(`temperatures?${params}`);
    if(request!==temperatureRequest)return;
    if(history.revision!==config.revision)throw new Error('Floor plan changed; reload the saved plan.');
    temperatureHistory=history;
    const slider=$('temperature-time');slider.disabled=false;slider.max=history.times.length-1;
    slider.value=history.times.length-1;
    if(temperatureEnd)temperatureIndex=history.times.length-1;
    $('temperature-newer').disabled=!temperatureEnd;
    renderState();
  } catch(error){if(request===temperatureRequest)$('temperature-time-label').textContent=`History unavailable: ${error.message}`;}
}
function shiftTemperatures(direction) {
  if(!temperatureHistory)return;
  const span=Date.parse(temperatureHistory.end)-Date.parse(temperatureHistory.start);
  const next=Date.parse(temperatureHistory.end)+direction*span;
  temperatureEnd=next>=Date.now()-60000?null:new Date(next).toISOString();
  temperatureIndex=temperatureEnd?0:null;loadTemperatures();
}

function renderLevels() {
  const levels=levelsFor(config.plan),select=$('level');
  if(selectedLevel!=='all' && !levels.some(level=>level.id===selectedLevel))selectedLevel=levels[0].id;
  select.replaceChildren();
  for(const level of [...levels,{id:'all',name:'All floors · stacked'}]) {
    const option=node('option',level.name);option.value=level.id;select.append(option);
  }
  select.value=selectedLevel;
}
function changeLevel() {
  if(!config || !state)return;
  selectedLevel=$('level').value;
  const url=new URL(location.href);url.searchParams.set('level',selectedLevel);
  history.replaceState(null,'',url);
  view?.setLevel(selectedLevel);editor.setLevel(selectedLevel);renderState();
}

function selectRoom(id) {
  for (const room of $('rooms').children) room.setAttribute('aria-pressed', String(room.dataset.roomId === id));
  const room = state.rooms.find(row => row.id === id);
  if (room) $('save-status').textContent = `${room.name}: `
    + (state.circuits.filter(row => row.room_id === id).map(row => row.display_name).join(', ') || 'No circuits assigned');
}

function mappingSignature(payload) {
  return JSON.stringify([payload.active_device_gid, payload.circuits.map(row => row.channel_name).sort()]);
}

function renderMappings() {
  signature = mappingSignature(state);
  $('panel').textContent = `Panel: ${state.active_device_gid || 'No readings yet'}. `
    + (state.other_panel_bindings ? `${state.other_panel_bindings} mappings from other panels are preserved.` : '');
  const container = $('mappings');
  container.replaceChildren();
  for (const circuit of state.circuits) {
    const label = node('label', circuit.display_name + (circuit.missing ? ' (not reported)' : ''));
    const select = document.createElement('select');
    select.setAttribute('aria-label', `Room for ${circuit.display_name}`);
    select.dataset.channel = circuit.channel_name;
    const empty = node('option', 'Unassigned'); empty.value = ''; select.append(empty);
    for (const room of config.plan.rooms) {
      const option = node('option', `${levelName(config.plan,room)} · ${room.name}`); option.value = room.id; select.append(option);
    }
    select.value = circuit.room_id || '';
    select.addEventListener('change', () => {
      dirty = true;
      $('save').disabled = needsReload || editor.dirty;
      $('save-status').textContent = 'Unsaved room mappings';
    });
    label.append(select);
    container.append(label);
  }
}

async function refresh() {
  if (!config || busy || document.hidden) return;
  busy = true;
  const expectedGeneration = generation;
  try {
    const next = await api('state');
    if (expectedGeneration !== generation) return;
    if (next.revision !== config.revision || mappingSignature(next) !== signature) {
      needsReload = true;
      editor.block(true);
      $('save').disabled = true;
      $('save-status').textContent = 'The saved plan or panel circuits changed. Reload the saved plan before editing.';
    }
    // Never apply a new plan's room ids to the old geometry.
    if (next.revision !== config.revision) return;
    state = next;
    renderState();
  } catch (error) {
    if (expectedGeneration !== generation) return;
    if (state) {
      state = { ...state, online: false, current_watts: null,
        sensor_sync: state.sensor_sync ? {...state.sensor_sync,ok:false} : undefined,
        mains: state.mains?.map(row=>({...row,watts:null})),
        sensors: state.sensors.map(row=>({...row,stale:true})),
        rooms: state.rooms.map(room => ({ ...room, watts: null, reporting_circuits: 0 })) };
      renderState();
    }
    $('status').textContent = `${error.message} Last received history retained; live power unavailable.`;
  } finally {
    busy = false;
  }
}

async function load() {
  const expectedGeneration = ++generation;
  $('save').disabled = true;
  const nextConfig = await api('config');
  const nextState = await api('state');
  if (expectedGeneration !== generation) return;
  if (nextState.revision !== nextConfig.revision) throw new Error('Plan changed while loading. Reload the saved plan.');
  config = nextConfig; state = nextState;
  temperatureHistory=null;temperatureIndex=null;++temperatureRequest;
  dirty = false; needsReload = false;
  $('name').textContent = `${config.plan.name}${config.example ? ' · Example layout, not your measured home' : ''}`;
  $('save-status').textContent = '';
  renderMappings();
  renderLevels();
  editor.setConfig(config, state.sensors);
  editor.setLevel(selectedLevel);
  editorRoot.hidden = false;
  view?.dispose(); view = null;
  $('render-error').hidden = true;
  try { view = createView($('canvas'), $('labels'), config.plan, selectRoom); }
  catch (error) {
    $('render-error').hidden = false;
    $('render-error').textContent = '3D rendering is unavailable in this browser. Room data and mappings still work below.';
    console.warn('Home 3D renderer unavailable:', error.message);
  }
  view?.setLevel(selectedLevel);
  renderState();
  if($('metric').value==='temperature_f')loadTemperatures();
  if(location.hash==='#home3d-floor-plan')$('floor-plan').scrollIntoView({block:'start'});
}

async function save(plan, bindings, sensors=config.sensors) {
  if(saving)return false;
  saving=true;editor.setSaving(true);
  $('save-status').textContent='Saving...';
  for(const select of $('mappings').querySelectorAll('select'))select.disabled=true;
  $('save').disabled = true;
  try {
    await api('config', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan, bindings, sensors, revision: config.revision }) });
    await load();
    $('save-status').textContent = 'Saved';
    return true;
  } catch (error) {
    $('save-status').textContent = error.message;
    $('save').disabled = needsReload || !dirty;
    return false;
  } finally {
    saving=false;editor.setSaving(false);
    for(const select of $('mappings').querySelectorAll('select'))select.disabled=false;
  }
}

function collectBindings() {
  const visible = new Set(state.circuits.map(row => row.channel_name));
  const bindings = config.bindings.filter(binding => binding.device_gid !== state.active_device_gid
    || !visible.has(binding.channel_name));
  for (const select of $('mappings').querySelectorAll('select')) {
    if (select.value) bindings.push({ device_gid: state.active_device_gid,
      channel_name: select.dataset.channel, room_id: select.value });
  }
  return bindings;
}
$('save').addEventListener('click', () => save(config.plan, collectBindings()));
$('level').addEventListener('change',changeLevel);
$('metric').addEventListener('change', () => {
  if(!state)return;
  const url=new URL(location.href);url.searchParams.set('metric',$('metric').value);history.replaceState(null,'',url);
  renderState();if($('metric').value==='temperature_f' && !temperatureHistory)loadTemperatures();
});
$('temperature-window').addEventListener('change',()=>{temperatureIndex=null;loadTemperatures();});
$('temperature-time').addEventListener('input',()=>{temperatureIndex=Number($('temperature-time').value);renderState();});
$('temperature-time').addEventListener('wheel',event=>{
  if(!temperatureHistory || $('temperature-time').disabled)return;
  event.preventDefault();const slider=$('temperature-time');
  slider.value=Math.max(0,Math.min(Number(slider.max),Number(slider.value)+(event.deltaY>0?-1:1)));
  temperatureIndex=Number(slider.value);renderState();
},{passive:false});
$('temperature-older').addEventListener('click',()=>shiftTemperatures(-1));
$('temperature-newer').addEventListener('click',()=>shiftTemperatures(1));
$('temperature-live').addEventListener('click',()=>{temperatureEnd=null;temperatureIndex=null;renderState();loadTemperatures();});
$('reset').addEventListener('click', () => view?.reset());
$('refresh').addEventListener('click', refresh);
$('reload').addEventListener('click', () => {
  if ((!dirty && !editor.dirty) || confirm('Discard unsaved floor-plan and mapping changes and reload?')) load().catch(error => { $('status').textContent = error.message; });
});
$('import').addEventListener('change', async event => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file || !config) return;
  try {
    if (file.size > 250 * 1024) throw new Error('Floor plan must be smaller than 250 KB.');
    const plan = JSON.parse(await file.text());
    if (confirm('Replace the floor plan and clear all saved room mappings and sensor placements, including other panels?')) await save(plan, [], []);
  } catch (error) { $('save-status').textContent = error.message; }
});
$('export').addEventListener('click', () => {
  if (!config) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(config.plan, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'floor-plan.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
window.addEventListener('beforeunload', event => { if (dirty || editor.dirty) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('pagehide', () => { clearInterval(timer); view?.dispose(); view = null; });
window.addEventListener('pageshow', event => { if (event.persisted) start(); });
async function start() {
  clearInterval(timer);
  try { await load(); }
  catch (error) { $('status').textContent = error.message; }
  timer = setInterval(refresh, 30000);
}
start();
