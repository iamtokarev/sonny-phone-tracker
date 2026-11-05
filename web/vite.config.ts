import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In dev the page comes from Vite on :5173 and Sonny's routes from Sonny on :8042 (spec §3).
const toSonny = { target: "http://127.0.0.1:8042", ws: true };

// Sonny's base class serves static/index.html at "/" and the whole folder at "/static" (spec §2, §3).
export default defineConfig({
  plugins: [react()],
  base: "/static/",
  build: {
    outDir: "../src/sonny/static",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/rpc": toSonny, "/ws": toSonny, "/api": toSonny },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
