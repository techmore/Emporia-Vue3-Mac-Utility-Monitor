import { readFile, writeFile } from 'node:fs/promises';
const sources = ['vendor/LICENSE', 'node_modules/three/LICENSE'];
const notices = await Promise.all(sources.map(path => readFile(new URL(path, import.meta.url), 'utf8')));
await writeFile(new URL('../../static/house-control/LICENSES.txt', import.meta.url),
  'house-control (MIT) - upstream commit 1ad70a26197c0503b5af7932f0b5c8a8d3c7a6f9\n\n'
  + notices[0] + '\n\nThree.js (MIT)\n\n' + notices[1]);
