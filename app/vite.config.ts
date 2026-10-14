import { defineConfig } from "vite";

export default defineConfig({
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: {
    target: "es2022",
    outDir: "dist",
    // Two pages: the edge strip and the full-screen effects overlay (ADR-059).
    rollupOptions: { input: { main: "index.html", fx: "fx.html" } },
  },
  test: { environment: "node" },
});
