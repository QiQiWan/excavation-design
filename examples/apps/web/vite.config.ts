import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id: string) => {
          if (id.includes('/node_modules/three/')) return 'three';
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/')) return 'react';
          if (id.includes('/node_modules/zustand/')) return 'state';
          return undefined;
        }
      }
    }
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts'
  }
});
