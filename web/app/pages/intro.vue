<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { mountProductExperience } from '~/utils/product-experience'

definePageMeta({ layout: false })
useSeoMeta({
  title: 'AniSignal · 聚合多平台更新，追踪你关心的变化',
  description: '关键词驱动的信息采集、事件追踪与订阅工具。多源采集、证据追溯、个人监控与商品进展，通过网站、邮件和 AstrBot QQ 群推送连接你的关注。',
  ogTitle: 'AniSignal · 让重要的变化，找到你',
  ogDescription: '从多平台信息到可追溯事件，再到关键词定制的关注流、邮件和 QQ 群推送。',
  ogType: 'website',
})
const pageRoot = ref<HTMLElement | null>(null)
let dispose: (() => void) | undefined
onMounted(() => {
  if (pageRoot.value) dispose = mountProductExperience(pageRoot.value)
})
onBeforeUnmount(() => dispose?.())
</script>

<template>
<div ref="pageRoot" class="as-product" data-motion="on">
<svg class="as-symbols" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><symbol id="as-i-arrow" viewBox="0 0 24 24"><path d="M5 12h14m-5-5 5 5-5 5"/></symbol>
<symbol id="as-i-external" viewBox="0 0 24 24"><path d="M7 17 17 7M7 7h10v10"/></symbol>
<symbol id="as-i-signal" viewBox="0 0 24 24"><path d="M5 15a4 4 0 0 1 4 4M5 9a10 10 0 0 1 10 10M5 3a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></symbol>
<symbol id="as-i-layers" viewBox="0 0 24 24"><path d="m12 3 10 6-10 6L2 9l10-6Zm-9 11 9 5 9-5M3 18l9 5 9-5"/></symbol>
<symbol id="as-i-search" viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></symbol>
<symbol id="as-i-link" viewBox="0 0 24 24"><path d="m10 13 4-4m-6 6-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 3 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(1 0)"/></symbol>
<symbol id="as-i-box" viewBox="0 0 24 24"><path d="m12 2 9 5v10l-9 5-9-5V7l9-5Zm0 10 9-5m-9 5L3 7m9 5v10M7 4.7l9 5"/></symbol>
<symbol id="as-i-filter" viewBox="0 0 24 24"><path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z"/></symbol>
<symbol id="as-i-spark" viewBox="0 0 24 24"><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/></symbol>
<symbol id="as-i-sliders" viewBox="0 0 24 24"><path d="M4 5h9m4 0h3M4 12h3m4 0h9M4 19h11m4 0h1"/><circle cx="15" cy="5" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="19" r="2"/></symbol>
<symbol id="as-i-chat" viewBox="0 0 24 24"><path d="M21 11a9 9 0 0 1-9 9H3l2-5a9 9 0 1 1 16-4Z"/><path d="M8 10h8m-8 4h5"/></symbol>
<symbol id="as-i-mail" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 7 9 6 9-6"/></symbol>
<symbol id="as-i-check" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></symbol>
<symbol id="as-i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></symbol>
<symbol id="as-i-shield" viewBox="0 0 24 24"><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></symbol>
<symbol id="as-i-pause" viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></symbol>
<symbol id="as-i-menu" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></symbol>
<symbol id="as-i-close" viewBox="0 0 24 24"><path d="m6 6 12 12M6 18 18 6"/></symbol></svg>
<a class="as-skip" href="#as-main">跳到主要内容</a>
<header class="as-header">
 <div class="as-nav as-wrap">
  <a href="/intro" class="as-brand" aria-label="AniSignal 产品介绍"><span class="as-brand-mark"><svg class="as-icon " aria-hidden="true"><use href="#as-i-signal" /></svg></span>AniSignal<span class="as-brand-label">产品介绍</span></a>
  <nav class="as-nav-links" aria-label="产品导航"><a href="#capabilities">核心能力</a><a href="#workflow">关键词定制</a><a href="#delivery">消息送达</a></nav>
  <div class="as-nav-actions"><button type="button" class="as-motion" data-motion-toggle aria-pressed="false" aria-label="暂停页面动效" hidden><svg class="as-icon " aria-hidden="true"><use href="#as-i-pause" /></svg><span>动效开启</span></button><a href="/" class="as-button as-button-small">进入工作台 <svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></a></div>
 </div><div class="as-reading-progress" aria-hidden="true"><i></i></div>
