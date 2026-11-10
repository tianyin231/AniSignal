// AniSignal 设置视图：读取插件状态，按会话编辑订阅、通知、回复与权限，保存后由插件立即同步。
// 文案、默认值与可选项都取自插件实时 schema（含账号目录），本文件只负责布局与交互。

const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;

const TABS = [
  ["subscription", "订阅来源"],
  ["notifications", "自动通知"],
  ["presentation", "回复与模板"],
  ["interaction", "问答权限"],
];
const SCOPE_NOTES = {
  channels: "逐个分区挑选官方、社区与商品来源",
  legacy: "选中分区的全部平台都会推送",
  custom: "不看公共频道，只推送下方定制源与监控",
};
const MAIN_PLACEHOLDERS = ["{intro}", "{body}", "{items}", "{title}", "{summary}", "{content}", "{time}", "{source}", "{url}", "{images}", "{image_urls}", "{schedule}", "{details_hint}"];
const ITEM_PLACEHOLDERS = ["{index}", "{title}", "{summary}", "{content}", "{time}", "{source}", "{url}", "{images}", "{image_urls}", "{schedule}"];
const KIND_LABEL = { official: "官方", community: "社区", other: "其他" };
const UMO_RE = /^[^:]+:(FriendMessage|GroupMessage):[^:]+$/;
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

const S = {
  data: null,
  draft: null,
  baseline: "",
  selected: "connection",
  tab: "subscription",
  busy: false,
  showErrors: false,
  banner: null,
  stale: false,
  filter: "",
  open: new Map(),
  narrow: new Set(),
  armed: null,
  replacingKey: false,
};

const $ = (selector) => document.querySelector(selector);

/* ---------- DOM 工具 ---------- */

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") el.value = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

let toastTimer;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

/** 重绘时按 data-k 找回焦点与光标，避免点选后键盘焦点丢失。 */
function preserveFocus(fn) {
  const active = document.activeElement;
  const key = active?.dataset?.k;
  let range = null;
  try {
    if (active && typeof active.selectionStart === "number") range = [active.selectionStart, active.selectionEnd];
  } catch { /* 部分输入类型不支持选区 */ }
  const y = window.scrollY;
  fn();
  if (key) {
    const next = document.querySelector(`[data-k="${CSS.escape(key)}"]`);
    if (next) {
      next.focus({ preventScroll: true });
      if (range && next.setSelectionRange) {
        try { next.setSelectionRange(range[0], range[1]); } catch { /* 忽略 */ }
      }
    }
  }
  window.scrollTo(0, y);
}

/* ---------- schema 读取 ---------- */

const sessionSchema = () => S.data.schema.session;
const meta = (group, key) => (group ? sessionSchema()[group]?.items?.[key] : sessionSchema()[key]) || {};
const connMeta = (key) => S.data.schema.connection?.[key] || {};
const options = (m) => (m.options || []).map((value, i) => ({ value, label: (m.labels || [])[i] ?? String(value) }));

function communities() {
  return options(meta("subscription", "communities"));
}

function communityName(key) {
  if (key === "custom") return "定制信息源与监控";
  return communities().find((c) => c.value === key)?.label || key;
}

function channelGroups() {
  return Object.entries(sessionSchema().subscription.items)
    .filter(([field]) => field.startsWith("channel_"))
    .map(([field, m]) => {
      const key = field.slice(8);
      return { key, field, name: communityName(key) || (m.description || key).split(" · ")[0], options: options(m) };
    });
}

/** 频道键 <分区>:<official|community>:<平台>，商品与资讯等单项归入「其他」。 */
function sourceKind(value) {
  const kind = String(value).split(":")[1];
  return kind === "official" || kind === "community" ? kind : "other";
}

function shortLabel(label, { prefix = "", stripKind = true } = {}) {
  let text = String(label);
  if (prefix && text.startsWith(prefix + " · ")) text = text.slice(prefix.length + 3);
  if (stripKind) for (const kind of ["官方", "社区"]) if (text.startsWith(kind + " · ")) return text.slice(kind.length + 3);
  return text;
}

function fillTarget(target) {
  const out = { __template_key: "session", ...clone(target) };
  for (const [field, m] of Object.entries(sessionSchema())) {
    if (m.type === "object") {
      const group = out[field] && typeof out[field] === "object" ? out[field] : {};
      for (const [key, item] of Object.entries(m.items)) if (group[key] === undefined) group[key] = clone(item.default);
      out[field] = group;
    } else if (out[field] === undefined) {
      out[field] = clone(m.default);
    }
  }
  return out;
}

function parseUmo(umo) {
  const [platform, kind, id] = String(umo || "").split(":");
  if (!platform || !kind || !id) return null;
  return { platform, kind: kind === "GroupMessage" ? "群聊" : kind === "FriendMessage" ? "私聊" : kind, id };
}

/* ---------- 状态 ---------- */

const T = () => S.draft.targets[S.selected];
const isDirty = () => !!S.draft && JSON.stringify(S.draft) !== S.baseline;

function load(state, { keepSelection = false } = {}) {
  const previousUmo = typeof S.selected === "number" ? S.draft?.targets?.[S.selected]?.umo : null;
  S.data = state;
  S.draft = {
    base_url: state.connection.base_url || "",
    card_font_path: state.connection.card_font_path || "",
    tools_key: "",
    clear_tools_key: false,
    targets: (state.targets || []).map(fillTarget),
  };
  S.baseline = JSON.stringify(S.draft);
  S.stale = false;
  S.showErrors = false;
  S.replacingKey = false;
  S.armed = null;
  if (keepSelection && previousUmo != null) {
    const index = S.draft.targets.findIndex((t) => t.umo === previousUmo.trim());
    S.selected = index >= 0 ? index : S.draft.targets.length ? 0 : "connection";
  } else if (!keepSelection) {
    S.selected = state.connection.tools_key_set && S.draft.targets.length ? 0 : "connection";
  }
  if (typeof S.selected === "number" && S.selected >= S.draft.targets.length) S.selected = "connection";
  render();
}

function changed(rerender = true) {
  if (rerender) render();
  else {
    renderTop();
    renderRail();
  }
}

function setField(group, key, value, rerender = true) {
  const target = T();
  if (group) target[group][key] = value;
  else target[key] = value;
  changed(rerender);
}

function validate() {
  const errors = { connection: [], targets: {} };
  const draft = S.draft;
  if (!/^https?:\/\/[^\s/]+/.test(draft.base_url.trim())) errors.connection.push("服务地址应以 http:// 或 https:// 开头");
  const seen = new Map();
  draft.targets.forEach((t, index) => {
    const list = [];
    const name = (t.name || "").trim();
    const umo = (t.umo || "").trim();
    if (!name) list.push("缺少会话名称");
    else if (name.length > 100) list.push("会话名称超过 100 字");
    if (!UMO_RE.test(umo)) list.push("会话标识应形如 平台:GroupMessage:群号 或 平台:FriendMessage:用户");
    else if (seen.has(umo)) list.push(`会话标识与「${draft.targets[seen.get(umo)].name || "未命名会话"}」重复`);
    else seen.set(umo, index);
    const n = t.notifications;
    if (!TIME_RE.test(n.daily_time || "")) list.push("早报时间应为 HH:mm");
    if (!TIME_RE.test(n.calendar_time || "")) list.push("收尾提醒时间应为 HH:mm");
    if (!Number.isInteger(n.min_push_interval_minutes) || n.min_push_interval_minutes < 0) list.push("推送间隔应为不小于 0 的整数");
    for (const [key, label] of [["keywords", "关注关键词"], ["exclude_keywords", "排除关键词"]]) {
      const words = t.subscription[key] || [];
      if (words.length > 20) list.push(`${label}最多 20 个`);
      if (words.some((w) => w.length > 64)) list.push(`${label}每个不超过 64 字`);
    }
    if ((t.interaction.manager_ids || []).length > 20) list.push("管理用户最多 20 个");
    if (list.length) errors.targets[index] = list;
  });
  return errors;
}

