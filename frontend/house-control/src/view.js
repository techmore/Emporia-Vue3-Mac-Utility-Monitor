import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { normalize } from '../vendor/config.js';
import { buildHouse } from '../vendor/three/house.js';
import { buildFurniture } from '../vendor/three/furniture.js';
import { sensorKey, sensorReading } from './sensors.js';
import { levelsFor, itemLevel, planForLevel, stairTreads, floorTiles } from './levels.js';
import { temperatureBand } from './temperatures.js';

export function createView(canvas, labelContainer, plan, onPick) {
  const levels = levelsFor(plan);
  const config = normalize(plan);
  const css = getComputedStyle(canvas);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(css.getPropertyValue('--surface').trim());
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 2000);
  const direction = new THREE.Vector3(Math.sin(config.camera.azimuth * Math.PI / 180), 0,
                                     Math.cos(config.camera.azimuth * Math.PI / 180));
  const models=levels.map(level=>{
    const raw=planForLevel(plan,level.id);
    if(!raw.rooms.length)return {level,group:new THREE.Group(),floors:[]};
    const normalized=normalize(raw),house=buildHouse(normalized,direction);
    if(level.elevation>levels[0].elevation){
      const plinth=house.group.children[0];house.group.remove(plinth);
      plinth.geometry.dispose();plinth.material.dispose();
    }
    const openings=(plan.stairs || []).filter(stair=>stair.toLevel===level.id);
    const cutouts=(plan.accesses || []).filter(access=>access.toLevel===level.id).map(access=>access.rect);
    if(openings.length || cutouts.length)for(const floor of house.floors){
      const room=raw.rooms.find(room=>room.id===floor.userData.id);
      const pieces=floorTiles(room.rect,openings,cutouts).map(([x,z,w,d])=>{
        const geometry=new THREE.PlaneGeometry(w,d);geometry.rotateX(-Math.PI/2);
        geometry.translate(x+w/2,0,z+d/2);
        const pos=geometry.attributes.position,uv=geometry.attributes.uv;
        for(let i=0;i<pos.count;i++)uv.setXY(i,pos.getX(i),pos.getZ(i));
        return geometry;
      });
      floor.geometry.dispose();floor.geometry=pieces.length?mergeGeometries(pieces):new THREE.BufferGeometry();
      pieces.forEach(piece=>piece.dispose());
    }
    const group=new THREE.Group();group.position.y=level.elevation;
    group.add(house.group,buildFurniture(normalized.furniture));scene.add(group);
    return {level,group,floors:house.floors};
  });
  const flights=(plan.stairs || []).map(stair=>{
    const group=new THREE.Group();
    for(const tread of stairTreads(stair,levels)){
      const mesh=new THREE.Mesh(new THREE.BoxGeometry(tread.horizontal?tread.run:tread.width,
        .1,tread.horizontal?tread.width:tread.run),
        new THREE.MeshStandardMaterial({color:css.getPropertyValue('--olive-600').trim()}));
      mesh.position.set(tread.x,tread.y-.05,tread.z);group.add(mesh);
    }
    scene.add(group);return {stair,group};
  });
  const house={floors:models.flatMap(model=>model.floors)};
  const accesses=(plan.accesses || []).map(access=>{
    const group=new THREE.Group(),[x,z,w,d]=access.rect;
    const elevation=levels.find(level=>level.id===access.toLevel).elevation;
    const material=new THREE.MeshStandardMaterial({color:css.getPropertyValue('--olive-600').trim()});
    for(const [px,pz,pw,pd] of [[x+w/2,z,w,.06],[x+w/2,z+d,w,.06],[x,z+d/2,.06,d],[x+w,z+d/2,.06,d]]){
      const frame=new THREE.Mesh(new THREE.BoxGeometry(pw,.06,pd),material);
      frame.position.set(px,elevation+.03,pz);group.add(frame);
    }
    scene.add(group);return {access,group,elevation};
  });
  scene.add(new THREE.HemisphereLight(0xffffff, 0x555544, 2.3));
  const sun = new THREE.DirectionalLight(0xffffff, 2);
  sun.position.set(8, 18, 10);
  scene.add(sun);
  const controls = new OrbitControls(camera, canvas);
  controls.enablePan = false;
  controls.minPolarAngle = Math.PI * 0.12;
  controls.maxPolarAngle = Math.PI * 0.41;
  controls.minAzimuthAngle = config.camera.azimuth * Math.PI / 180 - Math.PI / 4;
  controls.maxAzimuthAngle = config.camera.azimuth * Math.PI / 180 + Math.PI / 4;
  const { minX, minZ, maxX, maxZ } = config.bounds;
  const span = Math.max(maxX - minX, maxZ - minZ);
  controls.minDistance = span * 0.65;
  controls.maxDistance = span * 5;
  const bounds = new THREE.Box3();
  for(const model of models)bounds.union(new THREE.Box3().setFromObject(model.group));
  const sphere = bounds.getBoundingSphere(new THREE.Sphere());
  const center = sphere.center;
  const labels = config.rooms.map(room => {
    const node = document.createElement('span');
    node.className = 'home3d-label';
    labelContainer.append(node);
    const level=itemLevel(plan.rooms.find(item=>item.id===room.id),plan);
    const elevation=levels.find(item=>item.id===level).elevation;
    return { id: room.id, level, node, point: new THREE.Vector3(room.x + room.w / 2, elevation+0.3, room.z + room.d / 2) };
  });
  const levelLabels=models.filter(model=>model.floors.length).map(({level})=>{
    const node=document.createElement('span');node.className='home3d-label';labelContainer.append(node);
    node.textContent=level.name;
    const rooms=planForLevel(plan,level.id).rooms;
    return {node,onlyAll:true,point:new THREE.Vector3(Math.min(...rooms.map(room=>room.rect[0]))-.5,
      level.elevation+.3,Math.min(...rooms.map(room=>room.rect[1])))};
  });
  const floors = new Map(house.floors.map(floor => {
    floor.material = floor.material.clone();
    floor.userData.baseColor=floor.material.color.clone();
    return [floor.userData.id, floor];
  }));
  const sensorLabels=new Map();
  const poles=new Map();
  const accessLabels=accesses.map(({access,elevation})=>{
    const node=document.createElement('span');node.className='home3d-label';node.textContent=access.name;labelContainer.append(node);
    return {node,levels:[access.level,access.toLevel],point:new THREE.Vector3(access.rect[0]+access.rect[2]/2,elevation+.1,access.rect[1]+access.rect[3]/2)};
  });
  let disposed = false, selectedLevel='all';
  function render() {
    if (disposed || document.hidden) return;
    renderer.render(scene, camera);
    const rect = canvas.getBoundingClientRect();
    for (const label of [...labels,...levelLabels,...accessLabels,...sensorLabels.values()]) {
      const point = label.point.clone().project(camera);
      label.node.hidden = (label.levels ? selectedLevel!=='all' && !label.levels.includes(selectedLevel) : label.onlyAll ? selectedLevel!=='all' :
        labels.includes(label) && selectedLevel==='all' || selectedLevel!=='all' && label.level!==selectedLevel)
        || point.z < -1 || point.z > 1;
      label.node.style.left = `${(point.x + 1) * rect.width / 2}px`;
      label.node.style.top = `${(1 - point.y) * rect.height / 2}px`;
    }
  }
  function reset() {
    controls.target.copy(center);
    const elevation = config.camera.elevation * Math.PI / 180;
    const halfVertical = camera.fov * Math.PI / 360;
    const halfHorizontal = Math.atan(Math.tan(halfVertical) * camera.aspect);
    const distance = sphere.radius / Math.sin(Math.min(halfVertical, halfHorizontal)) * 1.06;
    controls.maxDistance = Math.max(span * 5, distance * 1.5);
    camera.position.copy(center).add(new THREE.Vector3(direction.x * Math.cos(elevation),
      Math.sin(elevation), direction.z * Math.cos(elevation)).multiplyScalar(distance));
    controls.update();
    render();
  }
  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / rect.height;
    camera.updateProjectionMatrix();
    reset();
  }
  controls.addEventListener('change', render);
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  const ray = new THREE.Raycaster();
  let down;
  const pointerDown = event => { down = [event.clientX, event.clientY]; };
  const pointerUp = event => {
    if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
    const rect = canvas.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1,
      1 - (event.clientY - rect.top) / rect.height * 2), camera);
    const hit = ray.intersectObjects(models.filter(model=>model.group.visible).flatMap(model=>model.floors), false)[0];
    if (hit) onPick(hit.object.userData.id);
  };
  canvas.addEventListener('pointerdown', pointerDown);
  canvas.addEventListener('pointerup', pointerUp);
  document.addEventListener('visibilitychange', render);
  resize();
  return {
    reset,
    setLevel(id) {
      selectedLevel=id;
      for(const model of models)model.group.visible=id==='all' || model.level.id===id;
      for(const flight of flights)flight.group.visible=id==='all' || flight.stair.level===id || flight.stair.toLevel===id;
      for(const {access,group} of accesses)group.visible=id==='all' || access.level===id || access.toLevel===id;
      bounds.makeEmpty();
      for(const model of models.filter(model=>model.group.visible))bounds.union(new THREE.Box3().setFromObject(model.group));
      if(!bounds.isEmpty()){bounds.getBoundingSphere(sphere);center.copy(sphere.center);}
      reset();
    },
    update(rooms, metric, format, sensors=[]) {
      const placed=sensors.filter(row=>row.placement);
      const keys=new Set(placed.map(sensorKey));
      for(const [key,pole] of poles)if(!keys.has(key)){pole.removeFromParent();pole.geometry.dispose();pole.material.dispose();poles.delete(key);}
      for(const [key,label] of sensorLabels){if(!keys.has(key)){label.node.remove();sensorLabels.delete(key);}}
      for(const row of placed){
        const key=sensorKey(row);
        if(!sensorLabels.has(key)){
          const node=document.createElement('span');node.className='home3d-label home3d-sensor-marker';
          labelContainer.append(node);sensorLabels.set(key,{node,point:new THREE.Vector3()});
        }
        const label=sensorLabels.get(key);
        const room=plan.rooms.find(room=>room.id===row.placement.room_id);
        label.level=room ? itemLevel(room,plan) : null;
        const elevation=levels.find(level=>level.id===label.level)?.elevation || 0;
        label.point.set(row.placement.position[0],elevation+(row.placement.height ?? .65),row.placement.position[1]);
        if(row.placement.mount==='pole-base'){
          if(!poles.has(key))poles.set(key,new THREE.Mesh(new THREE.CylinderGeometry(.035,.035,plan.wallHeight,8),new THREE.MeshStandardMaterial({color:css.getPropertyValue('--text-light').trim()})));
          const pole=poles.get(key);models.find(model=>model.level.id===label.level)?.group.add(pole);
          pole.position.set(row.placement.position[0],plan.wallHeight/2,row.placement.position[1]);
        }else if(poles.has(key)){const pole=poles.get(key);pole.removeFromParent();pole.geometry.dispose();pole.material.dispose();poles.delete(key);}
        label.node.dataset.sensorSource=row.source;
        label.node.classList.toggle('stale',row.stale);
        label.node.textContent=`${row.name} · ${sensorReading(row)}${row.placement.mount==='pole-base'?' · pole base':''}${row.stale?' · stale':''}`;
      }
      const maximum = Math.max(1, ...rooms.map(room => Math.max(0, room[metric] ?? 0)));
      for (const label of labels) {
        const room = rooms.find(row => row.id === label.id);
        if (!room) continue;
        label.node.textContent = `${room.name} · ${format(room[metric], metric)}`;
        const material = floors.get(label.id).material;
        material.color.copy(floors.get(label.id).userData.baseColor);
        if(metric==='temperature_f'){
          const band=temperatureBand(room.temperature_f);
          const color=band?css.getPropertyValue(`--temperature-${band}`).trim():css.getPropertyValue('--text-light').trim();
          material.emissive.set(color);material.emissiveIntensity=band ? .55 : 0;
          if(band)material.color.set(color);
          continue;
        }
        material.emissive.set(css.getPropertyValue('--olive-600').trim());
        material.emissiveIntensity = room[metric] == null ? 0 : 0.12 + 0.5 * Math.max(0, room[metric]) / maximum;
      }
      render();
    },
    dispose() {
      disposed = true;
      observer.disconnect();
      controls.dispose();
      document.removeEventListener('visibilitychange', render);
      canvas.removeEventListener('pointerdown', pointerDown);
      canvas.removeEventListener('pointerup', pointerUp);
      const geometries = new Set(), materials = new Set(), textures = new Set();
      scene.traverse(node => {
        if (node.geometry) geometries.add(node.geometry);
        for (const material of node.material ? (Array.isArray(node.material) ? node.material : [node.material]) : []) {
          materials.add(material);
          if (material.map) textures.add(material.map);
        }
      });
      for (const geometry of geometries) geometry.dispose();
      for (const material of materials) material.dispose();
      for (const texture of textures) texture.dispose();
      renderer.dispose();
      labelContainer.replaceChildren();
    },
  };
}