</header>
<main id="as-main">
<section class="as-hero as-wrap" aria-labelledby="as-title">
 <div class="as-hero-copy">
  <span class="as-eyebrow"><i></i> 关键词驱动 · 跨平台信息雷达</span>
  <h1 id="as-title">让重要的变化，<br><em>找到你。</em></h1>
  <p class="as-hero-description">信息分散在各个平台，关注不必如此。<br>从采集、筛选到事件追溯，把你关心的更新<br class="as-desktop-br">汇入一处，再送到你常用的地方。</p>
  <div class="as-hero-actions"><a class="as-button" href="/">打开信息工作台 <svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></a><a class="as-button as-button-quiet" href="#workflow">试试关键词定制 <svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a></div>
  <div class="as-hero-note"><span>按需配置</span><i></i><span>来源可追溯</span><i></i><span>多通道送达</span></div>
  <a href="#delivery" class="as-astrbot-notice"><span class="as-notice-icon"><svg class="as-icon " aria-hidden="true"><use href="#as-i-chat" /></svg></span><span><b>已接入 AstrBot</b> · 支持 QQ 群定制推送</span><svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></a>
 </div>
 <div class="as-hero-visual" data-stack="stacked" data-stack-root>
  <div class="as-visual-label"><span><i></i> THE SIGNAL STACK</span><span>交互演示 · 示例数据</span></div>
  <div class="as-card-stage" data-stage>
   <div class="as-orbit as-orbit-one" aria-hidden="true"></div><div class="as-orbit as-orbit-two" aria-hidden="true"></div>
   <span class="as-stage-caption" aria-hidden="true">分散的来源<br><b>有序的线索</b></span>
   <div class="as-card-space" data-card-space><button type="button" class="as-source-card as-card-bili" data-card="0" style="--i:0" aria-pressed="false" aria-label="查看Bilibili示例卡片" data-detail-title="源头可回看。" data-detail-text="这张卡片代表一条账号动态。实际事件会保留来源身份和原始链接，便于回到源头核对。">
  <span class="as-card-top"><span class="as-platform-mark">B</span><span>Bilibili</span><span class="as-card-number">01</span></span>
  <span class="as-card-graphic" aria-hidden="true"><span></span><span></span><span></span><i></i></span>
  <span class="as-card-category">账号动态</span><strong>版本更新公告</strong><span class="as-card-body">发布说明与内容链接</span>
  <span class="as-card-lines" aria-hidden="true"><i></i><i></i></span><span class="as-card-time">12:00 · 示例来源</span>
</button><button type="button" class="as-source-card as-card-web" data-card="1" style="--i:1" aria-pressed="false" aria-label="查看官网公告示例卡片" data-detail-title="不是再贴一次链接。" data-detail-text="符合规则的官方内容可以归并为事件；归并并不删除每条内容的原始来源。">
  <span class="as-card-top"><span class="as-platform-mark">W</span><span>官网公告</span><span class="as-card-number">02</span></span>
  <span class="as-card-graphic" aria-hidden="true"><span></span><span></span><span></span><i></i></span>
  <span class="as-card-category">原始来源</span><strong>更新安排已公布</strong><span class="as-card-body">发布时间与关键说明</span>
  <span class="as-card-lines" aria-hidden="true"><i></i><i></i></span><span class="as-card-time">12:05 · 示例来源</span>
</button><button type="button" class="as-source-card as-card-youtube" data-card="2" style="--i:2" aria-pressed="false" aria-label="查看YouTube RSS示例卡片" data-detail-title="多源阅读，保留差异。" data-detail-text="频道更新可以统一浏览。是否归并取决于规则，不代表任意跨语言内容都会自动合并。">
  <span class="as-card-top"><span class="as-platform-mark">Y</span><span>YouTube RSS</span><span class="as-card-number">03</span></span>
  <span class="as-card-graphic" aria-hidden="true"><span></span><span></span><span></span><i></i></span>
  <span class="as-card-category">频道更新</span><strong>同主题频道更新</strong><span class="as-card-body">另一平台的公开信息</span>
  <span class="as-card-lines" aria-hidden="true"><i></i><i></i></span><span class="as-card-time">12:12 · 示例来源</span>
