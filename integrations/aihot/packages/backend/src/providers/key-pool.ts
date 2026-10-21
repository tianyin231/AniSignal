// 密钥级拒绝可以换下一把；网络超时的计费结果不确定，交给 receipt 恢复，不能直接重发。
const ROTATE_STATUSES = new Set([401, 402, 403, 429]);
const positions = new Map<string, number>();
const cooling = new Map<string, number>();

export function poolKeys(primary: string | null, extra: string | null): string[] {
  return [...new Set([primary ?? "", ...(extra ?? "").split(/[\s,]+/)].map(k => k.trim()).filter(Boolean))];
}

export async function fetchWithKeyPool(url: string, body: Record<string, unknown>, keys: string[], timeoutMs: number) {
  const identity = keys.join(",");
  const start = positions.get(identity) ?? 0;
  positions.set(identity, (start + 1) % keys.length);
  const ordered = keys.map((_, i) => keys[(start + i) % keys.length]!)
    .filter(key => (cooling.get(key) ?? 0) <= Date.now());
  if (!ordered.length) return { status: 429, text: '{"error":"模型密钥池正在冷却"}', requestId: null };
  const deadline = Date.now() + timeoutMs;
  for (let i = 0; i < ordered.length; i++) {
    const response = await fetch(url, {
      method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${ordered[i]}` },
      body: JSON.stringify(body), signal: AbortSignal.timeout(Math.max(1, deadline - Date.now())),
    });
    const result = { status: response.status, text: await response.text(), requestId: response.headers.get("x-request-id") };
    if (!ROTATE_STATUSES.has(response.status)) return result;
    cooling.set(ordered[i]!, Date.now() + 60_000);
    if (i === ordered.length - 1 || Date.now() >= deadline) return result;
  }
  throw new Error("模型密钥池为空");
}