const hasErrors = (errors) => errors.connection.length > 0 || Object.keys(errors.targets).length > 0;

/* ---------- 远端操作 ---------- */

async function fetchState() {
  return bridge.apiGet("settings");
}

async function save() {
  if (S.busy || !isDirty()) return;
  const errors = validate();
  S.showErrors = true;
  if (hasErrors(errors)) {
    S.banner = null;
    render();
    toast("还有需要修正的设置");
    return;
  }
  S.busy = true;
  renderTop();
  const config = {
    base_url: S.draft.base_url.trim(),
    card_font_path: S.draft.card_font_path.trim(),
    targets: S.draft.targets.map((t) => ({ ...t, name: t.name.trim(), umo: t.umo.trim() })),
  };
  if (S.draft.clear_tools_key) config.clear_tools_key = true;
  else if (S.draft.tools_key.trim()) config.tools_key = S.draft.tools_key.trim();
  try {
    const result = await bridge.apiPost("settings/save", { revision: S.data.revision, config });
    S.banner = null;
    S.busy = false;
    load(result, { keepSelection: true });
    if (result.sync?.state === "error") {
      S.banner = { kind: "warn", title: "已保存，但同步没有成功", text: result.sync.error };
      render();
      toast("已保存，同步未成功");
    } else {
      toast("已保存并同步到 AniSignal");
    }
  } catch (error) {
    S.busy = false;
    const message = error?.message || "未知错误";
    S.banner = { kind: "error", title: "保存失败", text: message, conflict: /别处修改/.test(message) };
    render();
  }
}

async function syncNow() {
  if (S.busy) return;
  S.busy = true;
  renderTop();
  try {
    const state = await bridge.apiPost("settings/sync", {});
    S.busy = false;
    applyLiveState(state);
    toast(state.sync?.state === "error" ? "同步未成功" : "已与 AniSignal 同步");
  } catch (error) {
    S.busy = false;
    renderTop();
    toast(`同步失败：${error?.message || "未知错误"}`);
  }
}

/** 轮询或手动同步得到的新状态：没有本地修改就整体刷新，否则只更新状态信息。 */
function applyLiveState(state) {
  if (!isDirty()) {
    load(state, { keepSelection: true });
    return;
  }
  Object.assign(S.data, {
    sync: state.sync,
    server_targets: state.server_targets,
    recent_sessions: state.recent_sessions,
    providers: state.providers,
    update: state.update,
  });
  if (state.revision !== S.data.revision) S.stale = true;
  render();
}

async function poll() {
  if (document.hidden || S.busy || !S.data) return;
  // 正在输入时不重绘，免得打断输入法或吞掉尚未成为标签的文字。
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "")) return;
  try {
    applyLiveState(await fetchState());
  } catch { /* 下一轮再试 */ }
}

/* ---------- 通用控件 ---------- */

function fieldRow(m, control, { inline = false, stack = false, label, hint, extra, error } = {}) {
  const text = hint === undefined ? m.hint : hint;
  return h("div", { class: `field${inline ? " inline" : ""}${stack ? " stack" : ""}` },
    stack ? null : h("div", { class: "label" }, label ?? m.description ?? ""),
    h("div", null,
      stack && (label ?? m.description) ? h("div", { class: "label", style: "margin-bottom:8px;font-weight:600;font-size:13px;color:var(--ink-2)" }, label ?? m.description) : null,
      control,
      text ? h("p", { class: "hint" }, text) : null,
      extra,
      error ? h("p", { class: "err-text" }, error) : null));
}

function switchControl(value, onChange, key, label) {
  return h("button", {
    type: "button", role: "switch", class: "switch", "aria-checked": String(!!value), "data-k": key,
    onClick: () => onChange(!value),
  }, h("span", { class: "track" }), label ? h("span", null, label) : null);
}

function segmented(m, value, onChange, key, { wide = false, notes = null } = {}) {
  return h("div", { class: `segmented${wide ? " wide" : ""}`, role: "radiogroup", "aria-label": m.description || "" },
    options(m).map((o) => h("button", {
      type: "button", role: "radio", "aria-checked": String(o.value === value), "data-k": `${key}:${o.value}`,
      onClick: () => o.value !== value && onChange(o.value),
    }, o.label, notes?.[o.value] ? h("small", null, notes[o.value]) : null)));
}

function toggled(list, value) {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

function chipSet(items, selected, onChange, key) {
  return h("div", { class: "chips" }, items.map((o) => h("button", {
    type: "button", class: `chip ${o.cls || ""}`, "aria-pressed": String(selected.includes(o.value)), "data-k": `${key}:${o.value}`,
    title: o.title || null,
    onClick: () => onChange(toggled(selected, o.value)),
  }, o.swatch ? h("span", { class: "swatch" }) : null, o.label)));
}

function tagInput(values, onChange, { key, placeholder, exclude = false }) {
  const input = h("input", { "data-k": key, placeholder, "aria-label": placeholder, enterkeyhint: "done" });
  const pending = () => input.value.split(/[,，、;；\n]/).map((s) => s.trim()).filter(Boolean);
  const merged = () => {
    const next = [...values];
    for (const part of pending()) if (!next.includes(part)) next.push(part);
    return next;
  };
  input.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter" || event.key === "," || event.key === "，") {
      event.preventDefault();
      const next = merged();
      input.value = "";
      if (next.length !== values.length) onChange(next);
    } else if (event.key === "Backspace" && !input.value && values.length) {
      onChange(values.slice(0, -1));
    }
  });
  // 失焦时只写入数据、就地补出标签，不整体重绘，免得吞掉用户正要点击的按钮。
  input.addEventListener("blur", () => {
    const parts = pending().filter((part) => !values.includes(part));
    if (!parts.length) return;
    values.push(...parts);
    input.value = "";
    for (const part of parts) input.before(tagChip(part, () => onChange(values.filter((v) => v !== part))));
    changed(false);
  });
  const box = h("div", {
    class: `tags${exclude ? " exclude" : ""}`,
    onClick: (event) => { if (event.target === box) input.focus(); },
  }, values.map((value) => tagChip(value, () => onChange(values.filter((v) => v !== value)))), input);
  return box;
}

function tagChip(value, onRemove) {
  return h("span", { class: "tag" }, value,
    h("button", { type: "button", "aria-label": `移除 ${value}`, onClick: onRemove }, "×"));
}

function textInput(value, onInput, { key, mono = false, placeholder = "", type = "text", invalid = false, cls = "" } = {}) {
  return h("input", {
    class: `input${mono ? " mono" : ""}${invalid ? " invalid" : ""} ${cls}`, type, value: value ?? "", placeholder, "data-k": key,
    spellcheck: "false", autocomplete: type === "password" ? "new-password" : "off",
    onInput: (event) => onInput(event.target.value, event.target),
  });
}