</button><button type="button" class="as-source-card as-card-community" data-card="3" style="--i:3" aria-pressed="false" aria-label="查看米游社示例卡片" data-detail-title="线索与结论分开。" data-detail-text="社区信息保留各自身份。未经核验的线索不能被写成已确认的商品进展。">
  <span class="as-card-top"><span class="as-platform-mark">M</span><span>米游社</span><span class="as-card-number">04</span></span>
  <span class="as-card-graphic" aria-hidden="true"><span></span><span></span><span></span><i></i></span>
  <span class="as-card-category">社区来源</span><strong>补充说明与反馈</strong><span class="as-card-body">上下文与独立线索</span>
  <span class="as-card-lines" aria-hidden="true"><i></i><i></i></span><span class="as-card-time">12:20 · 示例来源</span>
</button><button type="button" class="as-source-card as-card-event" data-card="4" style="--i:4" aria-pressed="false" aria-label="查看AniSignal示例卡片" data-detail-title="看见结论，也看见依据。" data-detail-text="事件视图集中展示关键事实、相关证据与修订记录。不是所有采集内容都会进入事件引擎。">
  <span class="as-card-top"><span class="as-platform-mark">A</span><span>AniSignal</span><span class="as-card-number">05</span></span>
  <span class="as-card-graphic" aria-hidden="true"><span></span><span></span><span></span><i></i></span>
  <span class="as-card-category">事件视图</span><strong>版本更新安排</strong><span class="as-card-body">从分散来源，到一条可追踪的事件</span>
  <span class="as-card-lines" aria-hidden="true"><i></i><i></i></span><span class="as-card-time">演示事件 · 非实时资讯</span>
</button></div>
   <div class="as-stage-ground" aria-hidden="true"></div>
  </div>
  <div class="as-stack-controls" data-stack-controls hidden><div class="as-segment" role="group" aria-label="卡片展开方式"><button type="button" data-stack-action="stacked" aria-pressed="true">收拢</button><button type="button" data-stack-action="spread" aria-pressed="false">展开来源 <svg class="as-icon " aria-hidden="true"><use href="#as-i-layers" /></svg></button></div><span>点选卡片，前移查看</span></div>
  <div class="as-inspector" aria-live="polite" aria-atomic="true"><span class="as-inspector-icon"><svg class="as-icon " aria-hidden="true"><use href="#as-i-link" /></svg></span><div><strong data-evidence-title>看见结论，也看见依据。</strong><p data-evidence-text>每张卡片代表一种来源。展开后点选，查看它在信息链路中的作用。</p></div><button type="button" class="as-icon-button" data-close-card aria-label="关闭卡片查看，回到展开状态" hidden><svg class="as-icon " aria-hidden="true"><use href="#as-i-close" /></svg></button></div>
  <noscript class="as-noscript">开启 JavaScript 可展开卡片并体验筛选；页面介绍与入口仍可正常阅读。</noscript>
 </div>
</section>
<div class="as-source-strip as-wrap"><span>多来源输入，统一阅读</span><div><b>Bilibili</b><b>米游社</b><b>YouTube <small>RSS</small></b><b>官网公告<small>已适配来源</small></b></div><a href="/sources">查看来源与状态 <svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a></div>
<section class="as-section as-wrap" id="capabilities" aria-labelledby="as-capabilities-title">
 <div class="as-section-heading" data-reveal><div><span class="as-overline">01 / THE WHOLE PICTURE</span><h2 id="as-capabilities-title">不止收到消息，<br>更能理解来龙去脉。</h2></div><p>采集、整理、追踪与送达，组成完整的信息链路。<br>当前展示什么，由关键词与采集配置决定。</p></div>
 <div class="as-features"><article class="as-feature as-feature-collection" data-reveal>
 <div class="as-feature-head"><svg class="as-icon " aria-hidden="true"><use href="#as-i-layers" /></svg><span>01</span></div><p class="as-feature-kicker">多源采集与降噪</p>
 <h3>先筛选，再汇入。</h3><p>按关键词、账号或公告来源采集。用共现词、热度门槛与分类规则，让信息流更贴近你的关注范围。</p><div class="as-mini" aria-hidden="true"><div class="as-mini-tags"><span>关键词</span><span>账号动态</span><span>官网公告</span></div><div class="as-mini-flow"><i></i><i></i><i></i><b>筛选</b><i></i></div></div><a href="/contents" class="as-text-link">浏览内容库<svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a>
