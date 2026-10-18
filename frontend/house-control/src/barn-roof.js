import * as THREE from 'three';

export function gableProfile(bounds, wallHeight) {
  const {minX,maxX,minZ,maxZ}=bounds;
  const alongZ=maxZ-minZ>=maxX-minX;
  const width=alongZ?maxX-minX:maxZ-minZ;
  const rise=Math.max(.6,Math.min(2.5,width*.3));
  const section=(end)=>{
    const across=alongZ?[minX,(minX+maxX)/2,maxX]:[minZ,(minZ+maxZ)/2,maxZ];
    return across.map((a,i)=>alongZ?[a,wallHeight+(i===1?rise:0),end]:[end,wallHeight+(i===1?rise:0),a]);
  };
  return [...section(alongZ?minZ:minX),...section(alongZ?maxZ:maxX)];
}

export function buildBarnGable(bounds, wallHeight) {
  const points=gableProfile(bounds,wallHeight),group=new THREE.Group();
  group.name='barn-attic-gable';
  const vertices=points.flat();
  const mesh=(indices,color,opacity)=>{
    const geometry=new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));
    geometry.setIndex(indices);geometry.computeVertexNormals();
    return new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color,roughness:.9,side:THREE.DoubleSide,transparent:true,opacity,depthWrite:false}));
  };
  // Ghosted roof surfaces and end gables leave the attic and meters visible.
  group.add(mesh([0,3,4,0,4,1,1,4,5,1,5,2],'#7b806b',.15));
  group.add(mesh([0,1,2,3,5,4],'#d8d2bd',.20));
  const segments=[];
  const edge=(a,b)=>segments.push(...a,...b);
  for(const [a,b] of [[0,1],[1,2],[3,4],[4,5],[0,3],[1,4],[2,5]])edge(points[a],points[b]);
  for(let i=1;i<4;i++){
    const row=[0,1,2].map(j=>points[j].map((value,k)=>value+(points[j+3][k]-value)*i/4));
    edge(row[0],row[1]);edge(row[1],row[2]);
  }
  const framing=new THREE.BufferGeometry();framing.setAttribute('position',new THREE.Float32BufferAttribute(segments,3));
  group.add(new THREE.LineSegments(framing,new THREE.LineBasicMaterial({color:'#687259',transparent:true,opacity:.75})));
  return group;
}