/* ---------- 顶栏与提示 ---------- */

function renderTop() {
  const pill = $("#sync-pill");
  const sync = S.data?.sync;
  pill.className = "sync-pill";
  if (!sync) {
    pill.textContent = "正在读取";
  } else if (sync.state === "error") {
    pill.classList.add("error");
    pill.textContent = `同步失败：${sync.error}`;
  } else if (sync.state === "synced") {
    pill.classList.add("synced");
    pill.textContent = `已同步${sync.synced_at ? " · " + sync.synced_at.split(" ").pop() : ""}`;
  } else {
    pill.classList.add("pending");
    pill.textContent = S.data.connection.tools_key_set ? "等待首次同步" : "待连接：填写用户 Token";
  }
  pill.title = pill.textContent;
  const dirty = isDirty();
  $("#dirty-note").hidden = !dirty;
  $("#discard-btn").hidden = !dirty || S.busy;
  $("#save-btn").disabled = !dirty || S.busy;
  $("#save-btn").textContent = S.busy ? "处理中…" : "保存并同步";
  $("#sync-btn").disabled = S.busy || !S.data;
  if (S.data) {
    const update = S.data.update || {};
    $("#version").replaceChildren(`会话与通知设置 · v${S.data.version}`,
      update.available ? h("button", {
        type: "button", class: "update-flag", title: "查看更新方式", onClick: () => select("connection"),
      }, `有新版本 v${update.latest}`) : "");
  }
}

function renderBanner() {
  const root = $("#banner");
  root.replaceChildren();
  if (S.stale) {
    root.append(h("div", { class: "banner warn" },
      h("div", null, h("strong", null, "设置已在别处修改"),
        "例如在聊天中改了订阅，或在 AstrBot 原生设置中保存过。现在保存会被拒绝；载入最新设置会放弃本页未保存的修改。"),
      h("button", { type: "button", class: "btn small", onClick: reloadLatest }, "载入最新设置")));
  }
  if (S.banner) {
    const b = S.banner;
    root.append(h("div", { class: `banner ${b.kind === "error" ? "" : b.kind}` },
      h("div", null, h("strong", null, b.title), b.text),
      b.conflict ? h("button", { type: "button", class: "btn small", onClick: reloadLatest }, "载入最新设置") : null,
      h("button", { type: "button", class: "btn small quiet", "aria-label": "关闭提示", onClick: () => { S.banner = null; renderBanner(); } }, "关闭")));
  }
  if (S.showErrors) {
    const errors = validate();
    const others = Object.keys(errors.targets).map(Number).filter((i) => i !== S.selected);
    const items = [
      ...(errors.connection.length && S.selected !== "connection" ? [["connection", "连接与账号"]] : []),
      ...others.map((i) => [i, S.draft.targets[i].name || `第 ${i + 1} 个会话`]),
    ];
    if (items.length) {
      root.append(h("div", { class: "banner" },
        h("div", null, h("strong", null, "以下页面还有需要修正的项"),
          h("div", { class: "chips", style: "margin-top:6px" }, items.map(([target, label]) =>
            h("button", { type: "button", class: "chip", onClick: () => select(target) }, label))))));
    }
  }
}

async function reloadLatest() {
  try {
    S.banner = null;
    load(await fetchState(), { keepSelection: true });
    toast("已载入最新设置");
  } catch (error) {
    toast(`读取失败：${error?.message || "未知错误"}`);
  }
}

/* ---------- 左侧导航 ---------- */

function select(target) {
  S.selected = target;
  S.armed = null;
  render();
  window.scrollTo({ top: 0 });
}

function renderRail() {
  const rail = $("#rail");
  if (!rail) return;
  const errors = validate();
  const sync = S.data.sync;
  const quota = sync.identity?.quota;
  const enabled = S.draft.targets.filter((t) => t.enabled).length;
  const overQuota = quota && enabled > quota.max_bot_targets;
  const user = sync.identity?.user;
  const connectionDot = S.showErrors && errors.connection.length ? "err" : sync.state === "error" ? "err" : sync.state === "synced" ? "on" : "warn";

  const connection = h("div", { class: "rail-group" },
    h("button", {
      type: "button", class: "rail-item", "aria-current": String(S.selected === "connection"),
      onClick: () => select("connection"),
    }, h("span", { class: "glyph" }, "⚙"),
    h("span", { class: "text" }, h("b", null, "连接与账号"), h("small", null, user ? `${user.display_name} · #${user.id}` : "尚未连接账号")),
    h("span", { class: `dot ${connectionDot}` })));

  const sessions = h("div", { class: "rail-group sessions" },
    h("div", { class: "rail-title" }, "会话",
      h("span", { title: quota ? `账号额度：最多启用 ${quota.max_bot_targets} 个会话` : null, style: overQuota ? "color:var(--hot)" : null },
        quota ? `启用 ${enabled}/${quota.max_bot_targets}` : `启用 ${enabled}/${S.draft.targets.length}`)),
    S.draft.targets.map((t, index) => {
      const parsed = parseUmo(t.umo);
      const name = (t.name || "").trim() || "未命名会话";
      const dot = errors.targets[index] ? "err" : t.enabled ? "on" : "";
      return h("button", {
        type: "button", class: "rail-item", "aria-current": String(S.selected === index), onClick: () => select(index),
        title: t.umo || null,
      }, h("span", { class: "glyph" }, name.slice(0, 1)),
      h("span", { class: "text" }, h("b", null, name),
        h("small", null, parsed ? `${parsed.kind} · ${parsed.id}` : "缺少会话标识")),
      h("span", { class: `dot ${dot}`, title: dot === "err" ? "有需要修正的项" : t.enabled ? "已启用" : "已暂停" }));
    }),
    h("button", { type: "button", class: "rail-item rail-add", onClick: () => addSession() }, "＋ 添加会话"));

  const recent = (S.data.recent_sessions || []).filter((row) => !S.draft.targets.some((t) => t.umo === row.umo));
  const recentGroup = recent.length ? h("div", { class: "rail-group recent" },
    h("div", { class: "rail-title" }, "最近有消息的会话", h("span", null, "未添加")),
    recent.slice(0, 8).map((row) => {
      const parsed = parseUmo(row.umo);
      return h("div", { class: "recent-item", title: row.umo },
        h("span", { class: "text" }, h("b", null, parsed ? `${parsed.kind} ${parsed.id}` : row.umo),
          h("small", null, `${parsed?.platform || ""} · ${relativeTime(row.last_active)}`)),
        h("button", {
          type: "button", class: "btn small",
          onClick: () => addSession({ umo: row.umo, name: parsed ? `${parsed.kind} ${parsed.id}` : "" }),
        }, "添加"));
    })) : null;

  rail.replaceChildren(connection, sessions, recentGroup || "");
}

