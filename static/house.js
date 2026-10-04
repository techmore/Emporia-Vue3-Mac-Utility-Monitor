/* Climate extension: observed frames and simulation stay separate. No interpolation across gaps. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const ns = 'http://www.w3.org/2000/svg';
  let data, index = 0, timer, generation = 0;
  const svg = (tag, attrs, content) => {
    const element = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    if (content !== undefined) element.textContent = content;
    return element;
  };
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const temperature = c => c == null ? 'No reading' : `${($('house-unit').value === 'F' ? c * 9 / 5 + 32 : c).toFixed(1)}°${$('house-unit').value}`;
  const average = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  const roomTemperature = room => average(data.sensors.filter(s => s.room_id === room).map(s => data.frames[index].temperatures[s.key]).filter(v => v != null));
  function pause() { clearInterval(timer); timer = null; $('house-play').textContent = '▶ Play'; }
  async function load() {
    pause(); const token = ++generation;
    $('house-warning').textContent = 'Loading sensor history…';
    try {
      const response = await fetch(`/api/climate/replay?days=${$('house-days').value}&demo=${$('house-source').value === 'demo' ? 1 : 0}`);
      if (!response.ok) throw new Error('Could not load history. Try refreshing.');
      const payload = await response.json(); if (token !== generation) return;
      data = payload; index = data.frames.length - 1;
      $('house-scrubber').max = index; $('house-scrubber').value = index;
      $('house-warning').textContent = data.warning;
      $('house-empty').hidden = data.sensors.length > 0;
      $('house-count').textContent = `${data.sensors.length} ${data.demo ? 'simulated' : 'registered'}`;
      $('house-sensors').replaceChildren(); $('model-room').replaceChildren();
      data.rooms.filter(r => r.id !== 'outdoor').forEach(room => {
        const option = document.createElement('option'); option.value = room.id; option.textContent = room.name; $('model-room').append(option);
      });
      data.sensors.forEach(sensor => {
        const row = document.createElement('div'); row.className = 'house-sensor';
        const name = document.createElement('strong'); name.textContent = sensor.name;
        const reading = document.createElement('div'); reading.className = 'sensor-reading'; reading.dataset.key = sensor.key;
        const label = document.createElement('label'); label.textContent = 'Place in room';
        const select = document.createElement('select'); select.setAttribute('aria-label', `Room for ${sensor.name}`);
        [ {id:'', name:'Unplaced'}, ...data.rooms].forEach(room => {
          const option = document.createElement('option'); option.value = room.id; option.textContent = room.name; select.append(option);
        });
        select.value = sensor.room_id || ''; label.append(select);
        select.addEventListener('change', async () => {
          const previous = sensor.room_id;
          if (!data.demo) {
            select.disabled = true;
            try {
              const result = await fetch('/api/climate/placement', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source:sensor.source,sensor_id:sensor.sensor_id,room_id:select.value || null})});
              if (!result.ok) throw new Error('Could not save sensor placement.');
            } catch (error) { select.value = previous || ''; $('house-warning').textContent = error.message; return; }
            finally { select.disabled = false; }
          }
          sensor.room_id = select.value || null; draw();
        });
        row.append(name, reading, label); $('house-sensors').append(row);
      });
      draw();
    } catch (error) { if (token === generation) { data = null; $('house-plan').replaceChildren(); $('house-power').replaceChildren(); $('house-sensors').replaceChildren(); $('model-result').textContent = ''; $('house-warning').textContent = error.message; } }
  }
  function draw() {
    if (!data) return;
    const plan = $('house-plan'); plan.replaceChildren();
    data.rooms.forEach(room => {
      const temp = roomTemperature(room.id);
      const fill = temp == null ? css('--surface2') : temp < 20 ? css('--olive-100') : temp < 24 ? css('--olive-300') : css('--olive-500');
      plan.append(svg('rect', {x:room.x,y:room.y,width:room.width,height:room.height,rx:5,fill,class:'room'}));
      plan.append(svg('text', {x:room.x+18,y:room.y+32,class:'room-name'}, room.name));
      plan.append(svg('text', {x:room.x+18,y:room.y+68,class:'room-temp',style:temp == null ? 'font-size:17px' : ''}, temperature(temp)));
      data.sensors.filter(s => s.room_id === room.id).forEach((sensor,i) => {
        const dot = svg('circle',{cx:room.x+24+i*20,cy:room.y+95,r:5,class:'sensor-dot'});
        dot.append(svg('title',{},`${sensor.name}: ${temperature(data.frames[index].temperatures[sensor.key])}`)); plan.append(dot);
      });
    });
    document.querySelectorAll('.sensor-reading').forEach(element => {
      const key = element.dataset.key, humidity = data.frames[index].humidity[key];
      element.textContent = `${temperature(data.frames[index].temperatures[key])}${humidity == null ? '' : ` · ${humidity.toFixed(0)}% humidity`}`;
    });
    $('house-time').textContent = new Date(data.frames[index].timestamp).toLocaleString([], {dateStyle:'medium',timeStyle:'short'}) + ' · hourly observations';
    drawPower(); model();
  }
  function drawPower() {
    const chart = $('house-power'); chart.replaceChildren();
    const observed = data.power.filter(p => p.total_kwh != null);
    $('house-power-note').textContent = `${data.demo ? 'Simulated' : 'Recorded'} · ${data.power_resolution === 'day' ? 'daily' : 'hourly'} kWh`;
    const total = observed.reduce((sum,p) => sum + p.total_kwh,0);
    $('house-power-total').textContent = observed.length ? `${total.toFixed(2)} kWh · $${(total * data.rate_per_kwh).toFixed(2)} at current rate $${data.rate_per_kwh.toFixed(4)}/kWh${data.demo ? ' · simulated' : ' · recorded total, gaps excluded'}` : 'No whole-home energy readings in this period.';
    const peak = Math.max(.001,...observed.map(p => p.total_kwh));
    const width = 1000 / Math.max(1,data.power.length);
    const frameTime = new Date(data.frames[index].timestamp);
    const localPeriod = `${frameTime.getFullYear()}-${String(frameTime.getMonth()+1).padStart(2,'0')}-${String(frameTime.getDate()).padStart(2,'0')}` + (data.power_resolution === 'hour' ? ` ${String(frameTime.getHours()).padStart(2,'0')}:00` : '');
    data.power.forEach((point,i) => {
      if (point.total_kwh == null) return;
      const height = Math.max(1,point.total_kwh/peak*90);
      const bar = svg('rect',{x:i*width,y:100-height,width:Math.max(1,width-2),height,rx:2,fill:point.period === localPeriod ? css('--olive-950') : css('--chart1')});
      bar.append(svg('title',{},`${point.period}: ${point.total_kwh.toFixed(3)} kWh`)); chart.append(bar);
    });
  }
  function model() {
    if (!data) return;
    const initial = roomTemperature($('model-room').value), outdoor = roomTemperature('outdoor');
    if (initial == null || outdoor == null) { $('model-result').textContent = 'Choose an hour with indoor and outdoor readings to explore cooling.'; return; }
    const ids = ['target','loss','capacity','power','cop','gains'];
    if (!ids.every(id => $('model-'+id).value !== '' && $('model-'+id).checkValidity())) { $('model-result').textContent = 'Enter values within the indicated ranges.'; return; }
    const [target, loss, capacity, power, cop, gains] = ids.map(id => Number($('model-'+id).value));
    const {cooled, passive, kwh} = window.coolingScenario({initial, outdoor, target, loss, capacity, power, cop, gains});
    $('model-result').textContent = `After 24h: ${temperature(cooled)} with cooling · ${temperature(passive)} without. Scenario cooling: ${kwh.toFixed(2)} kWh / $${(kwh*data.rate_per_kwh).toFixed(2)}.${data.demo ? ' Starts from simulated readings.' : ''}`;
  }
  $('house-play').addEventListener('click',()=> {
    if (timer) return pause(); if (!data) return;
    if (index === data.frames.length-1) index=0;
    $('house-play').textContent='⏸ Pause'; draw(); $('house-scrubber').value=index;
    timer=setInterval(()=> { if (index >= data.frames.length-1) return pause(); index++; $('house-scrubber').value=index; draw(); },400);
  });
  $('house-scrubber').addEventListener('input',()=> { pause(); index=Number($('house-scrubber').value); draw(); });
  ['house-source','house-days'].forEach(id => $(id).addEventListener('change',load));
  $('house-unit').addEventListener('change',draw); $('house-refresh').addEventListener('click',load);
  document.querySelectorAll('.house-inputs input,.house-inputs select').forEach(input => input.addEventListener('input',model));
  window.addEventListener('pagehide',pause); load();
})();
