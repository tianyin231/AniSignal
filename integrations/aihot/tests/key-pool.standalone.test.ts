import assert from "node:assert/strict";
import http from "node:http";
import { test } from "node:test";
import { fetchWithKeyPool, poolKeys } from "../packages/backend/src/providers/key-pool.ts";

test("key-level refusal rotates, successful calls spread across keys, and cooling avoids repeated refusal", async () => {
  const hits: string[] = [];
  const server = http.createServer((req, res) => {
    const auth = req.headers.authorization ?? "";
    hits.push(auth);
    res.writeHead(auth === "Bearer blocked-fixture" ? 429 : 200, { "content-type": "application/json" });
    res.end('{"choices":[{"message":{"content":"正常"}}]}');
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const keys = poolKeys("blocked-fixture", "active-fixture,second-fixture,active-fixture");
    assert.equal(keys.length, 3);
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/chat/completions`;
    for (let i = 0; i < 3; i++) assert.equal((await fetchWithKeyPool(url, { model: "fixture" }, keys, 1000)).status, 200);
    assert.deepEqual(hits, ["Bearer blocked-fixture", "Bearer active-fixture", "Bearer active-fixture", "Bearer second-fixture"]);
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
});

test("a network failure never sends the same potentially billed request to another key", async () => {
  let hits = 0;
  const server = http.createServer((req) => { hits++; req.socket.destroy(); });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/chat/completions`;
    await assert.rejects(fetchWithKeyPool(url, {}, ["network-fixture-1", "network-fixture-2"], 1000));
    assert.equal(hits, 1);
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
});
