import { defineConfig } from 'vite';
export default defineConfig({
  build: {
    target: 'safari16',
    outDir: '../../static/house-control',
    emptyOutDir: true,
    lib: { entry: 'src/main.js', formats: ['es'], fileName: () => 'home-3d.js' },
  },
});