function relativeTime(iso) {
  const at = Date.parse(iso);
  if (!at) return "";
  const minutes = Math.max(0, Math.round((Date.now() - at) / 60000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} 小时前`;
  return `${Math.round(minutes / 1440)} 天前`;
}

function addSession(preset = {}) {
  const target = fillTarget({ name: preset.name || "", umo: preset.umo || "", enabled: false });
  S.draft.targets.push(target);
  S.selected = S.draft.targets.length - 1;
  S.tab = "subscription";
  render();
  window.scrollTo({ top: 0 });
  document.querySelector(`[data-k="name"]`)?.focus();
}

/* ---------- 连接与账号 ---------- */

function renderConnection() {
  const sync = S.data.sync;
  const user = sync.identity?.user;
  const quota = sync.identity?.quota;
  const errors = S.showErrors ? validate().connection : [];

  // 只有会耗尽后影响推送的额度才变色；接入数与监控数用满是常态。
  const meter = (label, used, max, alarm = true) => {
    const ratio = max ? Math.min(1, used / max) : 0;
    return h("div", { class: `meter${alarm ? (ratio >= 1 ? " full" : ratio >= 0.8 ? " high" : "") : ""}` },
      h("span", null, label), h("b", null, used, h("small", null, ` / ${max}`)),
      h("div", { class: "track" }, h("i", { style: `width:${Math.round(ratio * 100)}%` })));
  };

  const account = h("section", { class: "card" }, h("div", { class: "card-body" },
    h("div", { class: "account" },
      h("div", { class: "avatar" }, user ? String(user.display_name || "?").slice(0, 1) : "?"),
      h("div", null,
        h("b", null, user ? `${user.display_name}` : sync.identity?.legacy ? "旧版插件接入" : "尚未连接 AniSignal 账号"),
        h("small", null, user ? `账号 #${user.id} · ` : "",
          sync.state === "error" ? `同步失败：${sync.error}` :
            sync.synced_at ? `上次同步 ${sync.synced_at}（北京时间）· 后台每 30 秒校验一次` :
              "填写用户 Token 并保存后自动加载额度和会话配置"))),
    quota ? h("div", { class: "meters" },
      meter("今日通知", quota.notifications_used, quota.daily_notifications),
      meter("启用会话", quota.bot_targets_used, quota.max_bot_targets),
      meter("Bot 接入", quota.bot_clients_used, quota.max_bot_clients, false),
      meter("自建监控", quota.custom_feeds_used, quota.max_feeds, false)) : null,
    quota ? h("p", { class: "hint" }, `自建监控采集间隔至少 ${quota.min_interval_minutes} 分钟；通知额度由邮件与 Bot 共用，失败的发送尝试也计入，北京时间零点重置。`) : null));

  const keyMeta = connMeta("tools_key");
  let keyControl;
  if (S.draft.clear_tools_key) {
    keyControl = h("div", { class: "key-state" }, h("span", { class: "badge err" }, "保存后清除"),
      h("button", { type: "button", class: "link-btn", onClick: () => { S.draft.clear_tools_key = false; changed(); } }, "撤销"));
  } else if (S.data.connection.tools_key_set && !S.replacingKey) {
    keyControl = h("div", { class: "key-state" }, h("span", { class: "badge ok" }, "已填写"),
      S.data.connection.tools_key_tail ? h("code", null, `····${S.data.connection.tools_key_tail}`) : null,
      h("button", { type: "button", class: "btn small", "data-k": "replace-key", onClick: () => { S.replacingKey = true; render(); $('[data-k="tools_key"]')?.focus(); } }, "更换"),
      h("button", { type: "button", class: "btn small danger", onClick: () => { S.draft.clear_tools_key = true; S.draft.tools_key = ""; changed(); } }, "清除"));
  } else {
    keyControl = h("div", { class: "inline-row" },
      h("div", { style: "flex:1;min-width:220px" }, textInput(S.draft.tools_key, (v) => { S.draft.tools_key = v; changed(false); },
        { key: "tools_key", type: "password", mono: true, placeholder: "粘贴 AniSignal 用户 Token" })),
      S.data.connection.tools_key_set ? h("button", { type: "button", class: "btn small quiet", onClick: () => { S.replacingKey = false; S.draft.tools_key = ""; changed(); } }, "取消") : null);
  }

  const settings = h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", null, h("h2", null, "连接"), h("p", null, "保存后立即同步；修改服务地址或 Token 会重新加载可用来源。"))),
    h("div", { class: "card-body" },
      fieldRow(connMeta("base_url"), textInput(S.draft.base_url, (v, el) => {
        S.draft.base_url = v;
        el.classList.toggle("invalid", S.showErrors && !/^https?:\/\/[^\s/]+/.test(v.trim()));
        changed(false);
      }, { key: "base_url", mono: true, placeholder: "https://anisignal.xyz", invalid: errors.length > 0 }), {
        hint: "服务根地址，插件会自动拼接 API 路径。", error: errors[0],
      }),
      fieldRow(keyMeta, keyControl, {
        label: "AniSignal 用户 Token",
        hint: "登录 AniSignal → 账号中心 → 机器人接入，创建专用 Token 并填入。每个 AstrBot 实例使用独立 Token；页面只显示末四位。",
      }),
      fieldRow(connMeta("card_font_path"), textInput(S.draft.card_font_path, (v) => { S.draft.card_font_path = v; changed(false); },
        { key: "card_font_path", mono: true, placeholder: "留空使用内置字体" }))));

  const update = S.data.update || {};
  const updateBadge = !update.checked ? h("span", { class: "badge" }, "正在检查新版本")
    : update.available ? h("span", { class: "badge warn" }, `有新版本 v${update.latest}`)
      : update.latest ? h("span", { class: "badge ok" }, "已是最新")
        : h("span", { class: "badge" }, "暂时无法连接 GitHub 检查版本");
  const updates = h("section", { class: "card" }, h("div", { class: "card-body" },
    h("div", { class: "inline-row", style: "margin-bottom:6px" },
      h("h3", { style: "margin:0;font-size:14px" }, "插件更新"),
      h("span", { class: "badge brand" }, `当前 v${S.data.version}`), updateBadge),
    h("p", { class: "hint", style: "margin:0" },
      "从插件市场安装时，插件页会直接提示新版本。从链接或手动安装时，按下面设置一次，之后有新版本时 AniSignal 卡片会显示「有新版本」，点它即可更新，设置会保留。"),
    h("ol", { class: "steps" },
      h("li", null, "打开 AstrBot「插件市场」页，点标签旁显示当前插件源的按钮，选择「添加插件源」，源地址填：",
        h("code", { class: "copyable", title: "点击选中整段地址" }, PLUGIN_SOURCE_URL), "，解析后保存。"),
      h("li", null, "回到「插件」页，在 AniSignal 卡片上点「⋯」→「更换插件源」，选择刚添加的 AniSignal 插件源，点「使用该插件源」。")),
    h("p", { class: "hint", style: "margin:0" },
      "没有设置插件源时，也可以点「⋯」→「重新安装」获取最新版本：首次会请你选择安装源，选 GitHub 仓库后确认强制更新。")));

  const guide = h("section", { class: "card" }, h("div", { class: "card-body" },
    h("h3", { style: "margin:0 0 6px;font-size:14px" }, "添加会话"),
    h("p", { class: "hint", style: "margin:0" },
      "在目标群聊或私聊发送 /情报会话，复制回复里的完整会话标识；也可以直接从左侧「最近有消息的会话」添加。",
      "\n移除会话后，AniSignal 会暂停该会话的推送，并保留历史投递记录。")));

  return [account, settings, updates, guide];
}

/* ---------- 会话编辑 ---------- */

