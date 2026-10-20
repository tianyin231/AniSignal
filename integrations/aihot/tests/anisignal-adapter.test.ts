import "./setup.ts";
import assert from "node:assert/strict";
import { after, test } from "node:test";
import { sql, closeDb } from "@aihot/backend/db";
import { publishArticle } from "@aihot/backend/publication/publish";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { buildApp } from "../apps/api/src/app.ts";
import { registerAdapter } from "../adapter/routes.ts";

process.env.ANISIGNAL_BRIDGE_TOKEN = "anisignal-adapter-fixture-0123456789abcdef";
const app = await buildApp();
registerAdapter(app);
const headers = { "x-anisignal-key": process.env.ANISIGNAL_BRIDGE_TOKEN };
const at = new Date(Date.now() - 60_000).toISOString();
const material = { source: { id: "anisignal-bilibili-1", name: "测试研究机构", tier: "T1", owner: "fixture-lab" },
  id: "as-1", url: "https://www.bilibili.com/video/BVfixture", title: "测试模型发布", text: "视频简介，不能当作视频全文。",
  platform: "bilibili", externalId: "BVfixture", author: "fixture", publishedAt: at, discoveredAt: at };

after(async () => { await app.close(); await stopBoss(); await closeDb(); });

test("the private bridge refuses missing and wrong credentials", async () => {
  assert.equal((await app.inject({ url: "/internal/anisignal/feed" })).statusCode, 401);
  assert.equal((await app.inject({ url: "/internal/anisignal/feed", headers: { "x-anisignal-key": "wrong" } })).statusCode, 401);
});

test("a video bridge keeps its excerpt, source ownership and idempotent revisions", async () => {
  const send = () => app.inject({ method: "POST", url: "/internal/anisignal/materials", headers, payload: material });
  assert.equal((await send()).statusCode, 200);
  assert.equal((await send()).statusCode, 200);
  const [a] = await sql`SELECT revision,excerpt,body_text,body_status FROM articles WHERE id='as-1'`;
  assert.equal(a.revision, 1);
  assert.equal(a.excerpt, material.text);
  assert.equal(a.body_text, null);
  assert.equal(a.body_status, "none");
  const [s] = await sql`SELECT tier, owner_entity_id FROM sources WHERE id=${material.source.id}`;
  assert.equal(s.tier, "T1"); assert.equal(s.owner_entity_id, "fixture-lab");
});

test("native publication and withdrawal govern both the site and queued bot snapshots", async () => {
  await sql`INSERT INTO analyses (article_id,input_revision,origin,relevance,category,title_zh,summary_zh,reason_zh,score,selected)
    VALUES ('as-1',1,'rule','pass','ai-models','测试模型发布','模型已公开测试。','一手发布',90,true)`;
  await sql`UPDATE articles SET grouping_status='complete' WHERE id='as-1'`;
  await publishArticle("as-1", { releasedAt: new Date(Date.now() - 60_000) });
  const feed = await app.inject({ url: "/internal/anisignal/feed?selected=true", headers });
  assert.equal(feed.statusCode, 200); assert.equal(feed.json().items[0].id, "as-1");
  const [p] = await sql`SELECT revision FROM publications WHERE article_id='as-1'`;
  const validate = () => app.inject({ method: "POST", url: "/internal/anisignal/bot/validate", headers,
    payload: [{ id: "as-1", revision: p.revision }] });
  assert.deepEqual((await validate()).json(), ["as-1"]);
  const changed = await app.inject({ method: "POST", url: "/internal/anisignal/materials/as-1/visibility", headers, payload: { hidden: true } });
  assert.equal(changed.statusCode, 200);
  assert.deepEqual((await validate()).json(), []);
  assert.equal((await app.inject({ url: "/api/site/items/as-1" })).statusCode, 404);
  assert.equal((await app.inject({ url: "/internal/anisignal/feed?selected=true", headers })).json().total, 0);
});

test("an imported historical report can never become a bot catch-up push", async () => {
  const archived = { ...material, id: "as-2", url: "https://www.bilibili.com/video/BVhistory", backfill: "anisignal-initial-import" };
  assert.equal((await app.inject({ method: "POST", url: "/internal/anisignal/materials", headers, payload: archived })).statusCode, 200);
  await sql`INSERT INTO analyses (article_id,input_revision,origin,relevance,category,title_zh,summary_zh,score,selected)
    VALUES ('as-2',1,'rule','pass','ai-models','历史发布','历史摘要',90,true)`;
  await sql`UPDATE articles SET grouping_status='complete' WHERE id='as-2'`;
  await publishArticle("as-2", { releasedAt: new Date(Date.now() - 60_000) });
  const [p] = await sql`SELECT revision FROM publications WHERE article_id='as-2'`;
  const result = await app.inject({ method: "POST", url: "/internal/anisignal/bot/validate", headers, payload: [{ id: "as-2", revision: p.revision }] });
  assert.deepEqual(result.json(), []);
  const daily = await app.inject({ method: "POST", url: "/internal/anisignal/bot/validate", headers,
    payload: [{ id: "as-2", revision: p.revision, allowBackfill: true }] });
  assert.deepEqual(daily.json(), ["as-2"]);
});

test("a truncated X post is never labelled as a confirmed full body", async () => {
  const partial = { ...material, id: "as-3", url: "https://x.com/fixture/status/123",
    platform: "x", bodyComplete: false, text: "Only the collected beginning." };
  assert.equal((await app.inject({ method: "POST", url: "/internal/anisignal/materials", headers, payload: partial })).statusCode, 200);
  const [article] = await sql`SELECT body_status,body_text FROM articles WHERE id='as-3'`;
  assert.equal(article.body_status, "unconfirmed");
  assert.equal(article.body_text, partial.text);
});
