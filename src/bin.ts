#!/usr/bin/env node
import { main } from "./cli.ts";

const code = await main();
// 等 stdout 寫完再結束，避免輸出被截斷；直接結束是因為模型連線的
// keep-alive 會讓行程多掛幾秒。
process.stdout.write("", () => process.exit(code));