function renderSession() {
  const index = S.selected;
  const t = T();
  const errors = S.showErrors ? validate().targets[index] || [] : [];
  const armedDelete = S.armed === `delete:${index}`;
  const umoInvalid = S.showErrors && !UMO_RE.test(t.umo.trim());
  const head = h("div", { class: "session-head" },
    h("div", null,
      h("label", { class: "sr-only", for: "session-name" }, "会话名称"),
      h("input", {
        id: "session-name", class: "title-input", value: t.name, placeholder: "会话名称，例如：鸣潮攻略群", "data-k": "name", maxlength: 100,
        onInput: (event) => setField(null, "name", event.target.value, false),
      }),
      h("div", { class: "session-meta", id: "session-meta" }, sessionMeta(t)),
      h("div", { style: "margin-top:12px" },
        h("label", { class: "sr-only", for: "session-umo" }, "会话标识（UMO）"),
        h("input", {
          id: "session-umo", class: `input mono${umoInvalid ? " invalid" : ""}`, value: t.umo, "data-k": "umo", spellcheck: "false", autocomplete: "off",
          placeholder: "会话标识（UMO），例如 napcat:GroupMessage:123456",
          onInput: (event) => {
            setField(null, "umo", event.target.value, false);
            event.target.classList.toggle("invalid", S.showErrors && !UMO_RE.test(event.target.value.trim()));
            // 只就地刷新平台标签，不整体重绘，免得吞掉随后的点击。
            $("#session-meta")?.replaceChildren(...sessionMeta(T()));
          },
        }),
        h("p", { class: "hint" }, meta(null, "umo").hint || ""),
        errors.length ? h("p", { class: "err-text" }, errors.join("；")) : null)),
    h("div", { class: "session-actions" },
      switchControl(t.enabled, (v) => setField(null, "enabled", v), "enabled", t.enabled ? "推送与查询已启用" : "已暂停"),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn small", onClick: duplicateSession }, "复制为新会话"),
        h("button", {
          type: "button", class: `btn small danger${armedDelete ? " armed" : ""}`, "data-k": "delete",
          title: "移除后主节点会暂停该会话，并保留历史投递记录",
          onClick: () => armedDelete ? deleteSession(index) : arm(`delete:${index}`),
        }, armedDelete ? "确认移除？" : "移除"))));

  const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "会话设置分组" },
    TABS.map(([key, label]) => h("button", {
      type: "button", role: "tab", class: "tab", "aria-selected": String(S.tab === key), "data-k": `tab:${key}`,
      onClick: () => { S.tab = key; render(); },
    }, label, key === "subscription" ? h("span", { class: "count" }, scopeCount(t)) : null)));

  const panels = {
    subscription: renderSubscription,
    notifications: renderNotifications,
    presentation: renderPresentation,
    interaction: renderInteraction,
  };
  return [
    h("section", { class: "card" }, head, tabs),
    h("section", { class: "card", role: "tabpanel" }, panels[S.tab](t, index)),
  ];
}

function sessionMeta(t) {
  const parsed = parseUmo(t.umo);
  const server = S.data.server_targets?.[t.umo.trim()];
  let state;
  if (S.data.sync.state === "error") state = h("span", { class: "badge err" }, "未同步");
  else if (server && server.enabled === t.enabled) state = h("span", { class: "badge ok" }, t.enabled ? "主节点已生效" : "主节点已暂停");
  else state = h("span", { class: "badge warn" }, "保存后生效");
  return [
    parsed ? h("span", { class: "badge brand" }, parsed.kind) : null,
    parsed ? h("span", null, `${parsed.platform} · ${parsed.id}`) : h("span", null, "填写会话标识后显示平台与类型"),
    state,
  ].filter(Boolean);
}

function arm(key) {
  S.armed = key;
  render();
  setTimeout(() => { if (S.armed === key) { S.armed = null; render(); } }, 4000);
}

function duplicateSession() {
  const copy = clone(T());
  copy.name = `${copy.name || "会话"}（副本）`;
  copy.umo = "";
  copy.enabled = false;
  S.draft.targets.push(copy);
  S.selected = S.draft.targets.length - 1;
  render();
  window.scrollTo({ top: 0 });
  document.querySelector('[data-k="umo"]')?.focus();
}

function deleteSession(index) {
  const [removed] = S.draft.targets.splice(index, 1);
  S.armed = null;
  S.selected = S.draft.targets.length ? Math.min(index, S.draft.targets.length - 1) : "connection";
  render();
  toast(`已移除「${removed.name || "未命名会话"}」，保存后生效`);
}

function selectedChannels(t) {
  return channelGroups().flatMap((g) => t.subscription[g.field] || []);
}

function scopeCount(t) {
  const sub = t.subscription;
  if (sub.scope_mode === "custom") return (sub.custom_feeds || []).length + (sub.custom_feed_ids || []).length;
  if (sub.scope_mode === "legacy") return `${(sub.communities || []).length} 区`;
  return selectedChannels(t).length;
}

/* ----- 订阅来源 ----- */

function renderSubscription(t, index) {
  const sub = t.subscription;
  const set = (key, value, rerender = true) => setField("subscription", key, value, rerender);
  const sections = [];

  let scopeBody;
  if (sub.scope_mode === "channels") scopeBody = renderChannelPicker(t, index);
  else if (sub.scope_mode === "legacy") {
    const m = meta("subscription", "communities");
    scopeBody = h("div", null,
      h("div", { class: "picker-tools" }, h("span", { class: "summary" }, "已选 ", h("b", null, (sub.communities || []).length), " 个分区")),
      chipSet(options(m).map((o) => ({ ...o, cls: `ch-${o.value}`, swatch: true })), sub.communities || [], (v) => set("communities", v), "communities"),
      (sub.communities || []).length ? null : h("p", { class: "err-text" }, "至少选择一个分区，否则主节点会拒绝此会话。"));
  } else {
    scopeBody = h("div", { class: "empty" }, "此模式不看公共频道，只推送下方「定制信息源与监控」中勾选的内容。");
  }
  sections.push(h("div", { class: "section" },
    h("h3", null, "来源范围"),
    h("p", { class: "lede" }, meta("subscription", "scope_mode").hint || ""),
    segmented(meta("subscription", "scope_mode"), sub.scope_mode, (v) => set("scope_mode", v), "scope_mode", { wide: true, notes: SCOPE_NOTES }),
    h("div", { style: "margin-top:16px" }, scopeBody)));

  const presetMeta = meta("subscription", "custom_feeds");
  const feedMeta = meta("subscription", "custom_feed_ids");
  const presets = options(presetMeta);
  const feeds = options(feedMeta);
  sections.push(h("div", { class: "section" },
    h("h3", null, "定制信息源与监控"),
    h("p", { class: "lede" }, "任何模式下都会额外推送这里勾选的内容。"),
    // 现有站点预设已并入游戏分区的官方来源；只有仍有预设或旧配置残留时才显示这一项。
    presets.length || (sub.custom_feeds || []).length
      ? fieldRow(presetMeta, chipSet(presets, sub.custom_feeds || [], (v) => set("custom_feeds", v), "custom_feeds")) : null,
    fieldRow(feedMeta, feeds.length ? chipSet(feeds, sub.custom_feed_ids || [], (v) => set("custom_feed_ids", v), "custom_feed_ids")
      : h("div", { class: "empty" }, S.data.connection.tools_key_set ? emptyFeedsText() : "填写用户 Token 并同步后，这里会列出账号可用的监控。"),
      { hint: feeds.length ? "勾选要推送到此会话的本人监控与他人公开的监控；新的监控在 AniSignal 网站「账号中心 → 定制监控」中创建。" : null })));

  sections.push(h("div", { class: "section" },
    h("h3", null, "关键词"),
    fieldRow(meta("subscription", "keywords"), tagInput(sub.keywords || [], (v) => set("keywords", v),
      { key: "keywords", placeholder: "输入后按回车，留空表示不限" })),
    fieldRow(meta("subscription", "exclude_keywords"), tagInput(sub.exclude_keywords || [], (v) => set("exclude_keywords", v),
      { key: "exclude_keywords", placeholder: "命中即不推送，优先于关注词", exclude: true }))));

  sections.push(h("div", { class: "section" },
    h("h3", null, "账号偏好"),
    fieldRow(meta("subscription", "inherit_preferences"), switchControl(sub.inherit_preferences, (v) => set("inherit_preferences", v), "inherit_preferences"), { inline: true }),
    fieldRow(meta("subscription", "inherit_keywords"), switchControl(sub.inherit_keywords, (v) => set("inherit_keywords", v), "inherit_keywords"), { inline: true })));

  return sections;
}

