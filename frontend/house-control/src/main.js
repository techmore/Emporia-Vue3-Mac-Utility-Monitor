import { createView } from './view.js';
import { createEditor } from './editor.js';
import { retainRoomBindings } from './plan-edit.js';
import { sensorReading } from './sensors.js';

const $ = id => document.getElementById(`home3d-${id}`);
const format = (value, metric) => value == null ? 'Unknown' : metric === 'watts'
  ? `${Math.round(value).toLocaleString()} W` : metric === 'kwh_24h'
    ? `${value.toFixed(2)} kWh` : `$${value.toFixed(2)}`;
let config, state, view, timer, dirty = false, busy = false, needsReload = false, signature;
let generation = 0, saving = false;
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
  const metric = $('metric').value;
  $('status').textContent = `${state.online ? 'Collector live' : 'Collector offline or readings stale'} · `
    + `Whole home: ${format(state.current_watts, 'watts')} · Last reading: ${state.last_reading || 'none'}`;
  if(state.sensor_sync) {
    $('status').textContent = `Local preview · SER8 feed ${state.sensor_sync.ok?'connected':'unavailable; retained values are stale'}`
      + ` · Whole home: ${format(state.current_watts,'watts')} · Power ${state.online?'recent':'stale or unavailable'}`
      + ` · Last reading: ${state.last_reading || 'none'} · Last refresh: ${state.sensor_sync.last_success || 'none'}`;
  }
  const mains=$('mains');mains.replaceChildren();
  $('mains-section').hidden=!state.mains?.length;
  for(const row of state.mains || []) {
    const card=node('article','','home3d-sensor');
    card.append(node('strong',row.name),node('span',format(row.watts,'watts')),
      node('small',`${format(row.kwh_24h,'kwh_24h')} · ${format(row.cost_24h,'cost_24h')} recorded / 24h`),
      node('small',`Measured: ${row.timestamp}${row.watts==null?' · live power unavailable':''}`));
    mains.append(card);
  }
  const container = $('rooms');
  container.replaceChildren();
  for (const room of state.rooms) {
    const button = node('button', '', 'home3d-room');
    button.type = 'button';
    button.dataset.roomId = room.id;
    button.append(node('strong', room.name), node('span', format(room[metric], metric)),
      node('small', `${room.mapped_circuits} mapped · ${room.reporting_circuits} reporting`),
      node('small', `${format(room.kwh_24h, 'kwh_24h')} · ${format(room.cost_24h, 'cost_24h')} recorded / 24h`));
    button.addEventListener('click', () => selectRoom(room.id));
    container.append(button);
  }
  const sensors=$('sensors');sensors.replaceChildren();
  if(!state.sensors.length)sensors.append(node('p','No recorded sensors yet. Existing Aqara, climate and EcoQube observations appear here automatically.'));
  for(const row of state.sensors){
    const card=node('article','','home3d-sensor');
    card.dataset.sensorSource=row.source;
    const room=config.plan.rooms.find(room=>room.id===row.placement?.room_id);
    card.append(node('strong',row.name),node('span',sensorReading(row)),
      node('small',`${row.source} · ${room?.name || 'Unplaced'} · ${row.stale?'Last recorded (stale)':'Recent observation'}`),
      node('small',row.timestamp ? new Date(row.timestamp).toLocaleString() : 'No observation available'));
    sensors.append(card);
  }
  editor.refreshSensors(state.sensors);
  view?.update(state.rooms, metric, format, state.sensors);
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
      const option = node('option', room.name); option.value = room.id; select.append(option);
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
  dirty = false; needsReload = false;
  $('name').textContent = `${config.plan.name}${config.example ? ' · Example layout, not your measured home' : ''}`;
  $('save-status').textContent = '';
  renderMappings();
  editor.setConfig(config, state.sensors);
  editorRoot.hidden = false;
  view?.dispose(); view = null;
  $('render-error').hidden = true;
  try { view = createView($('canvas'), $('labels'), config.plan, selectRoom); }
  catch (error) {
    $('render-error').hidden = false;
    $('render-error').textContent = '3D rendering is unavailable in this browser. Room data and mappings still work below.';
    console.warn('Home 3D renderer unavailable:', error.message);
  }
  renderState();
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
$('metric').addEventListener('change', () => { if (state) renderState(); });
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
