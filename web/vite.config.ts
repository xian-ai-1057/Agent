import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/**
 * KaTeX 的 CSS 每種字型都列了 woff2、woff、ttf 三種檔案，Vite 會全部
 * 複製出來（約 800KB、40 個檔案）。支援的瀏覽器都讀 woff2，所以在 Vite
 * 處理 url() 之前把 woff 與 ttf 的項目拿掉。
 */
function katexWoff2Only(): Plugin {
  return {
    name: "katex-woff2-only",
    enforce: "pre",
    transform(code, id) {
      const file = id.split("?")[0] ?? id;
      if (!/[\\/]katex(\.min)?\.css$/.test(file)) return null;
      return {
        code: code.replace(
          /,\s*url\([^)]*\.(?:woff|ttf)\)\s*format\(["'](?:woff|truetype)["']\)/g,
          "",
        ),
        map: null,
      };
    },
  };
}

// 開發時：先在專案資料夾執行 `harness web`，再執行 `npm run dev:web`，
// Vite 會把 /api 轉給 harness web（預設 4321 埠）。
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [katexWoff2Only(), react()],
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