</article>
<article class="as-feature as-feature-evidence" data-reveal>
 <div class="as-feature-head"><svg class="as-icon " aria-hidden="true"><use href="#as-i-link" /></svg><span>02</span></div><p class="as-feature-kicker">事件归并与证据</p>
 <h3>每个结论，都有来路。</h3><p>符合规则的来源归并为事件。关键事实、原始链接和修订记录放在一起，人工修正优先。</p><div class="as-mini" aria-hidden="true"><div class="as-mini-evidence"><span><i></i>原始来源</span><span><i></i>事件事实</span><span><i></i>修订记录</span></div></div><a href="/events" class="as-text-link">查看事件<svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a>
</article>
<article class="as-feature as-feature-fulfillment" data-reveal>
 <div class="as-feature-head"><svg class="as-icon " aria-hidden="true"><use href="#as-i-box" /></svg><span>03</span></div><p class="as-feature-kicker">商品进展追踪</p>
 <h3>追进展，不猜结果。</h3><p>分开查看官方进展、消费者到货与质量反馈。候选线索保留，但不冒充已核验结论。</p><div class="as-mini" aria-hidden="true"><div class="as-mini-axes"><span>官方<i></i></span><span>到货<i></i></span><span>反馈<i></i></span></div></div><a href="/products" class="as-text-link">查看商品进展<svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a>
</article>
<article class="as-feature as-feature-personal" data-reveal>
 <div class="as-feature-head"><svg class="as-icon " aria-hidden="true"><use href="#as-i-filter" /></svg><span>04</span></div><p class="as-feature-kicker">关注流与独立监控</p>
 <h3>你的关注，有自己的范围。</h3><p>关注词筛选站内内容；独立的 B 站 UID 或关键词监控另行采集，可选择公开或私有。</p><div class="as-mini" aria-hidden="true"><div class="as-mini-tags"><span>我的关注</span><span>公开 / 私有</span><span>独立存储</span></div></div><a href="/account" class="as-text-link">管理我的关注<svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a>
</article>
<article class="as-feature as-feature-ai" data-reveal>
 <div class="as-feature-head"><svg class="as-icon " aria-hidden="true"><use href="#as-i-spark" /></svg><span>05</span></div><p class="as-feature-kicker">可选 AI 辅助</p>
 <h3>少一点翻译和整理。</h3><p>为内容增加摘要、翻译和筛选，保留来源供核对。模型服务由部署者配置，不代替事实判断。</p><div class="as-mini" aria-hidden="true"><div class="as-mini-summary"><span>原始信息</span><i></i><b>摘要 · 翻译 · 筛选</b></div></div><a href="/events" class="as-text-link">阅读事件摘要<svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a>
</article>
<article class="as-feature as-feature-ops" data-reveal>
 <div class="as-feature-head"><svg class="as-icon " aria-hidden="true"><use href="#as-i-sliders" /></svg><span>06</span></div><p class="as-feature-kicker">管理与来源健康</p>
 <h3>看得到内容，也看得到状态。</h3><p>维护采集目标、分类、用户与站点配置；检查来源健康、AI 用量和通知投递记录。</p><div class="as-mini" aria-hidden="true"><div class="as-mini-status"><span><i></i>来源健康</span><span>采集记录</span><span>投递记录</span></div></div><a href="/sources" class="as-text-link">查看来源健康<svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a>
