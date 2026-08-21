import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const frontendPort = Number(process.env.SCORESHEET_FRONTEND_PORT ?? 5173);
const backendPort = Number(process.env.SCORESHEET_BACKEND_PORT ?? 8000);

export default defineConfig({
  plugins: [react()],
  server: {
    port: frontendPort,
    proxy: {
      '/api': `http://127.0.0.1:${backendPort}`,
    },
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      thresholds: {
        statements: 65,
        branches: 60,
        functions: 65,
        lines: 70,
      },
    },
  },
});
