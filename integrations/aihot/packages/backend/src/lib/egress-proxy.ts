// Resolve abroad for outbound proxy traffic, then CONNECT to the checked address. Local getaddrinfo
// can spend 20 s on a blocked name and occupy the lookup pool for unrelated images. Letting the proxy
// resolve an unchecked name instead would allow DNS rebinding into this host or its private network.
import net from "node:net";
import tls from "node:tls";
import { SocksClient } from "socks";
import { Agent, Client, ProxyAgent, request, type Dispatcher } from "undici";
import { isBlockedAddress } from "./url.ts";

// HTTP/2 idle-socket cleanup can emit an unhandled stream error after a request has finished.
// Apply the same protocol restriction to destinations, DNS-over-HTTPS and TLS proxy connections.
export const OUTBOUND_HTTP_OPTIONS = { allowH2: false } as const;

type Resolve = (hostname: string) => Promise<string[]>;
type ConnectOptions<T> = Omit<Dispatcher.ConnectOptions<T>, "origin">;
type ConnectCallback<T> = (error: Error | null, data: Dispatcher.ConnectData<T>) => void;

class PinnedProxyClient extends Client {
  private readonly resolve: Resolve;
  constructor(origin: URL, options: object, resolve: Resolve) {
    super(origin, options);
    this.resolve = resolve;
  }

  override connect<T = null>(options: ConnectOptions<T>): Promise<Dispatcher.ConnectData<T>>;
  override connect<T = null>(options: ConnectOptions<T>, callback: ConnectCallback<T>): void;
  override connect<T = null>(options: ConnectOptions<T>, callback?: ConnectCallback<T>): Promise<Dispatcher.ConnectData<T>> | void {
    const work = this.connectPublic(options);
    if (!callback) return work;
    void work.then((data) => callback(null, data), (error: Error) => callback(error, undefined as never));
  }

  private async connectPublic<T>(options: ConnectOptions<T>): Promise<Dispatcher.ConnectData<T>> {
    const target = new URL(`http://${options.path}`);
    const host = target.hostname.replace(/^\[|\]$/g, "");
    const addresses = net.isIP(host) ? [host] : await this.resolve(host);
    if (!addresses.length || addresses.some(isBlockedAddress)) throw new Error(`Blocked proxy address for ${host}`);
    const address = addresses.find((a) => net.isIP(a) === 4) ?? addresses[0]!;
    // Only the CONNECT authority changes: ProxyAgent still uses the original host for TLS/SNI and
    // the HTTP Host header. The proxy never resolves the untrusted hostname a second time.
    const authority = `${net.isIP(address) === 6 ? `[${address}]` : address}:${target.port || 80}`;
    return super.connect({ ...options, path: authority, headers: { ...options.headers, host: authority } });
  }
}

function socksAgent(proxyUrl: string, resolve?: Resolve, factory?: Agent.Options["factory"]): Agent {
  const proxy = new URL(proxyUrl);
  return new Agent({ ...OUTBOUND_HTTP_OPTIONS, factory, connect: async (options, callback) => {
    try {
      const host = options.hostname.replace(/^\[|\]$/g, "");
      let destination = host;
      if (resolve) {
        const addresses = net.isIP(host) ? [host] : await resolve(host);
        if (!addresses.length || addresses.some(isBlockedAddress)) throw new Error("Blocked proxy destination");
        destination = addresses.find(address => net.isIP(address) === 4) ?? addresses[0]!;
      }
      const { socket } = await SocksClient.createConnection({
        command: "connect", timeout: 5000,
        proxy: { host: proxy.hostname.replace(/^\[|\]$/g, ""), port: Number(proxy.port || 1080), type: 5,
          userId: decodeURIComponent(proxy.username), password: decodeURIComponent(proxy.password) },
        destination: { host: destination, port: Number(options.port || (options.protocol === "https:" ? 443 : 80)) },
      });
      if (options.protocol !== "https:") { callback(null, socket); return; }
      const secure = tls.connect({ socket, servername: options.servername || (net.isIP(host) ? undefined : host), ALPNProtocols: ["http/1.1"] });
      const failed = (error: Error) => { callback(error, null); };
      secure.once("error", failed);
      secure.setTimeout(5000, () => secure.destroy(new Error("SOCKS5 TLS handshake timeout")));
      secure.once("secureConnect", () => {
        secure.setTimeout(0);
        secure.removeListener("error", failed);
        callback(null, secure);
      });
    } catch {
      // SocksClientError 带完整代理 options，不能把原异常交给日志系统。
      callback(new Error("SOCKS5 proxy connection failed"), null);
    }
  } });
}

export function createEgressProxy(proxyUrl: string, resolve: Resolve, factory?: Agent.Options["factory"]): ProxyAgent | Agent {
  if (new URL(proxyUrl).protocol === "socks5:") return socksAgent(proxyUrl, resolve, factory);
  return new ProxyAgent({ uri: proxyUrl, ...OUTBOUND_HTTP_OPTIONS, proxyTls: OUTBOUND_HTTP_OPTIONS,
    proxyTunnel: true, factory, clientFactory: (origin, options) => new PinnedProxyClient(origin, options, resolve) });
}

/** The fixed DNS service is reached through the same outbound proxy, never the system resolver. */
export function createEgressResolver(proxyUrl: string): Resolve {
  // SOCKS 的无 resolver 分支仅用于下方固定的 Cloudflare DNS 地址。
  const dns = new URL(proxyUrl).protocol === "socks5:" ? socksAgent(proxyUrl)
    : new ProxyAgent({ uri: proxyUrl, ...OUTBOUND_HTTP_OPTIONS, proxyTls: OUTBOUND_HTTP_OPTIONS, maxResponseSize: 64 * 1024 });
  const cache = new Map<string, { addresses: string[]; until: number }>();
  const pending = new Map<string, Promise<string[]>>();
  return (host) => {
    const cached = cache.get(host);
    if (cached && cached.until > Date.now()) return Promise.resolve(cached.addresses);
    let work = pending.get(host);
    if (!work) {
      work = (async () => {
        const signal = AbortSignal.timeout(5_000);
        const records = (await Promise.all(["A", "AAAA"].map(async (type) => {
          const url = new URL("https://cloudflare-dns.com/dns-query");
          url.search = new URLSearchParams({ name: host, type }).toString();
          const res = await request(url, { dispatcher: dns, signal, headers: { accept: "application/dns-json" } });
          if (res.statusCode !== 200) { await res.body.dump(); throw new Error(`DNS upstream ${res.statusCode}`); }
          const data = await res.body.json() as { Status?: number; Answer?: Array<{ type: number; data: string; TTL: number }> };
          if (data.Status !== 0) throw new Error(`DNS lookup failed for ${host}`);
          return (data.Answer ?? []).filter((a) => a.type === 1 || a.type === 28);
        }))).flat();
        const addresses = records.map((r) => r.data);
        if (!addresses.length || addresses.some(isBlockedAddress)) throw new Error(`Blocked proxy address for ${host}`);
        cache.delete(host);
        if (cache.size >= 256) cache.delete(cache.keys().next().value!);
        cache.set(host, { addresses, until: Date.now() + Math.max(0, Math.min(60, ...records.map((r) => r.TTL || 0))) * 1000 });
        return addresses;
      })().finally(() => pending.delete(host));
      pending.set(host, work);
    }
    return work;
  };
}