</article></div>
</section>
<section class="as-workflow-section" id="workflow" aria-labelledby="as-workflow-title"><div class="as-wrap as-workflow">
 <div class="as-workflow-copy" data-reveal><span class="as-overline">02 / MAKE IT YOURS</span><h2 id="as-workflow-title">关注的范围，<br>由你来定义。</h2><p>同样是关键词，用途却不同。<br>选一种方式，看看信息会如何进入你的视野。</p>
  <div class="as-mode-list" role="tablist" aria-label="关键词使用方式" data-mode-tabs>
   <button type="button" id="as-mode-collect" role="tab" aria-selected="true" aria-controls="as-mode-panel" data-mode="collect"><span>01</span><span class="as-mode-text"><b>站点采集</b><small>管理员定义公共采集范围</small></span><svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></button>
   <button type="button" id="as-mode-watch" role="tab" aria-selected="false" aria-controls="as-mode-panel" tabindex="-1" data-mode="watch"><span>02</span><span class="as-mode-text"><b>个人关注</b><small>从站内已有内容中筛选</small></span><svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></button>
   <button type="button" id="as-mode-custom" role="tab" aria-selected="false" aria-controls="as-mode-panel" tabindex="-1" data-mode="custom"><span>03</span><span class="as-mode-text"><b>独立监控</b><small>B 站 UID / 关键词，单独采集</small></span><svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></button>
  </div>
 </div>
 <div class="as-keyword-demo" data-reveal>
  <div class="as-demo-top"><span><svg class="as-icon " aria-hidden="true"><use href="#as-i-filter" /></svg> 关键词工作台</span><span class="as-demo-label">交互演示</span></div>
  <div id="as-mode-panel" role="tabpanel" aria-labelledby="as-mode-collect" tabindex="0"><span class="as-mini-label" data-mode-tag>站点采集 · 公共内容库</span><h3 data-mode-title>先定义范围，再收集信息。</h3><p data-mode-description>管理员通过关键词、账号和来源配置采集任务。这里仅模拟关键词筛选，不执行采集。</p></div>
  <label class="as-input-label" for="as-keyword">试试一个关键词</label><div class="as-search-field"><svg class="as-icon " aria-hidden="true"><use href="#as-i-search" /></svg><input type="text" id="as-keyword" value="版本" maxlength="40" autocomplete="off" placeholder="输入版本、发货、活动…" aria-describedby="as-demo-privacy"><button type="button" class="as-icon-button" data-clear-keyword aria-label="清空演示关键词"><svg class="as-icon " aria-hidden="true"><use href="#as-i-close" /></svg></button></div>
  <div class="as-keyword-presets" role="group" aria-label="示例关键词"><button type="button" data-keyword="版本" aria-pressed="true"># 版本</button><button type="button" data-keyword="发货" aria-pressed="false"># 发货</button><button type="button" data-keyword="活动" aria-pressed="false"># 活动</button></div>
  <div class="as-results-heading"><span>匹配的示例内容</span><output data-result-count aria-live="polite">2 条</output></div>
  <div class="as-demo-results">
   <article data-demo-result data-platform="official" data-keywords="版本 更新 公告"><span class="as-result-icon">W</span><div><h4>版本更新公告</h4><p>官网公告 · 示例内容</p></div><span><svg class="as-icon " aria-hidden="true"><use href="#as-i-link" /></svg></span></article>
   <article data-demo-result data-platform="bilibili" data-keywords="版本 补充 说明"><span class="as-result-icon as-result-blue">B</span><div><h4>版本补充说明</h4><p>B 站账号 · 示例内容</p></div><span><svg class="as-icon " aria-hidden="true"><use href="#as-i-link" /></svg></span></article>
   <article data-demo-result data-platform="bilibili" data-keywords="发货 商品 进度" hidden><span class="as-result-icon as-result-blue">B</span><div><h4>商品发货进度说明</h4><p>B 站账号 · 示例内容</p></div><span><svg class="as-icon " aria-hidden="true"><use href="#as-i-link" /></svg></span></article>
   <article data-demo-result data-platform="community" data-keywords="活动 报名 开放" hidden><span class="as-result-icon">M</span><div><h4>活动报名开放</h4><p>社区来源 · 示例内容</p></div><span><svg class="as-icon " aria-hidden="true"><use href="#as-i-link" /></svg></span></article>
   <p class="as-empty" data-empty-results hidden>示例中没有匹配结果。换个词试试，或清空筛选。</p>
  </div>
  <p class="as-demo-footnote" id="as-demo-privacy">纯本地演示：不保存关键词、不连接账号、不发起采集。</p><a href="/account" class="as-text-link">前往我的关注 <svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></a>
 </div>
