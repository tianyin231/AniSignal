/**
 * AniSignal 情报助手 — DeepSeek Harness 前端设置面板 (Web / Desktop 通用)。
 * 通过 window.__ModuleLoader__.load 自注册，无需额外构建步骤。
 */
(function registerAniSignalClient() {
  if (typeof window === 'undefined' || !window.__ModuleLoader__ || typeof window.__ModuleLoader__.load !== 'function') {
    return;
  }

  const PACKAGE_NAME = 'dsh-plugin-anisignal';
  const MAIN_PANEL_ID = 'anisignal-settings-page';
  const STYLE_ID = 'anisignal-dsh-client-styles';

  function installStyles() {
    if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      .anisg-wrap { max-width: 880px; margin: 0 auto; padding: 16px 20px 48px; color: var(--text-primary, inherit); font-size: 13px; line-height: 1.55; }
      .anisg-page { height: 100%; overflow-y: auto; }
      .anisg-card { border: 1px solid rgba(128,128,128,0.22); border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; background: rgba(128,128,128,0.04); }
      .anisg-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
      .anisg-between { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
      .anisg-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px 14px; }
      .anisg-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; }
      .anisg-label { font-weight: 600; font-size: 12px; opacity: 0.88; }
      .anisg-hint { font-size: 11.5px; opacity: 0.68; }
      .anisg-input, .anisg-select, .anisg-textarea {
        width: 100%; box-sizing: border-box; padding: 6px 9px; border-radius: 6px;
        border: 1px solid rgba(128,128,128,0.32); background: transparent; color: inherit; font-size: 13px;
      }
      .anisg-textarea { min-height: 58px; resize: vertical; font-family: inherit; }
      .anisg-btn {
        padding: 5px 12px; border-radius: 6px; border: 1px solid rgba(128,128,128,0.32);
        background: rgba(128,128,128,0.08); color: inherit; cursor: pointer; font-size: 12.5px;
      }
      .anisg-btn:hover:not(:disabled) { background: rgba(128,128,128,0.16); }
      .anisg-btn:disabled { opacity: 0.5; cursor: not-allowed; }
      .anisg-btn-primary { background: #387861; color: #fff; border-color: #387861; font-weight: 600; }
      .anisg-btn-primary:hover:not(:disabled) { background: #2e6551; }
      .anisg-btn-danger { color: #c94a4a; border-color: rgba(201,74,74,0.4); }
      .anisg-badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 999px; font-size: 11.5px; border: 1px solid rgba(128,128,128,0.28); }
      .anisg-badge-ok { color: #2b7a5b; border-color: rgba(56,120,97,0.45); background: rgba(56,120,97,0.1); }
      .anisg-badge-err { color: #c94a4a; border-color: rgba(201,74,74,0.45); background: rgba(201,74,74,0.1); }
      .anisg-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
      .anisg-chip {
        padding: 3px 9px; border-radius: 999px; font-size: 12px; cursor: pointer; user-select: none;
        border: 1px solid rgba(128,128,128,0.3); opacity: 0.8;
      }
      .anisg-chip-on { background: rgba(56,120,97,0.18); border-color: #387861; color: #387861; font-weight: 600; opacity: 1; }
      .anisg-msg { padding: 8px 12px; border-radius: 7px; margin-bottom: 12px; font-size: 12.5px; }
      .anisg-msg-ok { background: rgba(56,120,97,0.14); border: 1px solid rgba(56,120,97,0.4); }
      .anisg-msg-err { background: rgba(201,74,74,0.14); border: 1px solid rgba(201,74,74,0.4); color: #c94a4a; }
      .anisg-sep { height: 1px; background: rgba(128,128,128,0.18); margin: 10px 0; }
    `;
    document.head.appendChild(style);
  }

  function splitCsv(str) {
    return String(str || '')
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function joinCsv(arr) {
    return Array.isArray(arr) ? arr.join(', ') : '';
  }

  function makeEmptyTarget(umo = '', name = '') {
    return {
      name: name || (umo ? `会话 ${umo.split(':').pop().slice(0, 8)}` : '新 QQ 会话'),
      umo: umo || '',
      enabled: true,
      subscription: {
        scope_mode: 'legacy',
        inherit_preferences: true,
        inherit_keywords: false,
        communities: ['wuwa', 'blhx', 'hsr', 'arknights', 'genshin', 'endfield', 'ai'],
        channels: [],
        custom_feeds: [],
        custom_feed_ids: [],
        keywords: [],
        exclude_keywords: [],
      },
      notifications: {
        daily_enabled: true,
        daily_sources: [],
        daily_time: '08:30',
        updates_enabled: true,
        updates_sources: [],
        calendar_enabled: true,
        calendar_kinds: ['activity', 'pool', 'maintenance', 'deadline'],
        calendar_time: '20:00',
        quiet_start: 23,
        quiet_end: 8,
        quiet_critical: false,
        merge_minutes: 0,
        min_push_interval_minutes: 0,
        daily_style: 'compact',
        max_images: 1,
      },
      presentation: {
        notification_style: 'persona',
        notification_format: 'card',
        instructions: '',
        notification_provider_id: '',
        notification_template: '{intro}\n\n{body}',
        notification_item_template: '{index}. {title}\n{summary}\n发布时间：{time}\n来源：{source}\n{url}\n{images}',
        show_details_hint: false,
      },
      interaction: {
        tools: ['digest', 'news', 'products'],
        chat_controls: false,
        manager_ids: [],
        reminders_enabled: true,
        activities_enabled: false,
      },
    };
  }

  window.__ModuleLoader__.load({
    id: PACKAGE_NAME,
    factory: (require) => {
      const React = require('react');
      const h = React.createElement;
      const { useState, useEffect, useCallback } = React;

      function PluginUpdates({ pluginContext, revision, onRevision }) {
        const [update, setUpdate] = useState(null);
        const [busy, setBusy] = useState('checking');
        const [notice, setNotice] = useState(null);
        const [restartRequired, setRestartRequired] = useState(false);

        const readUpdate = useCallback(async (body) => {
          const resp = await fetch('/anisignal/updates', body === undefined
            ? { cache: 'no-store' }
            : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          const data = await resp.json();
          if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
          setUpdate(data.update);
          return data;
        }, []);

        useEffect(() => {
          let active = true;
          const refresh = async (initial = false) => {
            try {
              await readUpdate();
            } catch (err) {
              if (active) setNotice({ kind: 'err', text: `读取更新状态失败：${err?.message || err}` });
            } finally {
              if (active && initial) setBusy('');
            }
          };
          void refresh(true);
          // 展示后台定时检查的结果；外部版本查询本身有六小时缓存。
          const timer = setInterval(() => { void refresh(); }, 60_000);
          return () => { active = false; clearInterval(timer); };
        }, [readUpdate]);

        const handleCheck = async () => {
          setBusy('checking');
          setNotice(null);
          try {
            await readUpdate({});
          } catch (err) {
            setNotice({ kind: 'err', text: `检查更新失败：${err?.message || err}` });
          } finally {
            setBusy('');
          }
        };

        const handlePreference = async (autoCheck) => {
          setBusy('preference');
          setNotice(null);
          const previous = update;
          setUpdate((prev) => ({ ...prev, auto_check: autoCheck }));
          try {
            const data = await readUpdate({ auto_check: autoCheck, revision });
            onRevision?.(data.revision);
            setNotice({ kind: 'ok', text: autoCheck ? '已开启自动检查更新' : '已关闭自动检查更新' });
          } catch (err) {
            setUpdate(previous);
            setNotice({ kind: 'err', text: `保存更新设置失败：${err?.message || err}` });
          } finally {
            setBusy('');
          }
        };

        const handleInstall = async () => {
          setBusy('installing');
          setNotice({ kind: 'ok', text: '正在交给 DSH 安装更新，期间插件可能暂时重载…' });
          try {
            const manager = pluginContext?.remote?.pluginManager;
            if (!manager) throw new Error('当前 DSH 未提供插件更新接口，请升级 DSH');
            const listed = await manager.listBundles();
            if (!listed.ok) throw new Error(listed.error?.message || '无权读取插件列表');
            const bundle = listed.value.find((item) => item.name === PACKAGE_NAME);
            if (bundle?.update !== 'git') {
              throw new Error('当前安装方式不支持直接更新。请在 DSH 插件页改用 github:tianyin231/DSH_Plugin_AniSignal 安装一次，之后即可使用更新按钮');
            }
            const answer = await manager.updateBundle(PACKAGE_NAME, update.latest_version, {
              spec: update.spec,
              requestId: crypto.randomUUID(),
            });
            if (!answer.ok) throw new Error(answer.error?.message || '更新请求失败');
            const { result, restore } = answer.value;
            if (result.application !== 'applied' && result.application !== 'restart-required') {
              const restored = restore?.application === 'applied' || restore?.application === 'restart-required';
              throw new Error((result.error?.diagnostic || result.error?.code || '更新未完成')
                + (restored ? '；DSH 已恢复旧版本' : '；请到 DSH 插件页查看安装详情'));
            }
            setRestartRequired(result.application === 'restart-required');
            setNotice({ kind: 'ok', text: result.application === 'restart-required'
              ? `v${update.latest_version} 已安装，重启 DSH 后生效`
              : `已更新至 v${update.latest_version}，重新打开插件页面即可查看` });
            setUpdate((prev) => ({ ...prev, available: false,
              current_version: result.application === 'applied' ? update.latest_version : prev.current_version }));
          } catch (err) {
            setNotice({ kind: 'err', text: `更新失败：${err?.message || err}` });
          } finally {
            setBusy('');
          }
        };

        return h('section', { className: 'anisg-card', 'aria-label': '插件更新' },
          h('div', { className: 'anisg-between' },
            h('div', { className: 'anisg-row' },
              h('strong', null, '插件更新'),
              update ? h('span', { className: 'anisg-badge' }, `当前 v${update.current_version}`) : null,
              update?.available ? h('span', { className: 'anisg-badge anisg-badge-ok' }, `发现新版 v${update.latest_version}`)
                : (update?.latest_version && !update.error && !restartRequired ? h('span', null, '已是最新版本') : null),
            ),
            h('div', { className: 'anisg-row' },
              h('button', { type: 'button', className: 'anisg-btn', onClick: handleCheck, disabled: Boolean(busy) || restartRequired },
                busy === 'checking' ? '检查中…' : '检查更新'),
              h('button', {
                type: 'button', className: 'anisg-btn anisg-btn-primary', onClick: handleInstall,
                disabled: Boolean(busy) || !update?.available || Boolean(update?.error) || restartRequired,
              }, busy === 'installing' ? '更新中…' : (update?.available ? `更新至 v${update.latest_version}` : '更新插件')),
            ),
          ),
          h('div', { className: 'anisg-row', style: { marginTop: '10px' } },
            h('label', { className: 'anisg-row' },
              h('input', {
                type: 'checkbox', checked: update?.auto_check !== false, disabled: Boolean(busy) || !update,
                onChange: (event) => handlePreference(event.target.checked),
              }),
              busy === 'preference' ? '保存中…' : '自动检查更新（每 6 小时）',
            ),
            update?.checked_at ? h('span', { className: 'anisg-hint' }, `上次检查：${new Date(update.checked_at).toLocaleString()}`) : null,
          ),
          update?.error ? h('div', { className: 'anisg-msg anisg-msg-err', role: 'status', style: { marginTop: '10px', marginBottom: 0 } },
            `检查更新失败：${update.error}，可点击「检查更新」重试`) : null,
          notice ? h('div', { className: `anisg-msg anisg-msg-${notice.kind}`, role: 'status', style: { marginTop: '10px', marginBottom: 0 } }, notice.text) : null,
        );
      }

      function AniSignalDetailUpdates({ subject, pluginContext }) {
        if (subject?.kind !== 'bundle' || subject.pkg?.name !== PACKAGE_NAME) return null;
        return h(PluginUpdates, { pluginContext });
      }

      function TargetEditor({ target, index, catalog, onChange, onRemove }) {
        const [expanded, setExpanded] = useState(index === 0 || !target.umo);
        const sub = target.subscription || {};
        const notif = target.notifications || {};
        const pres = target.presentation || {};
        const inter = target.interaction || {};

        const patch = (section, updates) => {
          if (!section) onChange({ ...target, ...updates });
          else onChange({ ...target, [section]: { ...(target[section] || {}), ...updates } });
        };

        const toggleInList = (list, id) => {
          const cur = Array.isArray(list) ? list : [];
          return cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
        };

        const communities = catalog?.communities || [];
        const queryTools = catalog?.query_tools || [];
        const channelsMap = catalog?.channels || {};
        const selectedComms = new Set(sub.communities || []);
        const filteredChannels = Object.entries(channelsMap).filter(([chKey]) => {
          const prefix = chKey.split(':')[0];
          return selectedComms.size === 0 || selectedComms.has(prefix);
        });

        return h('div', { className: 'anisg-card' },
          h('div', { className: 'anisg-between' },
            h('div', { className: 'anisg-row' },
              h('input', {
                type: 'checkbox',
                checked: Boolean(target.enabled),
                onChange: (e) => patch(null, { enabled: e.target.checked }),
              }),
              h('strong', null, target.name || `会话 #${index + 1}`),
              target.umo ? h('span', { className: 'anisg-badge' }, target.umo) : null,
            ),
            h('div', { className: 'anisg-row' },
              h('button', {
                type: 'button',
                className: 'anisg-btn',
                onClick: () => setExpanded((v) => !v),
              }, expanded ? '收起详情' : '展开配置'),
              h('button', {
                type: 'button',
                className: 'anisg-btn anisg-btn-danger',
                onClick: onRemove,
              }, '删除'),
            ),
          ),
          expanded ? h('div', { style: { marginTop: '12px' } },
            h('div', { className: 'anisg-grid' },
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '会话备注名称'),
                h('input', {
                  className: 'anisg-input',
                  value: target.name || '',
                  placeholder: '例如：二游情报群 / 个人私聊',
                  onChange: (e) => patch(null, { name: e.target.value }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '会话标识 (UMO)'),
                h('input', {
                  className: 'anisg-input',
                  value: target.umo || '',
                  placeholder: 'dsh_qqbot:GroupMessage:OPENID 或 dsh_onebot:GroupMessage:群号',
                  onChange: (e) => patch(null, { umo: e.target.value }),
                }),
                h('span', { className: 'anisg-hint' }, '可在 QQ 中对机器人说「查看当前情报会话标识」直接获取'),
              ),
            ),

            h('div', { className: 'anisg-sep' }),
            h('div', { className: 'anisg-grid' },
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '订阅范围模式'),
                h('select', {
                  className: 'anisg-select',
                  value: sub.scope_mode || 'legacy',
                  onChange: (e) => patch('subscription', { scope_mode: e.target.value }),
                },
                  h('option', { value: 'legacy' }, '按分区整包订阅（推荐快速上手）'),
                  h('option', { value: 'channels' }, '按细分官方/社区/商品频道勾选'),
                  h('option', { value: 'custom' }, '仅使用账号自建监控源'),
                ),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '账号偏好继承'),
                h('label', { className: 'anisg-row', style: { marginTop: '5px' } },
                  h('input', {
                    type: 'checkbox',
                    checked: Boolean(sub.inherit_preferences),
                    onChange: (e) => patch('subscription', { inherit_preferences: e.target.checked }),
                  }),
                  h('span', null, '继承 AniSignal 网页端分区偏好与排除词'),
                ),
              ),
            ),

            sub.scope_mode !== 'custom' ? h('div', { className: 'anisg-field' },
              h('label', { className: 'anisg-label' }, '关注分区'),
              h('div', { className: 'anisg-chips' },
                communities.map((c) => {
                  const active = selectedComms.has(c.id);
                  return h('span', {
                    key: c.id,
                    className: `anisg-chip ${active ? 'anisg-chip-on' : ''}`,
                    onClick: () => patch('subscription', { communities: toggleInList(sub.communities, c.id) }),
                  }, c.label);
                }),
              ),
            ) : null,

            sub.scope_mode === 'channels' ? h('div', { className: 'anisg-field', style: { marginTop: '6px' } },
              h('label', { className: 'anisg-label' }, `细分频道（已选 ${(sub.channels || []).length} 个）`),
              h('div', { className: 'anisg-chips', style: { maxHeight: '132px', overflowY: 'auto', padding: '4px 0' } },
                filteredChannels.map(([chKey, chLabel]) => {
                  const active = (sub.channels || []).includes(chKey);
                  return h('span', {
                    key: chKey,
                    className: `anisg-chip ${active ? 'anisg-chip-on' : ''}`,
                    onClick: () => patch('subscription', { channels: toggleInList(sub.channels, chKey) }),
                  }, chLabel);
                }),
              ),
            ) : null,

            h('div', { className: 'anisg-grid', style: { marginTop: '6px' } },
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '关注关键词（逗号分隔，留空不限）'),
                h('input', {
                  className: 'anisg-input',
                  value: joinCsv(sub.keywords),
                  onChange: (e) => patch('subscription', { keywords: splitCsv(e.target.value) }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '排除关键词（逗号分隔，优先过滤）'),
                h('input', {
                  className: 'anisg-input',
                  value: joinCsv(sub.exclude_keywords),
                  onChange: (e) => patch('subscription', { exclude_keywords: splitCsv(e.target.value) }),
                }),
              ),
            ),

            h('div', { className: 'anisg-sep' }),
            h('div', { className: 'anisg-grid' },
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-row' },
                  h('input', {
                    type: 'checkbox',
                    checked: Boolean(notif.daily_enabled),
                    onChange: (e) => patch('notifications', { daily_enabled: e.target.checked }),
                  }),
                  h('span', { className: 'anisg-label' }, '每日早报时间 (北京时间 HH:mm)'),
                ),
                h('div', { className: 'anisg-row' },
                  h('input', {
                    className: 'anisg-input',
                    style: { width: '110px' },
                    value: notif.daily_time || '08:30',
                    onChange: (e) => patch('notifications', { daily_time: e.target.value }),
                  }),
                  h('select', {
                    className: 'anisg-select',
                    style: { width: '150px' },
                    value: pres.notification_format || 'card',
                    onChange: (e) => patch('presentation', { notification_format: e.target.value }),
                  },
                    h('option', { value: 'card' }, '早报图片卡优先'),
                    h('option', { value: 'text' }, '纯文本早报'),
                  ),
                ),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-row' },
                  h('input', {
                    type: 'checkbox',
                    checked: Boolean(notif.updates_enabled),
                    onChange: (e) => patch('notifications', { updates_enabled: e.target.checked }),
                  }),
                  h('span', { className: 'anisg-label' }, '即时快讯推送 / 合并窗口(分钟)'),
                ),
                h('input', {
                  type: 'number',
                  min: 0,
                  max: 60,
                  className: 'anisg-input',
                  value: notif.merge_minutes ?? 0,
                  onChange: (e) => patch('notifications', { merge_minutes: Number(e.target.value) || 0 }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-row' },
                  h('input', {
                    type: 'checkbox',
                    checked: Boolean(notif.calendar_enabled),
                    onChange: (e) => patch('notifications', { calendar_enabled: e.target.checked }),
                  }),
                  h('span', { className: 'anisg-label' }, '活动/卡池收尾提醒 (HH:mm)'),
                ),
                h('input', {
                  className: 'anisg-input',
                  value: notif.calendar_time || '20:00',
                  onChange: (e) => patch('notifications', { calendar_time: e.target.value }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '免打扰时段 (整点 0–23)'),
                h('div', { className: 'anisg-row' },
                  h('input', {
                    type: 'number',
                    min: 0,
                    max: 23,
                    className: 'anisg-input',
                    style: { width: '76px' },
                    value: notif.quiet_start ?? 23,
                    onChange: (e) => patch('notifications', { quiet_start: Number(e.target.value) || 0 }),
                  }),
                  h('span', null, '至'),
                  h('input', {
                    type: 'number',
                    min: 0,
                    max: 23,
                    className: 'anisg-input',
                    style: { width: '76px' },
                    value: notif.quiet_end ?? 8,
                    onChange: (e) => patch('notifications', { quiet_end: Number(e.target.value) || 0 }),
                  }),
                ),
              ),
            ),

            h('div', { className: 'anisg-sep' }),
            h('div', { className: 'anisg-field' },
              h('label', { className: 'anisg-label' }, '开放对话查询工具'),
              h('div', { className: 'anisg-chips' },
                queryTools.map((tool) => {
                  const active = (inter.tools || []).includes(tool.id);
                  return h('span', {
                    key: tool.id,
                    className: `anisg-chip ${active ? 'anisg-chip-on' : ''}`,
                    onClick: () => patch('interaction', { tools: toggleInList(inter.tools, tool.id) }),
                  }, tool.label);
                }),
              ),
            ),

            h('div', { className: 'anisg-grid', style: { marginTop: '8px' } },
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-row' },
                  h('input', {
                    type: 'checkbox',
                    checked: Boolean(inter.reminders_enabled),
                    onChange: (e) => patch('interaction', { reminders_enabled: e.target.checked }),
                  }),
                  h('span', null, '允许在会话中创建定时提醒'),
                ),
                h('label', { className: 'anisg-row', style: { marginTop: '4px' } },
                  h('input', {
                    type: 'checkbox',
                    checked: Boolean(inter.chat_controls),
                    onChange: (e) => patch('interaction', { chat_controls: e.target.checked }),
                  }),
                  h('span', null, '允许在聊天中口述修改订阅'),
                ),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '群内管理授权用户标识（逗号分隔 QQ 号或 OpenID）'),
                h('input', {
                  className: 'anisg-input',
                  value: joinCsv(inter.manager_ids),
                  onChange: (e) => patch('interaction', { manager_ids: splitCsv(e.target.value) }),
                }),
              ),
            ),

            h('div', { className: 'anisg-grid', style: { marginTop: '6px' } },
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '通知播报风格'),
                h('select', {
                  className: 'anisg-select',
                  value: pres.notification_style || 'persona',
                  onChange: (e) => patch('presentation', { notification_style: e.target.value }),
                },
                  h('option', { value: 'persona' }, '附带简短模型开场语'),
                  h('option', { value: 'original' }, '仅发送核验原文/卡片'),
                ),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '通知开场指定模型 (可选 provider/model)'),
                h('input', {
                  className: 'anisg-input',
                  placeholder: '留空默认跟随 DSH 默认模型',
                  value: pres.notification_provider_id || '',
                  onChange: (e) => patch('presentation', { notification_provider_id: e.target.value }),
                }),
              ),
            ),
            h('div', { className: 'anisg-field' },
              h('label', { className: 'anisg-label' }, '排版与补充要求（可选）'),
              h('textarea', {
                className: 'anisg-textarea',
                placeholder: '例如：语气简练，重点标出维护时间与兑换码截止日期',
                value: pres.instructions || '',
                onChange: (e) => patch('presentation', { instructions: e.target.value }),
              }),
            ),
          ) : null,
        );
      }

      function AniSignalSettingsSection({ pluginContext }) {
        const [view, setView] = useState(null);
        const [loading, setLoading] = useState(true);
        const [saving, setSaving] = useState(false);
        const [syncing, setSyncing] = useState(false);
        const [banner, setBanner] = useState(null);
        const [showChannelOverrides, setShowChannelOverrides] = useState(false);

        const [form, setForm] = useState({
          base_url: 'https://anisignal.xyz',
          tools_key: '',
          clear_tools_key: false,
          card_font_path: '',
          qqbot_app_id: '',
          qqbot_app_secret: '',
          clear_qqbot_secret: false,
          qqbot_markdown: false,
          onebot_http_url: '',
          onebot_ws_url: '',
          onebot_access_token: '',
          clear_onebot_token: false,
          targets: [],
        });

        const applyViewToForm = useCallback((state) => {
          if (!state) return;
          setView(state);
          const conn = state.connection || {};
          setForm({
            base_url: conn.base_url || 'https://anisignal.xyz',
            tools_key: '',
            clear_tools_key: false,
            card_font_path: conn.card_font_path || '',
            qqbot_app_id: conn.qqbot_app_id || '',
            qqbot_app_secret: '',
            clear_qqbot_secret: false,
            qqbot_markdown: Boolean(conn.qqbot_markdown),
            onebot_http_url: conn.onebot_http_url || '',
            onebot_ws_url: conn.onebot_ws_url || '',
            onebot_access_token: '',
            clear_onebot_token: false,
            targets: Array.isArray(state.targets) ? state.targets : [],
          });
        }, []);

        const loadState = useCallback(async () => {
          setLoading(true);
          try {
            const resp = await fetch('/anisignal/settings', { cache: 'no-store' });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            applyViewToForm(data);
            setBanner(null);
          } catch (err) {
            setBanner({ kind: 'err', text: `读取插件状态失败：${err?.message || err}` });
          } finally {
            setLoading(false);
          }
        }, [applyViewToForm]);

        useEffect(() => {
          installStyles();
          loadState();
        }, [loadState]);

        const handleSave = async () => {
          setSaving(true);
          setBanner(null);
          try {
            const resp = await fetch('/anisignal/save', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                revision: view?.revision || '',
                config: form,
              }),
            });
            const data = await resp.json();
            if (data?.state) applyViewToForm(data.state);
            if (!resp.ok || !data?.ok) {
              setBanner({ kind: 'err', text: data?.error || '保存或同步未成功' });
            } else {
              setBanner({ kind: 'ok', text: '配置已保存并与 AniSignal 节点同步成功' });
            }
          } catch (err) {
            setBanner({ kind: 'err', text: `保存请求失败：${err?.message || err}` });
          } finally {
            setSaving(false);
          }
        };

        const handleSyncNow = async () => {
          setSyncing(true);
          setBanner(null);
          try {
            const resp = await fetch('/anisignal/sync', { method: 'POST' });
            const data = await resp.json();
            if (data?.state) applyViewToForm(data.state);
            if (!resp.ok || !data?.ok) {
              setBanner({ kind: 'err', text: data?.error || '同步失败' });
            } else {
              setBanner({ kind: 'ok', text: '已立即同步订阅并检查待发通知队列' });
            }
          } catch (err) {
            setBanner({ kind: 'err', text: `同步失败：${err?.message || err}` });
          } finally {
            setSyncing(false);
          }
        };

        if (loading && !view) {
          return h('div', { className: 'anisg-wrap' }, '正在加载 AniSignal 情报助手配置…');
        }

        const conn = view?.connection || {};
        const sync = view?.sync || {};
        const transports = view?.transports || {};
        const cardInfo = view?.card || {};
        const discovered = view?.discovered_sessions || [];

        return h('div', { className: 'anisg-wrap' },
          h('div', { className: 'anisg-between', style: { marginBottom: '12px' } },
            h('div', null,
              h('h3', { style: { margin: '0 0 4px 0', fontSize: '16px' } }, `AniSignal 情报助手 v${view?.version || ''}`),
              h('div', { className: 'anisg-hint' }, '同时支持官方 QQ Bot (@tencent-connect/dsh-qqbot) 与 OneBot v11 / NapCat (dsh-onebot-qq / dsh-qq-onebot)'),
            ),
            h('div', { className: 'anisg-row' },
              h('button', { type: 'button', className: 'anisg-btn', onClick: loadState, disabled: loading }, '刷新'),
              h('button', { type: 'button', className: 'anisg-btn', onClick: handleSyncNow, disabled: syncing }, syncing ? '同步中…' : '立即同步'),
              h('button', { type: 'button', className: 'anisg-btn anisg-btn-primary', onClick: handleSave, disabled: saving }, saving ? '保存中…' : '保存并同步'),
            ),
          ),

          banner ? h('div', { className: `anisg-msg ${banner.kind === 'ok' ? 'anisg-msg-ok' : 'anisg-msg-err'}` }, banner.text) : null,

          h(PluginUpdates, {
            pluginContext,
            revision: view?.revision,
            onRevision: (revision) => setView((prev) => prev ? { ...prev, revision } : prev),
          }),

          h('div', { className: 'anisg-card' },
            h('div', { className: 'anisg-between' },
              h('div', { className: 'anisg-row' },
                h('span', { className: `anisg-badge ${sync.state === 'synced' ? 'anisg-badge-ok' : (sync.state === 'error' ? 'anisg-badge-err' : '')}` },
                  sync.state === 'synced' ? '已连接主节点' : (sync.state === 'error' ? '未同步' : '待配置 Token'),
                ),
                sync.identity?.user ? h('span', null, `账号：${sync.identity.user.display_name} (#${sync.identity.user.id})`) : null,
                sync.synced_at ? h('span', { className: 'anisg-hint' }, `上次同步：${sync.synced_at}`) : null,
              ),
              h('div', { className: 'anisg-row' },
                h('span', { className: `anisg-badge ${transports.qqbot?.ready ? 'anisg-badge-ok' : ''}` },
                  transports.qqbot?.ready ? `官方QQBot就绪 (*${transports.qqbot.appIdTail})` : '官方QQBot未绑定',
                ),
                h('span', { className: `anisg-badge ${transports.onebot?.ready ? 'anisg-badge-ok' : ''}` },
                  transports.onebot?.ready ? `OneBot就绪 (${transports.onebot.kind})` : 'OneBot未连接',
                ),
                h('span', { className: `anisg-badge ${cardInfo.available ? 'anisg-badge-ok' : ''}` },
                  cardInfo.available ? '早报图片卡就绪' : '早报纯文本模式',
                ),
              ),
            ),
            sync.error ? h('div', { style: { color: '#c94a4a', marginTop: '6px', fontSize: '12px' } }, sync.error) : null,
            sync.quota_summary ? h('div', { className: 'anisg-hint', style: { marginTop: '6px' } }, sync.quota_summary) : null,
            !cardInfo.available && cardInfo.reason ? h('div', { className: 'anisg-hint', style: { marginTop: '4px' } }, `提示：${cardInfo.reason}`) : null,
          ),

          h('div', { className: 'anisg-card' },
            h('strong', null, '节点鉴权与渲染设置'),
            h('div', { className: 'anisg-grid', style: { marginTop: '10px' } },
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, 'AniSignal 服务地址'),
                h('input', {
                  className: 'anisg-input',
                  value: form.base_url,
                  onChange: (e) => setForm({ ...form, base_url: e.target.value }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' },
                  conn.tools_key_set ? `用户 Token（已保存${conn.tools_key_tail ? ` *${conn.tools_key_tail}` : ''}，留空保持不变）` : '用户 Token（在 anisignal.xyz 账号中心创建）',
                ),
                h('input', {
                  type: 'password',
                  className: 'anisg-input',
                  placeholder: conn.tools_key_set ? '输入新 Token 以替换，留空保留原值' : '粘贴用户 Token',
                  value: form.tools_key,
                  onChange: (e) => setForm({ ...form, tools_key: e.target.value, clear_tools_key: false }),
                }),
                conn.tools_key_set ? h('label', { className: 'anisg-row anisg-hint' },
                  h('input', {
                    type: 'checkbox',
                    checked: form.clear_tools_key,
                    onChange: (e) => setForm({ ...form, clear_tools_key: e.target.checked }),
                  }),
                  h('span', null, '清空已保存的 Token'),
                ) : null,
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '自定义中文字体路径（可选）'),
                h('input', {
                  className: 'anisg-input',
                  placeholder: cardInfo.fontFile || '自动探测系统字体 (PingFang / NotoSansCJK / 微软雅黑)',
                  value: form.card_font_path,
                  onChange: (e) => setForm({ ...form, card_font_path: e.target.value }),
                }),
              ),
            ),

            h('div', { style: { marginTop: '6px' } },
              h('button', {
                type: 'button',
                className: 'anisg-btn',
                onClick: () => setShowChannelOverrides((v) => !v),
              }, showChannelOverrides ? '收起推送通道高级参数' : '展开推送通道高级参数（自动探测失败或反向 WS 时填写）'),
            ),

            showChannelOverrides ? h('div', { className: 'anisg-grid', style: { marginTop: '10px' } },
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, '官方 QQ Bot AppID（留空自动读取 im-qqbot 配置）'),
                h('input', {
                  className: 'anisg-input',
                  value: form.qqbot_app_id,
                  onChange: (e) => setForm({ ...form, qqbot_app_id: e.target.value }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' },
                  conn.qqbot_secret_set ? `官方 QQ Bot AppSecret（已存 *${conn.qqbot_secret_tail}）` : '官方 QQ Bot AppSecret（留空自动读取）',
                ),
                h('input', {
                  type: 'password',
                  className: 'anisg-input',
                  placeholder: '留空保留不变',
                  value: form.qqbot_app_secret,
                  onChange: (e) => setForm({ ...form, qqbot_app_secret: e.target.value }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, 'OneBot v11 HTTP 地址（推荐反向 WS 用户填写）'),
                h('input', {
                  className: 'anisg-input',
                  placeholder: '例如 http://127.0.0.1:3000',
                  value: form.onebot_http_url,
                  onChange: (e) => setForm({ ...form, onebot_http_url: e.target.value }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' }, 'OneBot v11 正向 WS 地址（留空自动读取 dsh-qq / onebot 配置）'),
                h('input', {
                  className: 'anisg-input',
                  placeholder: '例如 ws://127.0.0.1:3001',
                  value: form.onebot_ws_url,
                  onChange: (e) => setForm({ ...form, onebot_ws_url: e.target.value }),
                }),
              ),
              h('div', { className: 'anisg-field' },
                h('label', { className: 'anisg-label' },
                  conn.onebot_token_set ? `OneBot AccessToken（已存 *${conn.onebot_token_tail}）` : 'OneBot AccessToken（可选）',
                ),
                h('input', {
                  type: 'password',
                  className: 'anisg-input',
                  placeholder: '留空保留不变',
                  value: form.onebot_access_token,
                  onChange: (e) => setForm({ ...form, onebot_access_token: e.target.value }),
                }),
              ),
            ) : null,
          ),

          discovered.length > 0 ? h('div', { className: 'anisg-card' },
            h('strong', null, '从已发现的 QQ 会话一键添加'),
            h('div', { className: 'anisg-chips', style: { marginTop: '8px' } },
              discovered.map((item) => h('button', {
                key: item.umo,
                type: 'button',
                className: 'anisg-btn',
                onClick: () => setForm({
                  ...form,
                  targets: [...form.targets, makeEmptyTarget(item.umo, `${item.scope === 'group' ? '群聊' : '私聊'} ${item.peerId.slice(0, 10)}`)],
                }),
              }, `+ ${item.umo}`)),
            ),
          ) : null,

          h('div', { className: 'anisg-between', style: { margin: '14px 0 10px' } },
            h('strong', { style: { fontSize: '14px' } }, `订阅会话列表（共 ${form.targets.length} 个）`),
            h('button', {
              type: 'button',
              className: 'anisg-btn',
              onClick: () => setForm({ ...form, targets: [...form.targets, makeEmptyTarget()] }),
            }, '+ 手动添加会话'),
          ),

          form.targets.length === 0 ? h('div', { className: 'anisg-card anisg-hint' },
            '暂未配置任何会话。你可以先在 QQ 群聊或私聊里让机器人调用「查看当前情报会话标识」，或点击上方「+ 手动添加会话」。',
          ) : form.targets.map((target, idx) => h(TargetEditor, {
            key: `${idx}-${target.umo || 'new'}`,
            target,
            index: idx,
            catalog: view?.catalog,
            onChange: (updated) => {
              const copy = [...form.targets];
              copy[idx] = updated;
              setForm({ ...form, targets: copy });
            },
            onRemove: () => {
              const copy = form.targets.filter((_, i) => i !== idx);
              setForm({ ...form, targets: copy });
            },
          })),
        );
      }

      function AniSignalPage({ pluginContext }) {
        return h('div', { className: 'anisg-page' }, h(AniSignalSettingsSection, { pluginContext }));
      }

      function OpenAniSignalAction({ subject, packageName, onOpen }) {
        if (subject?.kind !== 'bundle' && subject?.kind !== 'row') return null;
        if (subject.pkg?.name !== packageName) return null;
        if (subject.kind === 'row' && subject.row?.moduleName !== packageName) return null;
        return h('button', {
          type: 'button',
          className: 'anisg-btn anisg-btn-primary',
          onClick: onOpen,
        }, '设置');
      }

      return {
        name: 'anisignal-client',
        inject: ['slots', 'remote', 'remote.pluginManager'],
        apply(ctx) {
          installStyles();
          const disposers = [];
          if (ctx.slots) {
            disposers.push(ctx.slots.inject('settings.section', () => ctx.slots.register({
              name: 'settings.section',
              id: 'anisignal',
              order: 8,
              label: 'AniSignal 情报助手',
              inject: () => ({ pluginContext: ctx }),
            }, AniSignalSettingsSection)));
            disposers.push(ctx.slots.inject('plugins.detail.section', () => ctx.slots.register({
              name: 'plugins.detail.section',
              id: 'anisignal-updates',
              order: 20,
              inject: () => ({ pluginContext: ctx }),
            }, AniSignalDetailUpdates)));
          }

          if (typeof ctx.inject === 'function') {
            ctx.inject(['layout'], (pageCtx) => {
              const pageSlots = pageCtx.slots;
              const layout = pageCtx.layout;
              if (!pageSlots || !layout) return undefined;
              const stopMain = pageSlots.inject('main', () => pageSlots.register({
                name: 'main',
                key: MAIN_PANEL_ID,
                inject: () => ({ pluginContext: pageCtx }),
              }, AniSignalPage));
              const stopAction = pageSlots.inject('plugins.detail.actions', () => pageSlots.register({
                name: 'plugins.detail.actions',
                id: 'anisignal-open-settings',
                order: 20,
                inject: () => ({
                  packageName: PACKAGE_NAME,
                  onOpen: () => layout.selectPanel(MAIN_PANEL_ID),
                }),
              }, OpenAniSignalAction));
              return () => {
                stopAction?.();
                stopMain?.();
              };
            });
          }

          if (typeof ctx.effect === 'function') {
            ctx.effect(() => () => {
              for (const d of disposers.reverse()) d?.();
            }, 'anisignal-client.slots');
          }
        },
      };
    },
  });
})();
