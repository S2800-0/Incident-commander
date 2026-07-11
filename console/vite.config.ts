import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy API + WebSocket to the FastAPI server on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/incidents": "http://localhost:8000",
      "/harness": "http://localhost:8000",
      "/investigate": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
