// 信源只初始化一次；模型调用默认不加平台配额，保留供应商自身的限流重试。
import { sql, closeDb } from "@aihot/backend/db";
import { audit } from "@aihot/backend/audit";
import { updateSource } from "@aihot/backend/admin/sources";

const [unlimited] = await sql`SELECT 1 FROM settings WHERE key='anisignal:unlimited-llm'`;
if (!unlimited) await sql.begin(async tx => {
  // budgets 无记录即不限额；0 的语义是停用，不能用 0 代替不限额。
  const before = await tx`DELETE FROM budgets WHERE service='llm' RETURNING *`;
  await audit("anisignal-deploy", "budget.remove", "budget:llm", "按用户要求取消模型调用配额", before, null, { db: tx });
  await tx`INSERT INTO settings (key,value,updated_by) VALUES ('anisignal:unlimited-llm','true'::jsonb,'anisignal-deploy') ON CONFLICT DO NOTHING`;
});

const [done] = await sql`SELECT 1 FROM settings WHERE key='anisignal:initialized'`;
if (!done) {
  const sources = await sql`SELECT id,config,updated_at FROM sources WHERE kind='rss' AND cursor IS NULL`;
  for (const source of sources) await updateSource(source.id, {
    version: source.updated_at.toISOString(),
    patch: { config: { ...source.config, _aihot: { ...source.config._aihot, initialBackfillLimit: 1 } } },
    reason: "首次每源导入一条，日常采集范围保持完整",
  }, "anisignal-deploy");
  await sql`INSERT INTO settings (key,value,updated_by) VALUES ('anisignal:initialized','true'::jsonb,'anisignal-deploy') ON CONFLICT DO NOTHING`;
}
await closeDb();
