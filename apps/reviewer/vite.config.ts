/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the API runs on 127.0.0.1:8000 under /v1; the browser talks to /api/v1 exactly as in production (spec §22B).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api/v1": { target: "http://127.0.0.1:8000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api\/v1/, "/v1") },
    },
  },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", include: ["src/**/*.test.ts", "src/**/*.test.tsx"], globals: true },
});
