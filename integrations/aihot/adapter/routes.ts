// 平台适配只负责认证、数据入口和运维接口；编辑与发布决策始终调用上游实现。
import { timingSafeEqual } from "node:crypto";
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { sql } from "@aihot/backend/db";
import { upsertMaterial } from "@aihot/backend/content/materials";
import { queueProcessing } from "@aihot/backend/jobs/content";
import { createSource, listSources, sourceDetail, updateSource, fetchNow } from "@aihot/backend/admin/sources";
import { setVisibility, searchContent, contentChain, overrideFields, rerun } from "@aihot/backend/admin/content";
import { modelsOverview, switchModel } from "@aihot/backend/admin/models";
import { listBudgets, updateBudget } from "@aihot/backend/admin/settings";
import { runsOverview } from "@aihot/backend/admin/runs";
import { ITEM_COLUMNS, ITEM_FROM, toFeedItemSummary, type ItemRow } from "@aihot/backend/publication/items";
import { listedCondition, seatedCondition } from "@aihot/backend/publication/scope";
import { TOPICS, topicMembership } from "@aihot/backend/publication/topics";

const sourceSchema = z.object({
  id: z.string().regex(/^anisignal-[a-z0-9-]+$/).max(80), name: z.string().min(1).max(200),
  tier: z.enum(["T1", "T1_5", "T2"]).default("T2"),
  owner: z.string().max(120).nullable().default(null),
});
const materialSchema = z.object({
  source: sourceSchema, id: z.string().regex(/^as-[0-9]+$/),
  url: z.url().refine(v => /^https?:\/\//.test(v)), title: z.string().max(2000),
  text: z.string().max(200000).default(""), author: z.string().nullable().default(null),
  publishedAt: z.iso.datetime().nullable(), discoveredAt: z.iso.datetime(),
  platformCollectedAt: z.iso.datetime().optional(),
  platform: z.string(), externalId: z.string(), hidden: z.boolean().default(false),
  media: z.array(z.object({ kind: z.enum(["image", "video"]), url: z.url() })).max(30).default([]),
  backfill: z.string().nullable().default(null),
});

export function registerAdapter(app: FastifyInstance) {
  app.register(async internal => {
    internal.addHook("onRequest", async (req, reply) => {
      const wanted = Buffer.from(process.env.ANISIGNAL_BRIDGE_TOKEN ?? "");
      const actual = Buffer.from(String(req.headers["x-anisignal-key"] ?? ""));
      if (wanted.length < 32 || actual.length !== wanted.length || !timingSafeEqual(actual, wanted))
        return reply.code(401).send({ error: "unauthorized" });
      reply.header("Cache-Control", "no-store");
    });
    internal.post("/materials", async req => {
      const input = materialSchema.parse(req.body);
      const detail = await sourceDetail(input.source.id);
      // 外部桥接源由平台管理身份；不让定时采集器重复调用 X 等付费服务。
      if (!detail) await createSource({ id: input.source.id, name: input.source.name, kind: "external",
        config: {}, tier: input.source.tier, site_fulltext: false, syndicate_fulltext: false }, "anisignal-bridge");
      const current = (await sourceDetail(input.source.id))!.source;
      if (current.name !== input.source.name || current.tier !== input.source.tier || current.owner_entity_id !== input.source.owner)
        await updateSource(input.source.id, { version: new Date(current.updated_at).toISOString(), reason: "同步平台来源身份",
          patch: { name: input.source.name, tier: input.source.tier, owner_entity_id: input.source.owner } }, "anisignal-bridge");
      const isX = input.platform === "x";
      const result = await upsertMaterial({
        id: input.id, sourceId: input.source.id, url: input.url, title: input.title,
        author: input.author, publishedAt: input.publishedAt ? new Date(input.publishedAt) : null,
        discoveredAt: new Date(input.discoveredAt), via: "anisignal", backfill: input.backfill,
        // B 站视频简介是 excerpt，不能冒充已提取的全文。
        ...(isX ? { bodyText: input.text, bodyStatus: "ok" as const,
          xPost: { tweetId: input.externalId, authorName: input.source.name,
            handle: input.author ?? "", text: input.text, media: input.media } }
          : { excerpt: input.text, bodyStatus: "none" as const }),
        media: input.media, raw: { platform: input.platform, externalId: input.externalId, platformCollectedAt: input.platformCollectedAt },
      });
      if (input.hidden) await changeVisibility(result.articleId, true);
      if (result.created || result.revised || result.processingNeeded) await queueProcessing(result.articleId);
      return result;
    });
    internal.get("/feed", async req => {
      const q = z.object({ selected: z.enum(["true", "false"]).default("false"),
        hours: z.coerce.number().int().min(1).max(720).default(168),
        limit: z.coerce.number().int().min(1).max(200).default(20),
        offset: z.coerce.number().int().min(0).default(0), q: z.string().max(100).default(""),
        topic: z.string().max(60).default("") }).parse(req.query);
      const now = new Date();
      const scope = q.selected === "true" ? seatedCondition(now) : listedCondition(now);
      const topic = TOPICS.find(t => t.name === q.topic || t.slug === q.topic);
      const where = sql`${scope} AND p.timeline_at >= ${new Date(now.getTime() - q.hours * 3600000)}
        AND (${!q.q} OR p.search_text ILIKE ${`%${q.q}%`})
        AND (${!q.topic} OR ${topic?.slug ?? q.topic} = ANY(${topicMembership(topic ? [topic] : [])}))`;
      const [rows, [counts]] = await Promise.all([
        sql<(ItemRow & { platform: string | null })[]>`SELECT ${ITEM_COLUMNS}, a.raw->>'platform' AS platform ${ITEM_FROM} WHERE ${where}
          ORDER BY p.sort_at DESC, p.article_id DESC LIMIT ${q.limit} OFFSET ${q.offset}`,
        sql`SELECT count(*)::int AS total FROM publications p WHERE ${where}`,
      ]);
      return { items: rows.map(r => ({ ...toFeedItemSummary(r), platform: r.platform })), total: counts.total,
        topics: TOPICS.map(t => t.name) };
    });
    internal.get("/bot/items", async req => {
      const q = z.object({ start: z.iso.datetime(), end: z.iso.datetime(),
        after: z.iso.datetime().optional(), cutover: z.iso.datetime().optional() }).parse(req.query);
      return sql`SELECT p.article_id AS id, p.title, p.original_title, p.summary, p.url,
        p.published_at, p.discovered_at, p.updated_at, p.revision, p.fact_id,
        p.channel, p.first_party, s.name AS source_name
        FROM publications p JOIN sources s ON s.id = p.source_id
        WHERE ${seatedCondition(new Date())} AND NOT p.backfill
          AND p.published_at >= ${new Date(q.start)} AND p.published_at < ${new Date(q.end)}
          AND p.updated_at < ${new Date(q.end)}
          AND (${q.after ?? null}::timestamptz IS NULL OR p.updated_at > ${q.after ?? null}::timestamptz)
          AND (${q.cutover ?? null}::timestamptz IS NULL OR p.published_at >= ${q.cutover ?? null}::timestamptz)
        ORDER BY p.published_at DESC, p.article_id LIMIT 200`;
    });
    internal.post("/bot/validate", async req => {
      const items = z.array(z.object({ id: z.string(), revision: z.number().int() })).max(200).parse(req.body);
      if (!items.length) return [];
      const rows = await sql`SELECT p.article_id AS id, p.revision FROM publications p
        WHERE p.article_id IN ${sql(items.map(i => i.id))} AND ${seatedCondition(new Date())} AND NOT p.backfill`;
      return items.filter(i => rows.some(r => r.id === i.id && r.revision === i.revision)).map(i => i.id);
    });
    internal.post<{ Params: { id: string } }>("/materials/:id/visibility", async req => {
      const { hidden } = z.object({ hidden: z.boolean() }).parse(req.body);
      return { ok: true, result: await changeVisibility(req.params.id, hidden) };
    });
    internal.get("/overview", async () => {
      const [runs, models, budgets, sources] = await Promise.all([
        runsOverview(), modelsOverview(), listBudgets(), listSources({}),
      ]);
      return { runs, models, budgets, sources, upstream: process.env.AIHOT_RELEASE };
    });
    internal.get("/sources", async req => listSources(req.query as never));
    internal.get("/content", async req => ({ rows: await searchContent(String((req.query as { q?: string }).q ?? "")) }));
    internal.get<{ Params: { id: string } }>("/content/:id", async req => contentChain(req.params.id));
    internal.post<{ Params: { id: string } }>("/content/:id/visibility", async req => setVisibility(req.params.id, req.body as never, "anisignal-admin"));
    internal.post<{ Params: { id: string } }>("/content/:id/override", async req => overrideFields(req.params.id, req.body as never, "anisignal-admin"));
    internal.post<{ Params: { id: string } }>("/content/:id/rerun", async req => {
      const body = z.object({ step: z.enum(["extract", "analyze", "group"]), requestId: z.string().min(1).max(120) }).parse(req.body);
      return rerun(req.params.id, body.step, body.requestId, "anisignal-admin");
    });
    internal.post("/sources", async req => createSource(req.body, "anisignal-admin"));
    internal.get<{ Params: { id: string } }>("/sources/:id", async req => sourceDetail(req.params.id));
    internal.patch<{ Params: { id: string } }>("/sources/:id", async req => updateSource(req.params.id, req.body as never, "anisignal-admin"));
    internal.post<{ Params: { id: string } }>("/sources/:id/fetch", async req => fetchNow(req.params.id, "anisignal-admin"));
    internal.put<{ Params: { service: string } }>("/budgets/:service", async req => updateBudget(req.params.service, req.body as never, "anisignal-admin"));
    internal.put<{ Params: { capability: string } }>("/models/:capability", async req => {
      const body = z.object({ model: z.string().nullable(), reason: z.string().min(1) }).parse(req.body);
      return switchModel(req.params.capability, body.model, body.reason, "anisignal-admin");
    });
  }, { prefix: "/internal/anisignal" });
}

async function changeVisibility(id: string, hidden: boolean) {
  const [article] = await sql`SELECT a.id, coalesce(o.version, 0) AS version FROM articles a
    LEFT JOIN editorial_overrides o ON o.article_id = a.id WHERE a.id = ${id}`;
  if (!article) return null;
  return setVisibility(id, { visibility: hidden ? "withdrawn" : "public", version: article.version,
    reason: hidden ? "平台人工下架或删除" : "平台人工恢复" }, "anisignal-admin");
}
