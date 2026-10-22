// 代理配置由平台统一管理；每个 HTTP 请求（含整条重定向链）只租用一次。
import { config } from "../config.ts";

export async function leaseCollectionProxy(): Promise<string | null> {
  if (!config.collectorProxyUrl) return null;
  const token = process.env.ANISIGNAL_BRIDGE_TOKEN;
  if (!token) throw new Error("采集代理配置缺少桥接认证");
  const response = await fetch(config.collectorProxyUrl, {
    method: "POST", headers: { "x-anisignal-key": token }, signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`采集代理配置不可用（HTTP ${response.status}）`);
  const body = await response.json() as { url?: unknown };
  if (body.url === null) return null;
  if (typeof body.url !== "string" || !["http:", "https:", "socks5:"].includes(new URL(body.url).protocol))
    throw new Error("采集代理配置格式无效");
  return body.url;
}
