import assert from 'node:assert/strict';
import {readingState,temperatureSummary,placeTemperatureLabels} from '../frontend/house-control/src/temperature-display.js';
const row=(value,stale=false)=>({metrics:{temperature_f:value},stale});
assert.deepEqual(temperatureSummary([row(0),row(72),row(100,true),row(null)],true),{recent:2,stale:1,missing:1,min:0,max:72});
assert.equal(readingState(row(0),true).value,0);
assert.equal(readingState(row(72),false).status,'stale');
assert.equal(readingState(row(NaN),true).status,'missing');
for(const [temp,band] of [[69.9,'cold'],[70,'comfortable'],[75,'comfortable'],[75.1,'warm'],[78,'hot']])assert.equal(readingState(row(temp),true).band,band);
assert.equal(readingState(row(100,true),true).band,'unknown');
assert.equal(temperatureSummary([row(100,true)],true).max,null);
for(const [width,height] of [[640,400],[350,220]]){
 const labels=placeTemperatureLabels(Array.from({length:8},(_,i)=>({id:i,x:width/2,y:height/2,w:100,h:55,priority:i===7?3:1})),width,height);
 assert.equal(labels[0].id,7);
 for(const a of labels){
   assert(a.x>=0&&a.y>=0&&a.x+a.w<=width&&a.y+a.h<=height);
   for(const b of labels.filter(b=>b!==a))assert(!(a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y));
 }
}
console.log('Temperature display: fresh/stale/missing, zero values, boundaries, range and bounded non-overlapping labels passed');

const house={left:150,right:490,top:45,bottom:355};
const outside=placeTemperatureLabels(Array.from({length:6},(_,i)=>({id:i,x:220+i*35,y:100+i*30,w:100,h:45})),640,400,house);
assert.equal(outside.length,6);
for(const a of outside){
 assert(a.x+a.w<=house.left-12 || a.x>=house.right+12 || a.y+a.h<=house.top-12 || a.y>=house.bottom+12);
 assert(a.x>=0 && a.x+a.w<=640 && a.y>=0 && a.y+a.h<=400);
 for(const b of outside.filter(b=>b!==a))assert(!(a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y));
}
assert.equal(placeTemperatureLabels([{x:200,y:200,w:100,h:50}],400,400,{left:0,right:400,top:0,bottom:400}).length,0);
console.log('Outside-house callouts: six sensors stay off the home, separated, bounded, and overflow stays in the list');
const {temperatureSparkline}=await import('../frontend/house-control/src/temperature-display.js');
const times=['2026-10-18T00:00:00Z','2026-10-18T01:00:00Z','2026-10-18T02:00:00Z'];
const crossing=temperatureSparkline([60,80,82],times);
assert.deepEqual(new Set(crossing.paths.map(row=>row.band)),new Set(['cold','normal','hot']));
assert.equal(crossing.min,60);assert.equal(crossing.max,82);
const gaps=temperatureSparkline([60,null,80],times);
assert.equal(gaps.paths.length,0);assert.equal(gaps.dots.length,2);
assert.equal(temperatureSparkline([null,null,null],times).min,null);
assert.equal(temperatureSparkline([0,0,0],times).min,0);
console.log('24h sparklines split at 68/78 degrees, retain zeros, and never connect missing buckets');
const {gableProfile}=await import('../frontend/house-control/src/barn-roof.js');
for(const bounds of [{minX:0,maxX:10,minZ:0,maxZ:6},{minX:-5,maxX:2,minZ:3,maxZ:14}]){
 const points=gableProfile(bounds,2.6);
 assert(points[1][1]>points[0][1]);assert.equal(points[1][1],points[4][1]);
 for(const [x,y,z] of points)assert(x>=bounds.minX&&x<=bounds.maxX&&z>=bounds.minZ&&z<=bounds.maxZ&&y>=2.6);
}
console.log('Barn gables keep eaves and ridge within either footprint orientation');
