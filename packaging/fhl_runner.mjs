import { spawnSync } from "node:child_process";
import { join } from "node:path";

const scriptPath = join(import.meta.dirname, "generate.mjs");
const args = process.argv.slice(2);

const result = spawnSync(process.execPath, [scriptPath, ...args], {
  env: process.env,
  stdio: "inherit",
  windowsHide: true,
});
process.exit(result.status ?? 1);
