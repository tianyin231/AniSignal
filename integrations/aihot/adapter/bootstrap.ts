// 只初始化一次；后续启动保留管理员在原版后台保存的预算和信源配置。
import { sql, closeDb } from "@aihot/backend/db";
import { updateBudget } from "@aihot/backend/admin/settings";
import { updateSource } from "@aihot/backend/admin/sources";

const [done] = await sql`SELECT 1 FROM settings WHERE key='anisignal:initialized'`;
if (!done) {
  // 新装原版的默认预算很高，平台默认延续当前的调用上限。
  if (!(await sql`SELECT 1 FROM receipt_attempts LIMIT 1`).length)
    await updateBudget("llm", { perMinute: 12, perHour: 100, perDay: 300, reason: "平台初始调用上限" }, "anisignal-deploy");
  const sources = await sql`SELECT id,config,updated_at FROM sources WHERE kind='rss' AND cursor IS NULL`;
  for (const source of sources) await updateSource(source.id, {
    version: source.updated_at.toISOString(),
    patch: { config: { ...source.config, _aihot: { ...source.config._aihot, initialBackfillLimit: 1 } } },
    reason: "首次每源导入一条，日常采集范围保持完整",
  }, "anisignal-deploy");
  await sql`INSERT INTO settings (key,value,updated_by) VALUES ('anisignal:initialized','true'::jsonb,'anisignal-deploy') ON CONFLICT DO NOTHING`;
}
await closeDb();
