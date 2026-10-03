import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/files": "http://localhost:8000",
      "/cache": "http://localhost:8000",
      "/music": "http://localhost:8000",
    },
  },
});