</div></section>
<section class="as-delivery-section" id="delivery" aria-labelledby="as-delivery-title"><div class="as-wrap">
 <div class="as-delivery-heading" data-reveal><div><span class="as-overline">03 / MEET YOU WHERE YOU ARE</span><h2 id="as-delivery-title">不必一直守着网页。<br><em>更新，也能来到 QQ 群。</em></h2></div><p>AstrBot 连接信息与会话。<br>按群配置分区、关键词、速报时间与免打扰，<br>网站和邮件，仍然是同一套产品的重要入口。</p></div>
 <div class="as-delivery-grid">
  <div class="as-delivery-options" data-reveal><div class="as-channel-tabs" role="tablist" aria-label="消息通道" data-channel-tabs><button type="button" role="tab" id="as-channel-qq" aria-controls="as-channel-panel" aria-selected="true" data-channel="qq"><svg class="as-icon " aria-hidden="true"><use href="#as-i-chat" /></svg> QQ 群</button><button type="button" role="tab" id="as-channel-email" aria-controls="as-channel-panel" aria-selected="false" tabindex="-1" data-channel="email"><svg class="as-icon " aria-hidden="true"><use href="#as-i-mail" /></svg> 邮件</button><button type="button" role="tab" id="as-channel-web" aria-controls="as-channel-panel" aria-selected="false" tabindex="-1" data-channel="web"><svg class="as-icon " aria-hidden="true"><use href="#as-i-layers" /></svg> 站内</button></div>
   <div id="as-channel-panel" role="tabpanel" aria-labelledby="as-channel-qq" tabindex="0"><span class="as-integration-label" data-channel-label>ANISIGNAL × ASTRBOT</span><h3 data-channel-title>每个群，都有自己的关注。</h3><p data-channel-description>通过已配置的 AstrBot QQ 通道，接收定时速报或重要更新，也能按需查询资讯与商品进展。</p></div>
   <div class="as-routing" aria-hidden="true"><span>AniSignal</span><i></i><span data-route-middle>AstrBot</span><i></i><b data-route-end>QQ 群</b></div>
   <div class="as-delivery-keywords" role="group" aria-label="推送预览关键词"><span>演示关注词</span><button type="button" data-delivery-topic="version" aria-pressed="true"># 版本更新</button><button type="button" data-delivery-topic="shipping" aria-pressed="false"># 商品进展</button></div>
   <button type="button" class="as-button as-button-white" data-preview-delivery>预览一条群推送 <svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></button>
   <p class="as-delivery-disclaimer">只演示消息呈现，不向真实群或邮箱发送。实际投递需配置对应通道。</p>
  </div>
  <div class="as-message-preview" data-channel-view="qq" data-reveal>
   <div class="as-message-top"><span class="as-chat-avatar"><svg class="as-icon " aria-hidden="true"><use href="#as-i-chat" /></svg></span><div><strong data-preview-heading>产品演示群</strong><span data-preview-subheading>AstrBot · 关键词定制</span></div><span class="as-preview-badge">示例会话</span></div>
   <div class="as-chat-body"><div class="as-chat-date">消息预览 · 非真实推送</div><div class="as-user-bubble" data-user-bubble>我关心的版本，有新消息吗？</div><div class="as-bot-message" data-bot-message><span class="as-bot-avatar">A</span><div><p class="as-bot-name" data-bot-name>AniSignal / AstrBot</p><div class="as-bot-bubble"><span class="as-bubble-topic" data-bubble-topic>关键词命中 · 版本更新</span><h4 data-message-title>关注的版本，有一条新进展。</h4><p data-message-body>将相关更新整理后送到群里，附上来源与时间，方便继续追溯。</p><div class="as-message-source"><span><svg class="as-icon " aria-hidden="true"><use href="#as-i-link" /></svg> <span data-message-source>来源与时间随消息保留</span></span><b>演示</b></div><a href="/events" class="as-message-link" data-message-link>到工作台查看真实事件 <svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a></div></div></div><p class="as-delivery-feedback" data-delivery-feedback aria-live="polite">选择关键词，再点击预览，体验不同消息内容。</p></div>
   <div class="as-message-bottom"><svg class="as-icon " aria-hidden="true"><use href="#as-i-shield" /></svg> <span data-message-footer>群内设置由管理员授权；信息保留原始出处。</span></div>
  </div>
 </div>
 <div class="as-delivery-bottom"><span>有来源的速报</span><span>按会话定制关键词</span><span>定时与免打扰</span><span>投递状态可查</span><a href="/admin/bot">配置机器人通道 <svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a></div>
