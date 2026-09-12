/**
 * Progressive enhancement for /intro. All state is local to the supplied root.
 * No API calls, storage writes, credentials, scraping or actual notifications.
 * Kept framework-independent so the exact interactions can be browser-tested.
 */
export function mountProductExperience(root: HTMLElement): () => void {
  const controller = new AbortController()
  const signal = controller.signal
  const get = <T extends Element>(selector: string): T => {
    const element = root.querySelector<T>(selector)
    if (!element) throw new Error(`Missing product-page element: ${selector}`)
    return element
  }
  const all = <T extends Element>(selector: string) => Array.from(root.querySelectorAll<T>(selector))
  const text = (selector: string, value: string) => { get<HTMLElement>(selector).textContent = value }
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)')
  const motionButton = get<HTMLButtonElement>('[data-motion-toggle]')
  const stage = get<HTMLElement>('[data-stage]')
  const space = get<HTMLElement>('[data-card-space]')
  const stack = get<HTMLElement>('[data-stack-root]')
  const cards = all<HTMLButtonElement>('[data-card]')
  const stackActions = all<HTMLButtonElement>('[data-stack-action]')
  const closeCard = get<HTMLButtonElement>('[data-close-card]')
  let selectedCard = -1
  let userPaused = false
  let motion = !reduceMotion.matches
  let pointerFrame = 0
  let scrollFrame = 0
  let previewFrame = 0
  let previewTimer = 0
  let disposed = false

  function resetTilt() {
    window.cancelAnimationFrame(pointerFrame)
    space.style.removeProperty('--as-rx')
    space.style.removeProperty('--as-ry')
  }
  function updateMotion() {
    motion = !userPaused && !reduceMotion.matches
    root.dataset.motion = motion ? 'on' : 'off'
    motionButton.disabled = reduceMotion.matches
    motionButton.setAttribute('aria-pressed', String(!motion))
    motionButton.setAttribute('aria-label', reduceMotion.matches ? '系统已启用减少动效' : motion ? '暂停页面动效' : '开启页面动效')
    text('[data-motion-toggle] span', reduceMotion.matches ? '静态模式' : motion ? '动效开启' : '动效暂停')
    if (!motion) resetTilt()
  }
  motionButton.hidden = false
  motionButton.addEventListener('click', () => { userPaused = !userPaused; updateMotion() }, { signal })
  reduceMotion.addEventListener('change', updateMotion, { signal })
  updateMotion()

  function setStack(mode: 'stacked' | 'spread' | 'focused') {
    stack.dataset.stack = mode
    stackActions.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.stackAction === (mode === 'stacked' ? 'stacked' : 'spread'))))
    if (mode !== 'focused') {
      selectedCard = -1
      cards.forEach(card => card.setAttribute('aria-pressed', 'false'))
      closeCard.hidden = true
      text('[data-evidence-title]', mode === 'stacked' ? '看见结论，也看见依据。' : '展开来源，保留每一层信息。')
      text('[data-evidence-text]', mode === 'stacked' ? '每张卡片代表一种来源。展开后点选，查看它在信息链路中的作用。' : '点击卡片前移查看，再关闭归队。键盘可用左右方向键切换，Esc 返回。')
    }
    resetTilt()
  }
  function inspectCard(index: number) {
    const card = cards[index]
    if (!card) return
    selectedCard = index
    setStack('focused')
    cards.forEach((item, i) => item.setAttribute('aria-pressed', String(i === index)))
    closeCard.hidden = false
    text('[data-evidence-title]', card.dataset.detailTitle || '来源详情')
    text('[data-evidence-text]', card.dataset.detailText || '')
  }
  get<HTMLElement>('[data-stack-controls]').hidden = false
  stackActions.forEach(button => button.addEventListener('click', () => setStack(button.dataset.stackAction === 'stacked' ? 'stacked' : 'spread'), { signal }))
  cards.forEach((card, index) => {
    card.addEventListener('click', () => selectedCard === index ? setStack('spread') : inspectCard(index), { signal })
    card.addEventListener('keydown', event => {
      let next = index
      if (event.key === 'ArrowRight') next = (index + 1) % cards.length
      else if (event.key === 'ArrowLeft') next = (index + cards.length - 1) % cards.length
      else if (event.key === 'Home') next = 0
      else if (event.key === 'End') next = cards.length - 1
      else return
      event.preventDefault()
      inspectCard(next)
      cards[next]?.focus({ preventScroll: true })
    }, { signal })
  })
  function closeInspection() {
    const index = selectedCard
    setStack('spread')
    cards[index]?.focus({ preventScroll: true })
  }
  closeCard.addEventListener('click', closeInspection, { signal })
  stack.addEventListener('keydown', event => {
    if (event.key === 'Escape' && selectedCard !== -1) {
      event.preventDefault()
      closeInspection()
    }
  }, { signal })
  stage.addEventListener('pointermove', event => {
    if (!motion || !finePointer.matches || event.pointerType === 'touch' || selectedCard !== -1) return
    const rect = stage.getBoundingClientRect()
    const x = Math.max(-.5, Math.min(.5, (event.clientX - rect.left) / rect.width - .5))
    const y = Math.max(-.5, Math.min(.5, (event.clientY - rect.top) / rect.height - .5))
    window.cancelAnimationFrame(pointerFrame)
    pointerFrame = window.requestAnimationFrame(() => {
      if (disposed) return
      space.style.setProperty('--as-rx', `${-y * 5}deg`)
      space.style.setProperty('--as-ry', `${x * 6}deg`)
    })
  }, { signal, passive: true })
  stage.addEventListener('pointerleave', resetTilt, { signal })
  stage.addEventListener('pointercancel', resetTilt, { signal })

  function bindTabs(selector: string, select: (button: HTMLButtonElement) => void) {
    const buttons = all<HTMLButtonElement>(`${selector} [role="tab"]`)
    function activate(button: HTMLButtonElement) {
      buttons.forEach(item => {
        item.setAttribute('aria-selected', String(item === button))
        item.tabIndex = item === button ? 0 : -1
      })
      select(button)
    }
    buttons.forEach((button, index) => {
      button.addEventListener('click', () => activate(button), { signal })
      button.addEventListener('keydown', event => {
        let next: number
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % buttons.length
        else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + buttons.length) % buttons.length
        else if (event.key === 'Home') next = 0
        else if (event.key === 'End') next = buttons.length - 1
        else return
        event.preventDefault()
        const target = buttons[next]
        if (target) { activate(target); target.focus() }
      }, { signal })
    })
  }
  const modes = {
    collect: { tag: '站点采集 · 公共内容库', title: '先定义范围，再收集信息。', description: '管理员通过关键词、账号和来源配置采集任务。这里仅模拟关键词筛选，不执行采集。' },
    watch: { tag: '个人关注 · 已采集内容', title: '把已有信息，变成你的关注流。', description: '关注词按标题筛选站内已有内容，不会新建采集任务，也不代表全网检索。' },
    custom: { tag: '独立监控 · B 站专属采集', title: '建立独立监控，结果单独保存。', description: 'B 站 UID / 关键词监控独立采集，可设公开或私有。本例只筛选 B 站示例，不进入主事件链路。' },
  }
  let mode: keyof typeof modes = 'collect'
  const keyword = get<HTMLInputElement>('#as-keyword')
  const resultRows = all<HTMLElement>('[data-demo-result]')
  const presets = all<HTMLButtonElement>('[data-keyword]')
  let resultTick = 0
  function filterResults(animateRows = false) {
    const needle = keyword.value.trim().toLocaleLowerCase()
    let count = 0
    let visibleIndex = 0
    resultRows.forEach(row => {
      const title = row.querySelector('h4')?.textContent || ''
      const haystack = mode === 'watch' ? title : `${title} ${row.dataset.keywords || ''}`
      const matches = (mode !== 'custom' || row.dataset.platform === 'bilibili') && haystack.toLocaleLowerCase().includes(needle)
      row.hidden = !matches
      if (matches) {
        row.style.setProperty('--row-i', String(visibleIndex++))
        count++
      }
    })
    text('[data-result-count]', `${count} 条`)
    get<HTMLElement>('[data-empty-results]').hidden = count !== 0
    presets.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.keyword?.toLocaleLowerCase() === needle)))
    if (animateRows) {
      const container = get<HTMLElement>('.as-demo-results')
      container.classList.remove('as-rows-in')
      void container.offsetWidth
      container.classList.add('as-rows-in')
      resultTick++
    }
  }
  bindTabs('[data-mode-tabs]', button => {
    const next = button.dataset.mode
    if (next !== 'collect' && next !== 'watch' && next !== 'custom') return
    mode = next
    const config = modes[next]
    swapPanel(get<HTMLElement>('#as-mode-panel'), () => {
      text('[data-mode-tag]', config.tag)
      text('[data-mode-title]', config.title)
      text('[data-mode-description]', config.description)
      get<HTMLElement>('#as-mode-panel').setAttribute('aria-labelledby', button.id)
      filterResults(true)
    })
  })
  keyword.addEventListener('input', filterResults, { signal })
  get<HTMLButtonElement>('[data-clear-keyword]').addEventListener('click', () => { keyword.value = ''; filterResults(); keyword.focus() }, { signal })
  presets.forEach(button => button.addEventListener('click', () => { keyword.value = button.dataset.keyword || ''; filterResults(true) }, { signal }))
  filterResults()

  const channels = {
    qq: { label: 'ANISIGNAL × ASTRBOT', title: '每个群，都有自己的关注。', description: '通过已配置的 AstrBot QQ 通道，接收定时速报或重要更新，也能按需查询资讯与商品进展。', heading: '产品演示群', subheading: 'AstrBot · 关键词定制', middle: 'AstrBot', end: 'QQ 群', action: '预览一条群推送 ', bot: 'AniSignal / AstrBot', footer: '群内设置由管理员授权；信息保留原始出处。' },
    email: { label: 'ANISIGNAL × EMAIL', title: '一封邮件，跟上关注的进展。', description: '接收全站早报，或按需请求关键词邮件。通知偏好和投递记录在账号中心管理，需要先配置 SMTP。', heading: '我的邮件', subheading: '按需关键词通知', middle: 'SMTP', end: '邮箱', action: '预览一封关键词邮件 ', bot: 'AniSignal · 邮件预览', footer: '全站早报与关键词邮件规则不同；需配置 SMTP。' },
    web: { label: 'ANISIGNAL WORKSPACE', title: '在工作台里，继续深挖。', description: '浏览个人关注流，回看内容与证据，再沿事件时间线追踪变化。这里是信息的归档与阅读入口。', heading: '我的关注流', subheading: '站内已采集内容', middle: '关注词', end: '站内', action: '预览站内关注 ', bot: 'AniSignal · 关注内容', footer: '关注流不新增采集任务；独立监控走另一条链路。' },
  }
  let channel: keyof typeof channels = 'qq'
  let topic: 'version' | 'shipping' = 'version'
  const previewButton = get<HTMLButtonElement>('[data-preview-delivery]')
  const topicButtons = all<HTMLButtonElement>('[data-delivery-topic]')

  // 面板内容切换：淡出 → 更新 → 滑入，避免瞬变
  function swapPanel(el: HTMLElement, update: () => void) {
    if (!motion) { update(); return }
    el.classList.add('as-swap-out')
    window.setTimeout(() => {
      if (disposed) return
      update()
      el.classList.remove('as-swap-out')
      el.classList.add('as-swap-in')
      window.setTimeout(() => el.classList.remove('as-swap-in'), 320)
    }, 130)
  }
  function renderMessage() {
    const shipping = topic === 'shipping'
    const label = shipping ? '商品进展' : '版本更新'
    text('[data-bubble-topic]', `关键词命中 · ${label}`)
    text('[data-user-bubble]', shipping ? '我关注的商品，有新进展吗？' : '我关心的版本，有新消息吗？')
    text('[data-message-title]', shipping ? '发现一条商品进展线索。' : '关注的版本，有一条新进展。')
    text('[data-message-body]', shipping ? '把官方说明与社区到货线索分开呈现。候选信息仍待核验，不据此预测个人订单的发货日期。' : channel === 'web' ? '在已采集内容中找到匹配更新，继续查看详情、来源和关联事件。关注流不会扩大采集范围。' : channel === 'email' ? '将命中关注词的更新整理进邮件，保留来源与时间。定时全站早报是另一种通知方式。' : '将相关更新整理后送到群里，附上来源与时间，方便继续追溯。')
    const link = get<HTMLAnchorElement>('[data-message-link]')
    link.href = shipping ? '/products' : channel === 'web' ? '/account' : '/events'
    if (link.firstChild) link.firstChild.textContent = shipping ? '查看真实商品进展 ' : channel === 'web' ? '前往我的真实关注流 ' : '到工作台查看真实事件 '
    topicButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.deliveryTopic === topic)))
  }
  function renderChannelCopy(config: (typeof channels)[keyof typeof channels]) {
    text('[data-channel-label]', config.label)
    text('[data-channel-title]', config.title)
    text('[data-channel-description]', config.description)
    text('[data-route-middle]', config.middle)
    text('[data-route-end]', config.end)
  }
  function renderChannelPreview(config: (typeof channels)[keyof typeof channels]) {
    text('[data-preview-heading]', config.heading)
    text('[data-preview-subheading]', config.subheading)
    text('[data-bot-name]', config.bot)
    text('[data-message-footer]', config.footer)
    get<HTMLElement>('[data-channel-view]').dataset.channelView = channel
    get<HTMLElement>('[data-user-bubble]').hidden = channel !== 'qq'
    if (previewButton.firstChild) previewButton.firstChild.textContent = config.action
    renderMessage()
  }
  function renderChannel() {
    const config = channels[channel]
    renderChannelCopy(config)
    renderChannelPreview(config)
    text('[data-delivery-feedback]', '选择关键词，再点击预览，体验不同消息内容。')
  }
  bindTabs('[data-channel-tabs]', button => {
    const next = button.dataset.channel
    if (next !== 'qq' && next !== 'email' && next !== 'web') return
    channel = next
    const config = channels[channel]
    get<HTMLElement>('#as-channel-panel').setAttribute('aria-labelledby', button.id)
    swapPanel(get<HTMLElement>('#as-channel-panel'), () => renderChannelCopy(config))
    swapPanel(get<HTMLElement>('.as-chat-body'), () => renderChannelPreview(config))
  })
  topicButtons.forEach(button => button.addEventListener('click', () => {
    topic = button.dataset.deliveryTopic === 'shipping' ? 'shipping' : 'version'
    swapPanel(get<HTMLElement>('.as-chat-body'), renderMessage)
    text('[data-delivery-feedback]', '示例关注词已切换；不会修改任何真实会话配置。')
  }, { signal }))
  previewButton.addEventListener('click', () => {
    const feedback = channel === 'qq' ? '群消息预览已更新 · 未向真实 QQ 群发送。' : channel === 'email' ? '邮件预览已更新 · 未向真实邮箱发送。' : '站内预览已更新 · 未保存关注词。'
    const typing = get<HTMLElement>('[data-typing]')
    const apply = () => {
      renderMessage()
      text('[data-delivery-feedback]', feedback)
    }
    window.clearTimeout(previewTimer)
    if (motion && channel === 'qq') {
      // 打字指示器 → 消息淡入，模拟真实会话节奏
      typing.hidden = false
      previewTimer = window.setTimeout(() => {
        if (disposed) return
        typing.hidden = true
        swapPanel(get<HTMLElement>('.as-chat-body'), apply)
      }, 720)
    } else {
      swapPanel(get<HTMLElement>('.as-chat-body'), apply)
    }
  }, { signal })
  renderChannel()

  // 送达链路动画仅在区块进入视口时运行（配合动效开关与 reduced-motion）
  const flowRoot = get<HTMLElement>('.as-delivery-section')
  const flowObserver = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    entries.forEach(entry => flowRoot.classList.toggle('is-inview', entry.isIntersecting))
  }, { threshold: .2 }) : null
  flowObserver?.observe(flowRoot)

  const reveals = all<HTMLElement>('[data-reveal]')
  const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return
      ;(entry.target as HTMLElement).dataset.reveal = 'shown'
      observer?.unobserve(entry.target)
    })
  }, { threshold: .08, rootMargin: '0px 0px -20px 0px' }) : null
  if (observer) reveals.forEach(element => {
    if (motion && element.getBoundingClientRect().top >= window.innerHeight) element.dataset.reveal = 'pending'
    observer.observe(element)
  })
  const navigation = all<HTMLAnchorElement>('.as-nav-links a')
  function updateProgress() {
    const max = document.documentElement.scrollHeight - window.innerHeight
    root.style.setProperty('--as-scroll', `${max > 0 ? Math.max(0, Math.min(100, window.scrollY / max * 100)) : 0}%`)
    let active: HTMLAnchorElement | undefined
    navigation.forEach(link => {
      const section = root.querySelector<HTMLElement>(link.hash)
      if (section && section.getBoundingClientRect().top <= 140) active = link
    })
    navigation.forEach(link => {
      if (link === active) link.setAttribute('aria-current', 'location')
      else link.removeAttribute('aria-current')
    })
  }
  function queueProgress() {
    window.cancelAnimationFrame(scrollFrame)
    scrollFrame = window.requestAnimationFrame(() => { if (!disposed) updateProgress() })
  }
  window.addEventListener('scroll', queueProgress, { signal, passive: true })
  window.addEventListener('resize', queueProgress, { signal, passive: true })
  updateProgress()

  // 页内锚点：平滑滚动且不把 #hash 留在 URL 里，避免刷新总落在锚点位置
  all<HTMLAnchorElement>('a[href^="#"]').forEach(link => {
    link.addEventListener('click', event => {
      const id = decodeURIComponent((link.getAttribute('href') || '').slice(1))
      const target = id ? document.getElementById(id) : null
      if (!target) return
      event.preventDefault()
      target.scrollIntoView({ behavior: motion ? 'smooth' : 'auto', block: 'start' })
      history.replaceState(null, '', window.location.pathname + window.location.search)
      queueProgress()
    }, { signal })
  })
  if (window.location.hash) {
    // 历史遗留的锚点地址：回到页顶并清掉，刷新不再定位到锚点
    window.scrollTo({ top: 0, behavior: 'auto' })
    history.replaceState(null, '', window.location.pathname + window.location.search)
  }

  return () => {
    disposed = true
    controller.abort()
    observer?.disconnect()
    window.cancelAnimationFrame(pointerFrame)
    window.cancelAnimationFrame(scrollFrame)
    window.cancelAnimationFrame(previewFrame)
    window.clearTimeout(previewTimer)
    resetTilt()
    reveals.forEach(element => { element.dataset.reveal = 'shown' })
    delete root.dataset.previewing
  }
}
