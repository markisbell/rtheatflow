import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API + WebSocket to the rtheatflow backend so the browser
// only ever talks to the Vite origin (no CORS dance in dev).
// Use 127.0.0.1 (not "localhost") so Windows doesn't try IPv6 ::1 first, which
// uvicorn (IPv4-only by default) refuses (SPEC §9.3).
// Sibling port scheme: rtheatflow uses 8001/5174 so it can run NEXT TO
// netzsim/rtpowerflow (8000/5173) on the same machine. strictPort keeps Vite
// from silently hopping to another port and proxying into the WRONG backend.
const BACKEND = process.env.RTHEATFLOW_BACKEND ?? "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": {
        target: BACKEND,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      "/ws": { target: BACKEND.replace(/^http/, "ws"), ws: true },
    },
  },
});
