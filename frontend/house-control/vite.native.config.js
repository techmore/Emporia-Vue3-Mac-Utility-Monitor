import { defineConfig } from 'vite';
export default defineConfig({build: {
  target:'safari16', outDir:'../../static/house-control', emptyOutDir:false,
  lib:{entry:'src/native-preview.js',formats:['iife'],name:'NativeHouse',fileName:()=> 'native-house.js'},
}});
