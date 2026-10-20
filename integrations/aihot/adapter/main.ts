import { assertProductionSecrets, config } from "@aihot/backend/config";
import { closeDb } from "@aihot/backend/db";
import { installModules } from "@aihot/backend/modules";
import { SERVER_MODULES } from "@aihot/site/modules/server";
import { startHeartbeat } from "@aihot/backend/operations/heartbeat";
import { startWorkerWatchdog } from "@aihot/backend/operations/watch";
import { buildApp } from "../apps/api/src/app.ts";
import { registerAdapter } from "./routes.ts";

installModules(SERVER_MODULES);
assertProductionSecrets([["auth", "SESSION_SECRET"], ["auth", "IMG_PROXY_SIGN_SECRET"]]);
if ((process.env.ANISIGNAL_BRIDGE_TOKEN?.length ?? 0) < 32) throw new Error("ANISIGNAL_BRIDGE_TOKEN is required");
if (config.environmentName === "production" && (config.adminPassword?.length ?? 0) < 12) throw new Error("ADMIN_PASSWORD is required");
const app = await buildApp();
registerAdapter(app);
await app.listen({ port: config.apiPort, host: process.env.API_HOST || "127.0.0.1" });
startHeartbeat(`api:${config.apiPort}`);
startWorkerWatchdog();
let stopping = false;
async function shutdown() {
  if (stopping) return;
  stopping = true;
  await app.close();
  await closeDb();
  process.exit(0);
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
