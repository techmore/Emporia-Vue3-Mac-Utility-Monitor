import { drawnRoom, moveItem, resizeRoom, retainRoomBindings, snap } from './plan-edit.js';

const SVG = 'http://www.w3.org/2000/svg';
const furnitureTypes = ['rug','sofa','armchair','coffeeTable','tv','sideboard','diningSet','island',
  'kitchen','fridge','bed','nightstand','dresser','plant','vanity','bathtub','toilet','desk','wardrobe','bookshelf'];
const element = (tag, text) => { const node = document.createElement(tag); node.textContent = text; return node; };
const svgNode = (tag, attributes, text = '') => {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  node.textContent = text; return node;
};

export function createEditor(root, { onSave, onDirty }) {
  const get = name => root.querySelector(`[data-editor="${name}"]`);
  const canvas = get('canvas'), inspector = get('inspector'), picker = get('selection');
  let config, plan, selected = { kind: 'rooms', index: 0 }, past = [], future = [], gesture, draw = false;
  let base, changed = false, blocked = false;
  function changedState() {
    changed = JSON.stringify(plan) !== base;
    get('save').disabled = blocked || !changed;
    get('undo').disabled = !past.length;
    get('redo').disabled = !future.length;
    onDirty(changed);
  }
  function checkpoint() { past.push(structuredClone(plan)); past = past.slice(-50); future = []; }
  function edit(action) { checkpoint(); action(); render(); changedState(); }
  function point(event) {
    const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(canvas.getScreenCTM().inverse());
    return [snap(p.x), snap(p.y)];
  }
  function fields() {
    inspector.replaceChildren();
    if (!selected) return;
    const item = plan[selected.kind][selected.index];
    if (!item) return;
    const field = (name, value, update, options) => {
      const label = element('label', name), input = document.createElement(options ? 'select' : 'input');
      if (options) for (const option of options) { const node = element('option', option); node.value = option; input.append(node); }
      else { input.type = typeof value === 'number' ? 'number' : 'text'; if (input.type === 'number') input.step = '0.25'; else input.maxLength = 120; }
      input.value = value; input.setAttribute('aria-label', name);
      input.addEventListener('change', () => {
        const next = typeof value === 'number' ? Number(input.value) : input.value;
        if ((typeof value === 'number' && (!input.value.trim() || !Number.isFinite(next))) || (typeof value === 'string' && !next.trim())) {
          get('status').textContent = 'Enter a valid value.'; input.value = value; return;
        }
        edit(() => update(next));
      });
      label.append(input); inspector.append(label);
    };
    if (selected.kind === 'rooms') {
      field('Room name', item.name, value => { item.name = value; });
      ['X (m)','Z (m)','Width (m)','Depth (m)'].forEach((name, i) => field(name, item.rect[i], value => {
        item.rect[i] = i < 2 ? Math.max(-100, Math.min(100, value)) : Math.max(0.5, Math.min(50, value));
      }));
      field('Floor', item.floor, value => { item.floor = value; }, ['oak','walnut','tile','stone']);
      inspector.append(element('small', 'Drag the room to move it; drag its corner to resize. Openings and furniture move independently.'));
    } else if (selected.kind === 'furniture') {
      field('Furniture type', item.type, value => { item.type = value; }, furnitureTypes);
      ['X (m)','Z (m)'].forEach((name, i) => field(name, item.pos[i], value => { item.pos[i] = Math.max(-100, Math.min(100, value)); }));
      field('Rotation', item.rot || 0, value => { item.rot = Math.max(-360, Math.min(360, value)); });
      field('Furniture width', item.width || 1, value => { item.width = Math.max(0.3, Math.min(10, value)); });
      field('Furniture height', item.height || 1, value => { item.height = Math.max(0.3, Math.min(10, value)); });
    } else {
      field('Opening type', item.type, value => { item.type = value; }, ['door','window','open']);
      const horizontal = item.from[1] === item.to[1];
      field('Orientation', horizontal ? 'horizontal' : 'vertical', value => {
        const length = Math.max(.5, Math.abs(item.to[0] - item.from[0]) + Math.abs(item.to[1] - item.from[1]));
        item.to = value === 'horizontal' ? [Math.min(100,item.from[0]+length),item.from[1]] : [item.from[0],Math.min(100,item.from[1]+length)];
      }, ['horizontal','vertical']);
      ['X (m)','Z (m)'].forEach((name, i) => field(name, item.from[i], value => {
        const delta = value - item.from[i]; moveItem(plan, 'openings', selected.index, i === 0 ? delta : 0, i === 1 ? delta : 0);
      }));
      field('Opening length', Math.abs(item.to[0]-item.from[0]) + Math.abs(item.to[1]-item.from[1]), value => {
        const length = Math.max(.5, Math.min(10, value));
        item.to = horizontal ? [Math.min(100,item.from[0]+length),item.from[1]] : [item.from[0],Math.min(100,item.from[1]+length)];
      });
    }
  }
  function render(refit = true, updateFields = true) {
    get('name').value = plan.name;
    if (refit) {
      const xs = plan.rooms.flatMap(room => [room.rect[0],room.rect[0]+room.rect[2]]);
      const zs = plan.rooms.flatMap(room => [room.rect[1],room.rect[1]+room.rect[3]]);
      const x = Math.min(...xs)-3, z = Math.min(...zs)-3;
      canvas.setAttribute('viewBox', `${x} ${z} ${Math.max(...xs)-x+3} ${Math.max(...zs)-z+3}`);
    }
    canvas.replaceChildren(); picker.replaceChildren();
    for (const kind of ['rooms','furniture','openings']) plan[kind].forEach((item, index) => {
      const active = selected?.kind === kind && selected.index === index;
      const group = svgNode('g', { 'data-kind':kind, 'data-index':index, class:active ? 'selected' : '' });
      let x, z;
      if (kind === 'rooms') {
        [x,z] = item.rect;
        group.append(svgNode('rect',{x,y:z,width:item.rect[2],height:item.rect[3],class:'editor-room'}));
        group.append(svgNode('text',{x:x+item.rect[2]/2,y:z+item.rect[3]/2,class:'editor-name'},item.name));
        if (active) group.append(svgNode('circle',{cx:x+item.rect[2],cy:z+item.rect[3],r:.22,'data-resize':'true',class:'editor-handle'}));
      } else if (kind === 'furniture') {
        [x,z] = item.pos;
        group.append(svgNode('rect',{x:x-.4,y:z-.3,width:.8,height:.6,class:'editor-furniture',transform:`rotate(${item.rot||0},${x},${z})`}));
        group.append(svgNode('text',{x,y:z-.5,class:'editor-name'},item.type));
      } else group.append(svgNode('line',{x1:item.from[0],y1:item.from[1],x2:item.to[0],y2:item.to[1],class:'editor-opening'}));
      canvas.append(group);
      const option = element('option', `${kind === 'rooms' ? 'Room' : kind === 'furniture' ? 'Furniture' : 'Opening'}: ${item.name || item.type}`);
      option.value = `${kind}:${index}`; picker.append(option);
    });
    picker.value = selected ? `${selected.kind}:${selected.index}` : '';
    get('remove').disabled = !selected;
    if (updateFields) fields();
  }
  function select(kind, index) { selected = {kind,index}; render(false); }
  picker.addEventListener('change', () => { const [kind,index] = picker.value.split(':'); select(kind,Number(index)); });
  canvas.addEventListener('pointerdown', event => {
    if (blocked || !plan || event.button !== 0) return;
    const start = point(event), target = event.target.closest('[data-kind]');
    if (!draw && !target) return;
    checkpoint();
    if (draw) {
      if (plan.rooms.length >= 64) { past.pop(); get('status').textContent='Maximum 64 rooms.'; return; }
      plan.rooms.push(drawnRoom(start,start,crypto.randomUUID())); selected={kind:'rooms',index:plan.rooms.length-1};
    } else selected={kind:target.dataset.kind,index:Number(target.dataset.index)};
    gesture={start,original:structuredClone(plan),draw,resize:event.target.hasAttribute('data-resize')};
    canvas.setPointerCapture(event.pointerId); render(false); event.preventDefault();
  });
  canvas.addEventListener('pointermove', event => {
    if (!gesture) return;
    const end=point(event); plan=structuredClone(gesture.original);
    if (gesture.draw) plan.rooms[selected.index]=drawnRoom(gesture.start,end,plan.rooms[selected.index].id);
    else if (gesture.resize) resizeRoom(plan.rooms[selected.index],...end);
    else moveItem(plan,selected.kind,selected.index,end[0]-gesture.start[0],end[1]-gesture.start[1]);
    render(false,false);
  });
  canvas.addEventListener('pointerup', () => {
    if (!gesture) return;
    gesture=null; draw=false; get('draw').setAttribute('aria-pressed','false'); render(); changedState();
  });
  canvas.addEventListener('pointercancel', () => { if (gesture) { plan=past.pop(); gesture=null; draw=false; render(); changedState(); } });
  canvas.addEventListener('keydown', event => {
    const delta={ArrowLeft:[-.25,0],ArrowRight:[.25,0],ArrowUp:[0,-.25],ArrowDown:[0,.25]}[event.key];
    if (delta && selected && !blocked) { event.preventDefault(); edit(()=>moveItem(plan,selected.kind,selected.index,...delta)); }
  });
  get('draw').addEventListener('click', () => { draw=!draw; get('draw').setAttribute('aria-pressed',String(draw)); get('status').textContent=draw?'Drag on the plan to draw a room.':'Select an item to edit.'; });
  get('add-room').addEventListener('click', () => {
    if (plan.rooms.length>=64) return;
    edit(()=>{plan.rooms.push(drawnRoom([0,0],[3,3],crypto.randomUUID()));selected={kind:'rooms',index:plan.rooms.length-1};});
  });
  get('add-furniture').addEventListener('click', () => {
    if(plan.furniture.length>=256)return;
    edit(()=>{plan.furniture.push({type:'sofa',pos:[1,1]});selected={kind:'furniture',index:plan.furniture.length-1};});
  });
  get('add-opening').addEventListener('click', () => {
    if(plan.openings.length>=256)return;
    const room=plan.rooms[0], [x,z]=room.rect;
    edit(()=>{plan.openings.push({type:'door',from:[x+.5,z],to:[x+1.5,z]});selected={kind:'openings',index:plan.openings.length-1};});
  });
  get('remove').addEventListener('click', () => {
    if(!selected)return;
    if(selected.kind==='rooms') {
      if(plan.rooms.length===1){get('status').textContent='Keep at least one room.';return;}
      const room=plan.rooms[selected.index];
      if(!confirm(`Delete ${room.name}? Circuit mappings to this room will be removed when saved.`))return;
    }
    edit(()=>{plan[selected.kind].splice(selected.index,1);selected={kind:'rooms',index:0};});
  });
  get('undo').addEventListener('click', () => {if(!past.length)return;future.push(structuredClone(plan));plan=past.pop();selected={kind:'rooms',index:0};render();changedState();});
  get('redo').addEventListener('click', () => {if(!future.length)return;past.push(structuredClone(plan));plan=future.pop();selected={kind:'rooms',index:0};render();changedState();});
  get('name').addEventListener('change', () => {const name=get('name').value.trim();if(name)edit(()=>{plan.name=name;});});
  get('save').addEventListener('click', async () => {
    get('save').disabled=true;
    const ok=await onSave(structuredClone(plan),retainRoomBindings(config.bindings,plan));
    if(!ok)changedState();
  });
  return {
    get dirty(){return changed;},
    setConfig(next) {config=next;plan=structuredClone(next.plan);base=JSON.stringify(plan);past=[];future=[];selected={kind:'rooms',index:0};blocked=false;gesture=null;get('name').value=plan.name;get('status').textContent='Drag rooms or furniture. Use the corner handle or fields to resize. Save to update the 3D view.';render();changedState();},
    block(value){blocked=value;get('save').disabled=value||!changed;},
  };
}