const PLUGIN_SOURCE_URL = "https://raw.githubusercontent.com/tianyin231/AstrBot_Plugin_AniSignal/main/astrbot-plugin-source.json";

function emptyFeedsText() {
  const quota = S.data.sync.identity?.quota;
  const limit = quota ? `（每个账号最多 ${quota.max_feeds} 个）` : "";
  return `还没有可选的监控。在 AniSignal 网站「账号中心 → 定制监控」点「新建监控」创建${limit}，约 1 分钟后出现在这里。`;
}

function renderChannelPicker(t, index) {
  const sub = t.subscription;
  const groups = channelGroups();
  const query = S.filter.trim().toLowerCase();
  const visible = groups.filter((g) => !query || g.name.toLowerCase().includes(query) || g.key.includes(query));
  const total = selectedChannels(t).length;
  const covered = groups.filter((g) => (sub[g.field] || []).length).length;
  const openKey = (g) => `${index}:${g.key}`;
  const isOpen = (g) => S.open.has(openKey(g)) ? S.open.get(openKey(g)) : (sub[g.field] || []).length > 0;
  const setGroup = (g, values) => setField("subscription", g.field, values);

  const tools = h("div", { class: "picker-tools" },
    h("input", {
      class: "input", type: "search", placeholder: "筛选分区", value: S.filter, "data-k": "channel-filter", "aria-label": "筛选分区",
      onInput: (event) => { S.filter = event.target.value; render(); },
    }),
    h("span", { class: "summary" }, "已选 ", h("b", null, total), " 个来源，覆盖 ", h("b", null, covered), " 个分区"),
    h("span", { class: "spacer" }),
    h("button", { type: "button", class: "link-btn", onClick: () => { groups.forEach((g) => S.open.set(openKey(g), (sub[g.field] || []).length > 0)); render(); } }, "只展开已选"),
    h("button", { type: "button", class: "link-btn", onClick: () => { groups.forEach((g) => S.open.set(openKey(g), false)); render(); } }, "全部收起"));

  const list = h("div", { class: "communities" }, visible.map((g) => {
    const chosen = sub[g.field] || [];
    const open = isOpen(g);
    const byKind = { official: [], community: [], other: [] };
    g.options.forEach((o) => byKind[sourceKind(o.value)].push(o));
    const officialValues = byKind.official.map((o) => o.value);
    const summary = Object.entries(byKind).map(([kind, items]) => {
      const n = items.filter((o) => chosen.includes(o.value)).length;
      return n ? (kind === "other" ? items.filter((o) => chosen.includes(o.value)).map((o) => o.label).join("、") : `${KIND_LABEL[kind]} ${n}`) : null;
    }).filter(Boolean).join(" · ");
    return h("div", { class: `community ch-${g.key}${chosen.length ? " has-selection" : ""}`, "data-open": String(open) },
      h("div", { class: "community-head" },
        h("button", {
          type: "button", class: "community-toggle", "aria-expanded": String(open), "data-k": `community:${g.key}`,
          onClick: () => { S.open.set(openKey(g), !open); render(); },
        }, h("span", { class: "bar" }), h("b", null, g.name),
        h("span", { class: "count" }, chosen.length ? `${chosen.length}/${g.options.length}${open ? "" : " · " + summary}` : `0/${g.options.length}`),
        h("span", { class: "caret", "aria-hidden": "true" }, "▾")),
        h("div", { class: "community-quick" },
          h("button", { type: "button", class: "btn small quiet", onClick: () => { S.open.set(openKey(g), true); setGroup(g, g.options.map((o) => o.value)); } }, "全选"),
          officialValues.length ? h("button", { type: "button", class: "btn small quiet", onClick: () => { S.open.set(openKey(g), true); setGroup(g, officialValues); } }, "仅官方") : null,
          chosen.length ? h("button", { type: "button", class: "btn small quiet", onClick: () => setGroup(g, []) }, "清空") : null)),
      open ? h("div", { class: "community-body" }, Object.entries(byKind).filter(([, items]) => items.length).map(([kind, items]) =>
        h("div", { class: "source-row" }, h("span", null, KIND_LABEL[kind]),
          chipSet(items.map((o) => ({ value: o.value, label: shortLabel(o.label), title: o.value })), chosen, (v) => setGroup(g, v), `src:${g.key}`)))) : null);
  }));

  const warning = total === 0 && !(sub.custom_feeds || []).length && !(sub.custom_feed_ids || []).length
    ? h("div", { class: "banner warn", style: "margin:0 0 12px" }, h("div", null, h("strong", null, "还没有勾选任何来源"), "此会话不会收到公共频道的推送。"))
    : null;
  return h("div", null, tools, warning, visible.length ? list : h("div", { class: "empty" }, "没有匹配的分区"));
}

/* ----- 自动通知 ----- */

