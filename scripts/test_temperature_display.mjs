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
