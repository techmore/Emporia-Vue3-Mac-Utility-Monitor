const assert = require('node:assert/strict');
const simulate = require('../static/cooling-model.js');
const defaults = {initial:25,outdoor:30,target:22,loss:60,capacity:2,power:700,cop:3,gains:150};
const close = (a,b) => assert.ok(Math.abs(a-b)<1e-9, `${a} != ${b}`);
const noPower = simulate({...defaults,power:0});
close(noPower.kwh,0); close(noPower.cooled,noPower.passive);
// Analytic passive solution at 24 hours, including constant internal gain.
const equilibrium = defaults.outdoor+defaults.gains/defaults.loss;
close(noPower.passive,equilibrium+(defaults.initial-equilibrium)*Math.exp(-.06*24/2));
const balanced = simulate({...defaults,initial:22,outdoor:22,gains:0});
close(balanced.cooled,22); close(balanced.kwh,0);
const hot = simulate(defaults);
assert.ok(hot.cooled >= defaults.target-1e-9 && hot.cooled < hot.passive);
assert.ok(hot.kwh>0 && hot.kwh <= .7*24);
const cold = simulate({...defaults,initial:18,outdoor:10,gains:0});
close(cold.kwh,0); close(cold.cooled,cold.passive);
assert.throws(()=>simulate({...defaults,capacity:0}));
assert.throws(()=>simulate({...defaults,outdoor:NaN}));
console.log('Cooling model: 6 scenario and validation checks passed');