function renderNotifications(t, index) {
  const n = t.notifications;
  const errors = S.showErrors ? validate().targets[index] || [] : [];
  const set = (key, value, rerender = true) => setField("notifications", key, value, rerender);
  const timeInput = (key) => h("input", {
    class: `input time${S.showErrors && !TIME_RE.test(n[key] || "") ? " invalid" : ""}`, type: "time", step: 60, value: n[key], "data-k": key,
    onInput: (event) => {
      set(key, event.target.value, false);
      event.target.classList.toggle("invalid", S.showErrors && !TIME_RE.test(event.target.value));
    },
  });
  const sectionHead = (title, key, note) => h("h3", null, title, h("span", { class: "spacer" }),
    switchControl(n[key], (v) => set(key, v), key, n[key] ? "开启" : "关闭"), note);

  const daily = h("div", { class: "section" },
    sectionHead("每日早报", "daily_enabled"),
    h("div", { class: `subtle-block${n.daily_enabled ? "" : " off"}` },
      fieldRow(meta("notifications", "daily_time"), timeInput("daily_time"), { inline: true, error: errors.find((e) => e.startsWith("早报时间")) }),
      fieldRow(meta("notifications", "daily_style"), segmented(meta("notifications", "daily_style"), n.daily_style, (v) => set("daily_style", v), "daily_style")),
      fieldRow(meta("notifications", "daily_sources"), narrowPicker(t, index, "daily_sources"))));

  const mergeMeta = meta("notifications", "merge_minutes");
  const updates = h("div", { class: "section" },
    sectionHead("重要动态", "updates_enabled"),
    h("div", { class: `subtle-block${n.updates_enabled ? "" : " off"}` },
      fieldRow(mergeMeta, rangeControl(mergeMeta, n.merge_minutes, (v) => set("merge_minutes", v, false), "merge_minutes", (v) => v ? `${v} 分钟` : "不合并"), { inline: true }),
      fieldRow(meta("notifications", "min_push_interval_minutes"), h("div", { class: "inline-row" },
        h("input", {
          class: `input short${S.showErrors && !(Number.isInteger(n.min_push_interval_minutes) && n.min_push_interval_minutes >= 0) ? " invalid" : ""}`,
          type: "number", min: 0, step: 1, value: n.min_push_interval_minutes, "data-k": "min_push_interval_minutes",
          onInput: (event) => {
            const raw = event.target.value.trim();
            set("min_push_interval_minutes", /^\d+$/.test(raw) ? Number(raw) : raw, false);
          },
        }), h("span", { class: "sep" }, "分钟"))),
      fieldRow(meta("notifications", "updates_sources"), narrowPicker(t, index, "updates_sources"))));

  const calendarMeta = meta("notifications", "calendar_kinds");
  const calendar = h("div", { class: "section" },
    sectionHead("活动收尾提醒", "calendar_enabled"),
    n.calendar_enabled && !n.updates_enabled ? h("p", { class: "lede", style: "color:var(--warn)" }, "需要同时开启「重要动态」才会发送。") : h("p", { class: "lede" }, meta("notifications", "calendar_enabled").hint || ""),
    h("div", { class: `subtle-block${n.calendar_enabled && n.updates_enabled ? "" : " off"}` },
      fieldRow(meta("notifications", "calendar_time"), timeInput("calendar_time"), { inline: true, error: errors.find((e) => e.startsWith("收尾提醒")) }),
      fieldRow(calendarMeta, chipSet(options(calendarMeta), n.calendar_kinds || [], (v) => set("calendar_kinds", v), "calendar_kinds"))));

  const hours = Array.from({ length: 24 }, (_, hour) => hour);
  const quietOn = n.quiet_start !== n.quiet_end;
  const inQuiet = (hour) => quietOn && (n.quiet_start < n.quiet_end ? hour >= n.quiet_start && hour < n.quiet_end : hour >= n.quiet_start || hour < n.quiet_end);
  const hourSelect = (key) => h("select", {
    class: "select auto", "data-k": key, "aria-label": meta("notifications", key).description,
    onChange: (event) => set(key, Number(event.target.value)),
  }, hours.map((hour) => h("option", { value: hour, selected: hour === n[key] }, `${String(hour).padStart(2, "0")}:00`)));
  const quiet = h("div", { class: "section" },
    h("h3", null, "免打扰", h("span", { class: "spacer" }), h("span", { class: `badge${quietOn ? " brand" : ""}` }, quietOn ? `${String(n.quiet_start).padStart(2, "0")}:00 – ${String(n.quiet_end).padStart(2, "0")}:00` : "已关闭")),
    h("p", { class: "lede" }, meta("notifications", "quiet_end").hint || ""),
    h("div", { class: "inline-row" }, h("span", null, "从"), hourSelect("quiet_start"), h("span", null, "到"), hourSelect("quiet_end")),
    h("div", { class: "quiet-bar", "aria-hidden": "true" }, hours.map((hour) => h("i", { class: inQuiet(hour) ? "q" : "", title: `${hour}:00` }))),
    h("div", { class: "quiet-scale", "aria-hidden": "true" }, ["00", "06", "12", "18", "24"].map((label) => h("span", null, label))),
    fieldRow(meta("notifications", "quiet_critical"), switchControl(n.quiet_critical, (v) => set("quiet_critical", v), "quiet_critical"), { inline: true }));

  const imageMeta = meta("notifications", "max_images");
  const images = h("div", { class: "section" },
    h("h3", null, "图片"),
    fieldRow(imageMeta, rangeControl(imageMeta, n.max_images, (v) => set("max_images", v, false), "max_images", (v) => `${v} 张`), { inline: false }));

  return [daily, updates, calendar, quiet, images];
}

function rangeControl(m, value, onChange, key, format) {
  const slider = m.slider || { min: 0, max: 10, step: 1 };
  const output = h("output", null, format(value));
  return h("div", { class: "range" },
    h("input", {
      type: "range", min: slider.min, max: slider.max, step: slider.step || 1, value, "data-k": key, "aria-label": m.description,
      onInput: (event) => { const v = Number(event.target.value); output.textContent = format(v); onChange(v); },
    }), output);
}

/** 早报/动态来源收窄：只列出会话订阅范围内的来源，留空表示跟随会话。 */
function narrowPicker(t, index, field) {
  const m = meta("notifications", field);
  const sub = t.subscription;
  const selected = t.notifications[field] || [];
  const chosen = new Set(selectedChannels(t));
  const inScope = (value) => {
    const v = String(value);
    if (v.startsWith("preset:")) return (sub.custom_feeds || []).includes(v.slice(7));
    if (v.startsWith("feed:")) return (sub.custom_feed_ids || []).map(String).includes(v.slice(5));
    if (sub.scope_mode === "custom") return false;
    if (sub.scope_mode === "channels") return chosen.has(v);
    return (sub.communities || []).includes(v.split(":")[0]);
  };
  const available = options(m).filter((o) => inScope(o.value) || selected.includes(o.value));
  const key = `${index}:${field}`;
  const open = S.narrow.has(key) || selected.length > 0;
  if (!open) {
    return h("div", { class: "inline-row" },
      h("span", { class: "badge" }, `跟随会话订阅（${available.length} 个来源）`),
      available.length ? h("button", { type: "button", class: "link-btn", "data-k": `narrow:${field}`, onClick: () => { S.narrow.add(key); render(); } }, "按来源收窄…") : null);
  }
  const groups = new Map();
  for (const o of available) {
    const v = String(o.value);
    const group = v.startsWith("preset:") || v.startsWith("feed:") ? "custom" : v.split(":")[0];
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(o);
  }
  const set = (values) => setField("notifications", field, values);
  return h("div", null,
    h("div", { class: "picker-tools" },
      h("span", { class: "summary" }, selected.length ? ["仅推送 ", h("b", null, selected.length), " 个来源"] : "未勾选时跟随会话订阅"),
      h("span", { class: "spacer" }),
      h("button", { type: "button", class: "link-btn", onClick: () => { S.narrow.delete(key); set([]); } }, selected.length ? "清空并跟随会话" : "收起")),
    available.length ? h("div", { class: "communities" }, [...groups.entries()].map(([group, items]) =>
      h("div", { class: `community ch-${group}`, style: "padding:10px 12px" },
        h("div", { style: "font-size:12.5px;font-weight:700;margin-bottom:6px;color:var(--ink-2)" }, communityName(group)),
        chipSet(items.map((o) => ({ value: o.value, label: shortLabel(o.label, { prefix: communityName(group), stripKind: false }), title: String(o.value), cls: inScope(o.value) ? "" : "muted" })),
          selected, set, `${field}:${group}`)))) : h("div", { class: "empty" }, "会话订阅范围内还没有来源。"));
}