</div></section>
<section class="as-section as-wrap as-trust" aria-labelledby="as-trust-title"><div class="as-section-heading" data-reveal><div><span class="as-overline">04 / BUILT TO BE TRACEABLE</span><h2 id="as-trust-title">不只给你一个答案，<br>也留下判断的依据。</h2></div><p>保留原始信息，区分候选与核验。<br>当结论变化，修订也有迹可循。</p></div>
 <div class="as-trace-line" data-reveal><div><span>01</span><h3>采集原始来源</h3><p>记录出处与采集范围</p></div><i><svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></i><div><span>02</span><h3>保留解析版本</h3><p>整理可以更新，来源不丢失</p></div><i><svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></i><div><span>03</span><h3>关联事件证据</h3><p>区分线索与已核验结论</p></div><i><svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></i><div><span>04</span><h3>回看修订与原文</h3><p>让每次判断有据可查</p></div></div>
 <div class="as-faq" id="questions"><h3>开始之前，你可能想知道。</h3><div>
  <details><summary>只能关注现在首页里的作品或商品吗？<span>+</span></summary><p>不是。内容范围由关键词、账号与采集目标决定。但新增平台仍需要适配器，分区也需要注册与配置；并非输入任何网址就能自动接入。</p></details>
  <details><summary>关注词、定制监控和群关键词有什么区别？<span>+</span></summary><p>关注词筛选站内已采集内容；定制监控创建独立的 B 站 UID 或关键词采集任务；群关键词限定该会话的消息范围，不会自动扩大站点采集范围。</p></details>
  <details><summary>启用 QQ 群推送，需要什么？<span>+</span></summary><p>需要部署 AstrBot、配置可用的 QQ 平台连接与 AniSignal 插件，并由管理员设置会话权限、关键词和投递规则。此页不会申请 QQ 登录或发送测试消息。</p></details>
  <details><summary>信息是实时、完整且已经核验的吗？<span>+</span></summary><p>不是。更新取决于已接入来源与采集周期；AI 输出需要核对，商品候选线索不等于已核验事实，也不能用于预测个人订单发货时间。邮件、AI 与机器人能力依赖实际部署配置。</p></details>
 </div></div>
</section>
<section class="as-final as-wrap" data-reveal><div class="as-final-orbits" aria-hidden="true"></div><span class="as-overline">LESS NOISE. MORE SIGNAL.</span><h2>把注意力，<br>留给真正关心的变化。</h2><p>从一个关键词开始，找到适合自己的信息路径。</p><div><a href="/" class="as-button">进入信息工作台 <svg class="as-icon " aria-hidden="true"><use href="#as-i-arrow" /></svg></a><a href="/account" class="as-button as-button-quiet">建立我的关注 <svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a></div></section>
</main>
<footer class="as-footer as-wrap"><div><a href="/intro" class="as-brand"><span class="as-brand-mark"><svg class="as-icon " aria-hidden="true"><use href="#as-i-signal" /></svg></span>AniSignal</a><p>关键词驱动的信息采集、事件追踪与订阅工具。</p></div><nav aria-label="页脚导航"><a href="/about">关于与边界</a><a href="/sources">来源健康</a><a href="https://github.com/tianyin231/AniSignal" target="_blank" rel="noopener noreferrer">GitHub <svg class="as-icon " aria-hidden="true"><use href="#as-i-external" /></svg></a></nav><span class="as-footer-note">产品试运行 · 功能以当前部署为准</span></footer>
</div>
</template>

<style src="../assets/css/product.css"></style>
