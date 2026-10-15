// Geometry operations shared by pointer and keyboard editing. Dimensions are meters.
export const snap = value => Math.round(value * 4) / 4;
const limit = value => Math.max(-100, Math.min(100, snap(value)));
export function moveItem(plan, kind, index, dx, dz) {
  const item = plan[kind][index];
  if (kind === 'rooms') {
    item.rect[0] = limit(item.rect[0] + dx);
    item.rect[1] = limit(item.rect[1] + dz);
  } else if (kind === 'furniture') {
    item.pos = [limit(item.pos[0] + dx), limit(item.pos[1] + dz)];
  } else if (kind === 'openings') {
    // Clamp the translation, not each endpoint, so an opening stays axis-aligned.
    dx = Math.max(-100 - Math.min(item.from[0], item.to[0]), Math.min(100 - Math.max(item.from[0], item.to[0]), snap(dx)));
    dz = Math.max(-100 - Math.min(item.from[1], item.to[1]), Math.min(100 - Math.max(item.from[1], item.to[1]), snap(dz)));
    item.from = [item.from[0] + dx, item.from[1] + dz];
    item.to = [item.to[0] + dx, item.to[1] + dz];
  }
}
export function resizeRoom(room, x, z) {
  room.rect[2] = Math.max(0.5, Math.min(50, snap(x - room.rect[0])));
  room.rect[3] = Math.max(0.5, Math.min(50, snap(z - room.rect[1])));
}
export function drawnRoom(start, end, id) {
  const x = limit(Math.min(start[0], end[0])), z = limit(Math.min(start[1], end[1]));
  return { id, name: 'New room', floor: 'oak', rect: [x, z,
    Math.max(0.5, Math.min(50, snap(Math.abs(end[0] - start[0])))),
    Math.max(0.5, Math.min(50, snap(Math.abs(end[1] - start[1]))))] };
}
export function retainRoomBindings(bindings, plan) {
  const rooms = new Set(plan.rooms.map(room => room.id));
  return bindings.filter(binding => rooms.has(binding.room_id));
}
export function clampSensor(position, room) {
  const [x,z,w,d] = room.rect;
  return [Math.max(x,Math.min(x+w,snap(position[0]))),Math.max(z,Math.min(z+d,snap(position[1])))];
}
export function relocateSensors(placements, roomId, before, room) {
  for (const sensor of placements.filter(row => row.room_id === roomId)) {
    sensor.position = clampSensor([sensor.position[0]+room.rect[0]-before[0],
      sensor.position[1]+room.rect[1]-before[1]],room);
  }
}
// Match the pinned vendor furniture constructors; fixed-size templates expose no sizes.
export function furnitureDimensions(item) {
  const widths={sofa:2.2,coffeeTable:1.1,tv:1.7,sideboard:1.6,diningSet:1.8,
    island:2,kitchen:3,bed:1.6,dresser:1.3,vanity:1,desk:1.4,wardrobe:1.6,bookshelf:1};
  const heights={plant:1.2,wardrobe:2.1,bookshelf:1.8};
  const fields=[];
  if(widths[item.type]!=null)fields.push(['width',item.width ?? widths[item.type]]);
  if(heights[item.type]!=null)fields.push(['height',item.height ?? heights[item.type]]);
  return fields;
}