/* ----- 回复与模板 ----- */

function renderPresentation(t) {
  const p = t.presentation;
  const set = (key, value, rerender = true) => setField("presentation", key, value, rerender);
  const providerMeta = meta("presentation", "notification_provider_id");
  const providers = S.data.providers || [];
  const providerOptions = [h("option", { value: "", selected: !p.notification_provider_id }, "跟随当前会话模型"),
    ...providers.map((row) => h("option", { value: row.id, selected: row.id === p.notification_provider_id }, row.model ? `${row.id} · ${row.model}` : row.id))];
  if (p.notification_provider_id && !providers.some((row) => row.id === p.notification_provider_id)) {
    providerOptions.push(h("option", { value: p.notification_provider_id, selected: true }, `${p.notification_provider_id}（未找到）`));
  }

  const template = (key, placeholders) => {
    const m = meta("presentation", key);
    const area = h("textarea", {
      class: "textarea mono", rows: key === "notification_template" ? 4 : 6, "data-k": key, spellcheck: "false",
      onInput: (event) => set(key, event.target.value, false),
    });
    area.value = p[key] ?? "";
    const insert = (token) => {
      const start = area.selectionStart ?? area.value.length;
      const end = area.selectionEnd ?? start;
      area.value = area.value.slice(0, start) + token + area.value.slice(end);
      area.setSelectionRange(start + token.length, start + token.length);
      set(key, area.value, false);
    };
    return fieldRow(m, h("div", null,
      h("div", { class: "placeholders", "aria-label": "插入占位符" }, placeholders.map((token) =>
        h("button", { type: "button", onMousedown: (event) => event.preventDefault(), onClick: () => { area.focus(); insert(token); } }, token))),
      area,
      p[key] !== m.default ? h("button", { type: "button", class: "link-btn", style: "margin-top:6px", onClick: () => set(key, m.default) }, "恢复默认模板") : null));
  };

  return [
    h("div", { class: "section" },
      h("h3", null, "主动查询"),
      fieldRow(meta("presentation", "reply_mode"), segmented(meta("presentation", "reply_mode"), p.reply_mode, (v) => set("reply_mode", v), "reply_mode", { wide: true }))),
    h("div", { class: "section" },
      h("h3", null, "主动通知"),
      fieldRow(meta("presentation", "notification_style"), segmented(meta("presentation", "notification_style"), p.notification_style, (v) => set("notification_style", v), "notification_style")),
      fieldRow(meta("presentation", "notification_format"), segmented(meta("presentation", "notification_format"), p.notification_format, (v) => set("notification_format", v), "notification_format")),
      fieldRow(providerMeta, h("select", {
        class: "select", "data-k": "notification_provider_id", onChange: (event) => set("notification_provider_id", event.target.value),
      }, providerOptions)),
      fieldRow(meta("presentation", "instructions"), (() => {
        const area = h("textarea", { class: "textarea", rows: 3, "data-k": "instructions", placeholder: "例如：分点列出，每条不超过两句。", onInput: (event) => set("instructions", event.target.value, false) });
        area.value = p.instructions ?? "";
        return area;
      })())),
    h("div", { class: "section" },
      h("h3", null, "通知模板"),
      template("notification_template", MAIN_PLACEHOLDERS),
      template("notification_item_template", ITEM_PLACEHOLDERS),
      fieldRow(meta("presentation", "show_details_hint"), switchControl(p.show_details_hint, (v) => set("show_details_hint", v), "show_details_hint"), { inline: true })),
  ];
}

/* ----- 问答权限 ----- */

function renderInteraction(t) {
  const it = t.interaction;
  const set = (key, value, rerender = true) => setField("interaction", key, value, rerender);
  const toolsMeta = meta("interaction", "tools");
  const needsDigest = it.activities_enabled && !(it.tools || []).includes("digest");
  return [
    h("div", { class: "section" },
      h("h3", null, "查询工具"),
      h("p", { class: "lede" }, "开放后，群友可以用命令或自然语言查询；未开放的工具不会出现在 /帮助 中。"),
      fieldRow(toolsMeta, chipSet(options(toolsMeta), it.tools || [], (v) => set("tools", v), "tools"))),
    h("div", { class: "section" },
      h("h3", null, "聊天中修改订阅"),
      fieldRow(meta("interaction", "chat_controls"), switchControl(it.chat_controls, (v) => set("chat_controls", v), "chat_controls"), { inline: true }),
      h("div", { class: `subtle-block${it.chat_controls ? "" : " off"}` },
        fieldRow(meta("interaction", "manager_ids"), tagInput(it.manager_ids || [], (v) => set("manager_ids", v),
          { key: "manager_ids", placeholder: "粘贴 /情报会话 返回的管理标识" })))),
    h("div", { class: "section" },
      h("h3", null, "提醒与互动"),
      fieldRow(meta("interaction", "reminders_enabled"), switchControl(it.reminders_enabled, (v) => set("reminders_enabled", v), "reminders_enabled"), { inline: true }),
      fieldRow(meta("interaction", "activities_enabled"), switchControl(it.activities_enabled, (v) => set("activities_enabled", v), "activities_enabled"), {
        inline: true,
        error: needsDigest ? "需要同时开放「近日速报」工具。" : null,
      })),
  ];
}

/* ---------- 总渲染 ---------- */

function render() {
  if (!S.data) return;
  preserveFocus(() => {
    renderTop();
    renderBanner();
    let root = $("#root");
    if (!root.classList.contains("layout")) {
      root.className = "layout";
      root.removeAttribute("aria-busy");
      root.replaceChildren(h("aside", { id: "rail", class: "rail", "aria-label": "设置导航" }), h("main", { id: "pane", class: "pane" }));
    }
    renderRail();
    $("#pane").replaceChildren(...(S.selected === "connection" ? renderConnection() : renderSession()));
  });
}

function renderFailure(error) {
  const root = $("#root");
  root.className = "loading";
  root.replaceChildren(h("div", { style: "text-align:center;max-width:420px" },
    h("p", { style: "font-weight:600;color:var(--ink)" }, "没能读取插件设置"),
    h("p", { class: "hint" }, error?.message || "请确认 AniSignal 插件已启用，并已更新到支持设置视图的版本。"),
    h("button", { type: "button", class: "btn", style: "margin-top:10px", onClick: start }, "重试")));
}

async function start() {
  if (!bridge) {
    renderFailure(new Error("当前 AstrBot 未提供插件视图接口，请在插件管理中使用原生设置。"));
    return;
  }
  try {
    await bridge.ready();
    load(await fetchState());
    // 版本检查在后台进行，首次打开时稍后补取一次结果。
    if (!S.data.update?.checked) setTimeout(poll, 6000);
  } catch (error) {
    renderFailure(error);
  }
}

$("#save-btn").addEventListener("click", save);
$("#sync-btn").addEventListener("click", syncNow);
$("#discard-btn").addEventListener("click", () => {
  S.draft = JSON.parse(S.baseline);
  S.showErrors = false;
  S.replacingKey = false;
  S.banner = null;
  if (typeof S.selected === "number" && S.selected >= S.draft.targets.length) S.selected = "connection";
  render();
  toast("已放弃未保存的修改");
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    save();
  }
});
setInterval(poll, 30000);
start();
