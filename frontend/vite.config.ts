import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    plugins: [react()],
    server: {
      // If 5177 is busy, Vite can fall back to another free port; /api still proxies to the backend.
      port: 5177,
      strictPort: false,
      proxy: {
        "/api": {
          target: env.VITE_API_PROXY || "http://127.0.0.1:8001",
          changeOrigin: true,
        },
      },
    },
  };
});
