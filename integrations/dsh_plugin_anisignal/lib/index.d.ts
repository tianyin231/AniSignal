export declare const name = 'anisignal';

export interface AniSignalPatchConfig {
  base_url?: string;
  tools_key?: string;
  card_font_path?: string;
  qqbot_app_id?: string;
  qqbot_app_secret?: string;
  qqbot_markdown?: boolean;
  onebot_http_url?: string;
  onebot_ws_url?: string;
  onebot_access_token?: string;
  targets?: Array<Record<string, unknown>>;
}

export declare class AniSignalService {
  constructor(ctx: unknown, initialPatchConfig?: AniSignalPatchConfig, options?: Record<string, unknown>);
  start(): void;
  stop(): void;
  refreshCycle(): Promise<void>;
  consumeOutboxOnce(): Promise<{ status: 'accepted' | 'failed' | 'unknown'; error: string } | null>;
  buildViewState(): Promise<Record<string, unknown>>;
}

export declare function apply(ctx: unknown, patchConfig?: AniSignalPatchConfig): void;

declare const plugin: {
  name: typeof name;
  apply: typeof apply;
};
export default plugin;
