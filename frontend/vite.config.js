import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,          // Port frontendu
    open: true,          // Otworzy się automatycznie w przeglądarce
    proxy: {
      "/api": {
        target: "http://localhost:8000",  // Twój backend FastAPI
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
