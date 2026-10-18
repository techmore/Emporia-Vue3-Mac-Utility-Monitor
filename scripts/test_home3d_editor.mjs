import assert from 'node:assert/strict';
import './test_mains_chart.mjs';
import { drawnRoom, moveItem, resizeRoom, retainRoomBindings, clampSensor, relocateSensors, furnitureDimensions } from '../frontend/house-control/src/plan-edit.js';
import { sensorKey, sensorReading } from '../frontend/house-control/src/sensors.js';
import { levelsFor, planForLevel, visibleRoomIds, stairTreads, floorTiles } from '../frontend/house-control/src/levels.js';
import {temperatureBand,roomTemperatures,historicalSensors} from '../frontend/house-control/src/temperatures.js';
assert.deepEqual([69.9,70,75,75.1,77.9,78,null].map(temperatureBand),['cold','comfortable','comfortable','warm','warm','hot',null]);
const temperatures=roomTemperatures([{id:'a'},{id:'unknown'}],[
  {placement:{room_id:'a'},stale:false,metrics:{temperature_f:70}},
  {placement:{room_id:'a'},stale:false,metrics:{temperature_f:74}},
  {placement:{room_id:'a'},stale:true,metrics:{temperature_f:100}}]);
assert.equal(temperatures[0].temperature_f,72);assert.equal(temperatures[1].temperature_f,null);
assert.equal(historicalSensors({times:['a','b'],series:[{values:[0,null]}]},0)[0].metrics.temperature_f,0);
assert.equal(historicalSensors({times:['a','b'],series:[{values:[0,null]}]},1)[0].stale,true);
const hatchTiles=floorTiles([0,0,10,10],[],[[4,4,1,1]]);
assert.equal(hatchTiles.reduce((sum,[,,w,d])=>sum+w*d,0),99);
console.log('Temperature thresholds, stale/missing rooms, historical gaps and attic hatch opening passed');
const stacked={levels:[{id:'first',name:'First floor',elevation:0},{id:'upstairs',name:'Upstairs',elevation:2.9}],
  rooms:[{id:'lower',rect:[0,0,10,9]},{id:'upper',level:'upstairs',rect:[0,0,10,9]}],
  furniture:[{type:'sofa',pos:[1,1],level:'upstairs'}],
  openings:[{type:'door',from:[0,0],to:[1,0]}]};
assert.deepEqual(planForLevel(stacked,'first').rooms.map(room=>room.id),['lower']);
assert.deepEqual(planForLevel(stacked,'upstairs').rooms[0].rect,stacked.rooms[0].rect);
assert.equal(planForLevel(stacked,'upstairs').furniture.length,1);
assert.equal(planForLevel(stacked,'upstairs').openings.length,0);
assert.deepEqual([...visibleRoomIds(stacked,'upstairs')],['upper']);
assert.equal(visibleRoomIds(stacked,'all').size,2);
assert.equal(levelsFor({rooms:[]})[0].elevation,0);
const stair={path:[[8,6],[8,8],[4,8]],width:1,steps:14,level:'first',toLevel:'upstairs'};
const treads=stairTreads(stair,stacked.levels);
assert.equal(treads.length,14);assert.equal(treads.at(-1).y,2.9);
assert.ok(treads.every((tread,i)=>tread.y>0 && (!i || tread.y>treads[i-1].y)));
const tiles=floorTiles([0,0,10,9],[stair]);
assert.ok(tiles.reduce((area,[,,w,d])=>area+w*d,0)<90);
assert.ok(!tiles.some(([x,z,w,d])=>x<8 && x+w>8 && z<7 && z+d>7));
assert.deepEqual(floorTiles([0,0,2,2],[]),[[0,0,2,2]]);
console.log('Stacked levels isolate geometry, rooms and stairs; upper-floor cutouts preserve the opening');
const room = drawnRoom([4,4],[1.1,1.2],'r');
assert.deepEqual(room.rect,[1,1.25,3,2.75]);
const plan={rooms:[room],furniture:[{pos:[1,1]}],openings:[{from:[99,0],to:[100,0]}]};
moveItem(plan,'rooms',0,.3,-.3);
assert.deepEqual(room.rect,[1.25,1,3,2.75]);
resizeRoom(room,-5,100);
assert.deepEqual(room.rect,[1.25,1,.5,50]);
moveItem(plan,'openings',0,50,1.2);
assert.deepEqual(plan.openings[0],{from:[99,1.25],to:[100,1.25]});
assert.deepEqual(retainRoomBindings([{room_id:'r'},{room_id:'deleted'}],plan),[{room_id:'r'}]);
console.log('Editor drag, resize, snapping, bounds and mapping preservation checks passed');
const placements=[{room_id:'r',position:[2,3]},{room_id:'other',position:[1,1]}];
relocateSensors(placements,'r',[0,0,5,5],{rect:[10,10,1,1]});
assert.deepEqual(placements[0].position,[11,11]);
assert.deepEqual(placements[1].position,[1,1]);
assert.deepEqual(clampSensor([-999,999],{rect:[-100,100,.5,50]}),[-100,150]);
assert.notEqual(sensorKey({source:'a',sensor_id:'x'}),sensorKey({source:'b',sensor_id:'x'}));
assert.equal(sensorReading({metrics:{temperature_f:32,humidity_pct:0,radon_pci_l:0}}),'32.0 °F · 0.0 % RH · 0.0 pCi/L');
assert.equal(sensorReading({metrics:{temperature_f:null}}),'No recorded values');
console.log('Sensor relocation, bounds, namespaced identities and zero/missing measurement checks passed');
assert.deepEqual(furnitureDimensions({type:'sofa'}),[['width',2.2]]);
assert.deepEqual(furnitureDimensions({type:'wardrobe',width:2}),[['width',2],['height',2.1]]);
assert.deepEqual(furnitureDimensions({type:'plant'}),[['height',1.2]]);
assert.deepEqual(furnitureDimensions({type:'armchair',width:9,height:9}),[]);
console.log('Furniture inspector fields match renderer-supported dimensions and defaults');
