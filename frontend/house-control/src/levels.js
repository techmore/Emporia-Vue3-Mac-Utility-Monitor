// Levels describe vertical position; room.floor remains the floor material.
export function levelsFor(plan) {
  return (plan.levels || [{id:'first',name:'First floor',elevation:0}])
    .slice().sort((a,b)=>a.elevation-b.elevation);
}
export function itemLevel(item, plan) { return item.level || levelsFor(plan)[0].id; }
export function planForLevel(plan, id) {
  return {...plan, rooms:plan.rooms.filter(item=>itemLevel(item,plan)===id),
    openings:(plan.openings || []).filter(item=>itemLevel(item,plan)===id),
    furniture:(plan.furniture || []).filter(item=>itemLevel(item,plan)===id)};
}
export function visibleRoomIds(plan, id) {
  return new Set(plan.rooms.filter(room=>id==='all' || itemLevel(room,plan)===id).map(room=>room.id));
}
export function levelName(plan, room) {
  return levelsFor(plan).find(level=>level.id===itemLevel(room,plan))?.name || '';
}
export function stairTreads(stair, levels) {
  const lower=levels.find(level=>level.id===stair.level).elevation;
  const rise=levels.find(level=>level.id===stair.toLevel).elevation-lower;
  const segments=stair.path.slice(1).map((end,i)=>{
    const start=stair.path[i],dx=end[0]-start[0],dz=end[1]-start[1];
    return {start,dx,dz,length:Math.hypot(dx,dz)};
  });
  const length=segments.reduce((sum,segment)=>sum+segment.length,0),run=length/stair.steps;
  return Array.from({length:stair.steps},(_,i)=>{
    let distance=(i+.5)*run,segment=segments[0];
    for(const next of segments){segment=next;if(distance<=next.length)break;distance-=next.length;}
    return {x:segment.start[0]+segment.dx*distance/segment.length,
      z:segment.start[1]+segment.dz*distance/segment.length,
      y:lower+(i+1)*rise/stair.steps,run,width:stair.width,horizontal:segment.dx!==0};
  });
}
// Split room surfaces around the stairwell so the upper floor has a real opening.
export function floorTiles(rect, stairs, cutouts=[]) {
  const [x,z,w,d]=rect;
  const holes=[...cutouts.map(([a,b,c,e])=>[Math.max(x,a),Math.max(z,b),Math.min(x+w,a+c),Math.min(z+d,b+e)]),...stairs.flatMap(stair=>stair.path.slice(1).map((end,i)=>{
    const start=stair.path[i],half=stair.width/2;
    return [Math.max(x,Math.min(start[0],end[0])-half),
      Math.max(z,Math.min(start[1],end[1])-half),
      Math.min(x+w,Math.max(start[0],end[0])+half),
      Math.min(z+d,Math.max(start[1],end[1])+half)];
  }))].filter(([a,b,c,e])=>a<c && b<e);
  const xs=[...new Set([x,x+w,...holes.flatMap(h=>[h[0],h[2]])])].sort((a,b)=>a-b);
  const zs=[...new Set([z,z+d,...holes.flatMap(h=>[h[1],h[3]])])].sort((a,b)=>a-b);
  const tiles=[];
  for(let i=1;i<xs.length;i++)for(let j=1;j<zs.length;j++){
    const cx=(xs[i-1]+xs[i])/2,cz=(zs[j-1]+zs[j])/2;
    if(!holes.some(([a,b,c,e])=>cx>a && cx<c && cz>b && cz<e))
      tiles.push([xs[i-1],zs[j-1],xs[i]-xs[i-1],zs[j]-zs[j-1]]);
  }
  return tiles;
}
