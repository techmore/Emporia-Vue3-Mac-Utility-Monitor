import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { normalize } from '../vendor/config.js';
import { buildHouse } from '../vendor/three/house.js';
import { buildFurniture } from '../vendor/three/furniture.js';

export function createView(canvas, labelContainer, plan, onPick) {
  const config = normalize(plan);
  const css = getComputedStyle(document.documentElement);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(css.getPropertyValue('--surface').trim());
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 2000);
  const direction = new THREE.Vector3(Math.sin(config.camera.azimuth * Math.PI / 180), 0,
                                     Math.cos(config.camera.azimuth * Math.PI / 180));
  const house = buildHouse(config, direction);
  const furniture = buildFurniture(config.furniture);
  scene.add(house.group, furniture);
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
  const bounds = new THREE.Box3().setFromObject(house.group);
  bounds.union(new THREE.Box3().setFromObject(furniture));
  const sphere = bounds.getBoundingSphere(new THREE.Sphere());
  const center = sphere.center;
  const labels = config.rooms.map(room => {
    const node = document.createElement('span');
    node.className = 'home3d-label';
    labelContainer.append(node);
    return { id: room.id, node, point: new THREE.Vector3(room.x + room.w / 2, 0.3, room.z + room.d / 2) };
  });
  const floors = new Map(house.floors.map(floor => {
    floor.material = floor.material.clone();
    return [floor.userData.id, floor];
  }));
  let disposed = false;
  function render() {
    if (disposed || document.hidden) return;
    renderer.render(scene, camera);
    const rect = canvas.getBoundingClientRect();
    for (const label of labels) {
      const point = label.point.clone().project(camera);
      label.node.hidden = point.z < -1 || point.z > 1;
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
    const hit = ray.intersectObjects(house.floors, false)[0];
    if (hit) onPick(hit.object.userData.id);
  };
  canvas.addEventListener('pointerdown', pointerDown);
  canvas.addEventListener('pointerup', pointerUp);
  document.addEventListener('visibilitychange', render);
  resize();
  return {
    reset,
    update(rooms, metric, format) {
      const maximum = Math.max(1, ...rooms.map(room => Math.max(0, room[metric] ?? 0)));
      for (const label of labels) {
        const room = rooms.find(row => row.id === label.id);
        if (!room) continue;
        label.node.textContent = `${room.name} · ${format(room[metric], metric)}`;
        const material = floors.get(label.id).material;
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
