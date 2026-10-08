const fs=require('fs'),vm=require('vm'),assert=require('assert');
const code=fs.readFileSync('static/mobile-navigation.js','utf8');
function run(dx,dy,blocked=false,mobile=true){
 const listeners={}, links=['/circuits','/aqara','/radon','/mitsubishi','/kasa'].map(pathname=>({pathname,href:pathname})); let target=null;
 const ctx={document:{querySelector:()=>({querySelectorAll:()=>links}),addEventListener:(n,f)=>listeners[n]=f},location:{pathname:'/aqara',assign:x=>target=x},matchMedia:()=>({matches:mobile}),innerWidth:390,Date,addEventListener:()=>{}};
 vm.runInNewContext(code,ctx);
 listeners.touchstart({touches:[{clientX:190,clientY:200}],target:{closest:()=>blocked}});
 listeners.touchend({changedTouches:[{clientX:190+dx,clientY:200+dy}]});return target;
}
assert.equal(run(-120,5),'/radon');assert.equal(run(120,5),'/circuits');
assert.equal(run(-120,5,true),null);assert.equal(run(-120,150),null);
assert.equal(run(-120,5,false,false),null);assert.equal(run(-30,0),null);
console.log('6 mobile gesture checks passed');
