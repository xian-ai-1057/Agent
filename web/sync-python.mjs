// 把打包好的前端複製進 Python 套件（python/src/harness/web/static）。
//
// Python 版用 pip 從 git 安裝時拿不到 Node.js，沒辦法自己打包前端，所以
// 打包結果要跟著 Python 原始碼一起進版控。`npm run build` 會自動執行這裡。
import { cpSync, existsSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";

const from = fileURLToPath(new URL("../dist/public", import.meta.url));
const to = fileURLToPath(
  new URL("../python/src/harness/web/static", import.meta.url),
);

if (!existsSync(from)) {
  throw new Error(`找不到 ${from}，請先執行 vite build`);
}
// 先清掉舊檔，避免留下檔名帶舊雜湊的 assets。
rmSync(to, { recursive: true, force: true });
cpSync(from, to, { recursive: true });
console.log(`已複製前端到 ${to}`);
