import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 開發時：先在專案資料夾執行 `harness web`，再執行 `npm run dev:web`，
// Vite 會把 /api 轉給 harness web（預設 4321 埠）。
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL("../dist/public", import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": {
        target: process.env.HARNESS_WEB_URL ?? "http://127.0.0.1:4321",
        changeOrigin: true,
      },
    },
  },
});
