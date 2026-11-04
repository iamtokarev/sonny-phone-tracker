import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Sonny's base class serves static/index.html at "/" and the whole folder at "/static" (spec §2, §3).
export default defineConfig({
  plugins: [react()],
  base: "/static/",
  build: {
    outDir: "../src/sonny/static",
    emptyOutDir: true,
  },
});
