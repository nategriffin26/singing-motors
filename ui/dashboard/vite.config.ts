/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendTarget = process.env.VITE_BACKEND_URL ?? 'http://localhost:8765';

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
      '/ws': {
        target: backendTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    exclude: ['dist/**', 'node_modules/**'],
  },
});
