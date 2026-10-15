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
