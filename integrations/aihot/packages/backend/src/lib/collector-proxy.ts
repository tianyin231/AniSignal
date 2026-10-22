// 每次读取取一份出口顺序，默认直连；重定向保持本次选中的出口。
import { config } from "../config.ts";

export async function collectionRoutes(): Promise<(string | null)[]> {
  if (!config.collectorProxyUrl) return [null];
  const token = process.env.ANISIGNAL_BRIDGE_TOKEN;
  if (!token) throw new Error("采集代理配置缺少桥接认证");
  const response = await fetch(config.collectorProxyUrl, {
    method: "POST", headers: { "x-anisignal-key": token }, signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`采集代理配置不可用（HTTP ${response.status}）`);
  const body = await response.json() as { routes?: unknown };
  if (!Array.isArray(body.routes) || !body.routes.length || body.routes.some(url => url !== null &&
    (typeof url !== "string" || !URL.canParse(url) || !["http:", "https:", "socks5:"].includes(new URL(url).protocol))))
    throw new Error("采集代理配置格式无效");
  return body.routes;
}
