import assert from 'node:assert/strict';
import {geometry,segments} from '../frontend/house-control/src/mains-chart.js';

const history={start:'2026-10-14T14:00:00+00:00',end:'2026-10-15T14:00:00+00:00',interval_seconds:60,
  series:['Main','Mains_A','Mains_B'].map(channel_name=>({channel_name,values:Array(1440).fill(null)}))};
assert.deepEqual(segments([null,0,10,null,20,null,null,0]),[[[1,0],[2,10]],[[4,20]],[[7,0]]]);
assert.equal(geometry(history).empty,true);
assert.deepEqual(geometry(history).paths,['','','']);
history.series[0].values.splice(0,7,0,100,null,200,300,null,0);
history.series[1].values[1]=-100;
let chart=geometry(history);
assert.equal(chart.paths[0].match(/M/g).length,3,'missing buckets must start new SVG subpaths');
assert.equal(chart.paths[0].match(/L/g).length,2,'no line may cross a missing interval');
assert.equal(chart.paths[2],'','an entirely missing leg must not become a zero line');
assert.equal(chart.min,-100);
assert.equal(chart.max,300);
assert.ok(chart.paths[0].includes('l0.01,0'),'isolated measurements must remain visible');
for(const change of [h=>h.series[0].values.pop(),h=>h.series[0].values[0]=NaN,
  h=>h.series[0].values[0]=Infinity,h=>h.interval_seconds=3600,
  h=>h.end=h.start,h=>h.series.reverse()]) {
  const bad=structuredClone(history);change(bad);assert.throws(()=>geometry(bad));
}
history.series.forEach(row=>row.values.fill(0));
chart=geometry(history);
assert.equal(chart.empty,false,'recorded zero must not be classified as missing history');
assert.equal(chart.paths[0].match(/M/g).length,1);
assert.ok(!chart.paths.join('').includes('NaN'));
console.log('Mains chart gaps, isolated/zero/negative values, bounds and malformed payload checks passed');
