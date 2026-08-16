// 领域模型与演示数据，对应设计文档 §7；全部为 mock，时间基准 2026-08-16

export type Platform = 'bilibili' | 'weibo' | 'x' | 'youtube' | 'web' | 'kuro'
export type Identity = 'official' | 'seeded' | 'user' | 'media' | 'merchant' | 'unknown'
export type Region = 'cn' | 'global'

export interface SourceRef {
  platform: Platform
  account: string
  region: Region
  publishedAt: string
  foundAt: string
  translated?: boolean
  url: string
}

export interface AppEvent {
  id: string
  title: string
  summary: string
  category: string
  identity: Identity
  regions: Region[]
  sources: SourceRef[]
  evidenceCount: number
  firstSeenAt: string
  updatedAt: string
  important?: boolean
  confidence: '高' | '中' | '低'
  confidenceReason: string
  conclusion: string
  statusChange?: string
  regionNote?: string
  conflicts?: string
  corrections?: { at: string; note: string }[]
}

export interface ProductStage {
  label: string
  state: 'done' | 'current' | 'pending'
  tone?: 'amber' | 'sky' | 'emerald'
  date?: string
  evidence?: number
  note?: string
}

export interface Product {
  slug: string
  name: string
  publisher: string
  skus: string[]
  price: string
  presaleAt: string
  balanceFrom: string
  balanceTo: string
  originalShipAt: string
  currentShipNote: string
  currentStage: number
  stages: ProductStage[]
}

export const identityMeta: Record<Identity, { label: string; badge: string; text: string }> = {
  official: {
    label: '官方',
    badge: 'bg-blue-500/10 text-blue-600 dark:bg-blue-400/15 dark:text-blue-300',
    text: 'text-blue-700 dark:text-blue-300',
  },
  seeded: {
    label: '官方送测',
    badge: 'bg-amber-500/10 text-amber-700 dark:bg-amber-400/15 dark:text-amber-300',
    text: 'text-amber-700 dark:text-amber-300',
  },
  user: {
    label: '普通用户',
    badge: 'bg-emerald-500/10 text-emerald-700 dark:bg-emerald-400/15 dark:text-emerald-300',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  media: {
    label: '媒体',
    badge: 'bg-sky-500/10 text-sky-700 dark:bg-sky-400/15 dark:text-sky-300',
    text: 'text-sky-700 dark:text-sky-300',
  },
  merchant: {
    label: '商家',
    badge: 'bg-teal-600/10 text-teal-700 dark:bg-teal-400/15 dark:text-teal-300',
    text: 'text-teal-700 dark:text-teal-300',
  },
  unknown: {
    label: '来源未说明',
    badge: 'bg-zinc-500/10 text-zinc-600 dark:bg-zinc-400/15 dark:text-zinc-300',
    text: 'text-zinc-500 dark:text-zinc-400',
  },
}

export const platformMeta: Record<Platform, { label: string; short: string; badge: string }> = {
  bilibili: { label: 'B站', short: 'B', badge: 'bg-pink-500' },
  weibo: { label: '微博', short: '博', badge: 'bg-red-600' },
  x: { label: 'X', short: 'X', badge: 'bg-zinc-900 dark:bg-zinc-700' },
  youtube: { label: 'YouTube', short: 'YT', badge: 'bg-red-500' },
  web: { label: '官网', short: '网', badge: 'bg-sky-600' },
  kuro: { label: '库街区', short: '库', badge: 'bg-teal-600' },
}

export const regionMeta: Record<Region, string> = {
  cn: '国服',
  global: '国际服',
}

const src = (
  platform: Platform,
  account: string,
  region: Region,
  publishedAt: string,
  foundAt: string,
  translated = false,
): SourceRef => ({ platform, account, region, publishedAt, foundAt, translated, url: '#' })

export const events: AppEvent[] = [
  {
    id: 'ev-delay-2',
    title: '第二批爱弥斯联名键盘延期：仓库搬迁，预计延后两周',
    summary: '官方确认第二批订单整体延期，新发货窗口为 8 月中旬起、分批进行；已下单用户无需重复确认地址。',
    category: '延期',
    identity: 'official',
    regions: ['cn', 'global'],
    sources: [
      src('weibo', '鸣潮官方微博', 'cn', '08-14 12:00', '08-14 12:04'),
      src('bilibili', '鸣潮官方账号', 'cn', '08-14 12:05', '08-14 12:11'),
      src('web', '国服官网公告页', 'cn', '08-14 12:30', '08-14 12:40'),
      src('kuro', '库街区官方', 'cn', '08-14 13:10', '08-14 13:15'),
      src('x', 'Wuthering Waves', 'global', '08-14 11:00', '08-14 11:06', true),
    ],
    evidenceCount: 12,
    firstSeenAt: '08-14 11:06',
    updatedAt: '08-14 13:15',
    important: true,
    confidence: '高',
    confidenceReason: '官方五个平台（微博 / B站 / 官网 / 库街区 / 国际服 X）在同一小时内一致发布公告，内容互证。',
    conclusion: '第二批订单因仓库搬迁整体延期约两周，官方口径为 8 月中旬起分批发货。',
    statusChange: '原定 07-30 → 08 中旬起',
  },
  {
    id: 'ev-balance-open',
    title: '第二批尾款通道开启：07-10 10:00 至 07-13 24:00',
    summary: '官方开启第二批尾款支付，三天窗口；逾期视为放弃名额。已有用户反映不知道尾款开启，订阅者已收到即时邮件。',
    category: '尾款',
    identity: 'official',
    regions: ['cn'],
    sources: [
      src('weibo', '鸣潮官方微博', 'cn', '07-10 10:00', '07-10 10:03'),
      src('bilibili', '鸣潮官方账号', 'cn', '07-10 10:05', '07-10 10:09'),
      src('web', '国服官网公告页', 'cn', '07-10 10:30', '07-10 10:31'),
    ],
    evidenceCount: 5,
    firstSeenAt: '07-10 10:03',
    updatedAt: '07-10 10:31',
    confidence: '高',
    confidenceReason: '官方三平台一致公告。',
    conclusion: '尾款窗口 07-10 10:00 至 07-13 24:00。',
  },
  {
    id: 'ev-seeded-unbox',
    title: '6 位博主发布开箱视频，其中 4 位声明官方送测',
    summary: '首批开箱集中于 08-05 至 08-08，画面与量产批次一致性待验证；另有 2 位未声明来源，标记「来源未说明」。',
    category: '开箱',
    identity: 'seeded',
    regions: ['cn'],
    sources: [
      src('bilibili', '博主A（声明送测）', 'cn', '08-05 18:00', '08-05 18:30'),
      src('bilibili', '博主B（声明送测）', 'cn', '08-06 12:00', '08-06 12:20'),
      src('bilibili', '博主C（未声明）', 'cn', '08-07 20:00', '08-07 20:40'),
    ],
    evidenceCount: 6,
    firstSeenAt: '08-05 18:30',
    updatedAt: '08-08 21:10',
    confidence: '中',
    confidenceReason: '送测声明来自作者自述；未声明来源的 2 位不做送测认定。',
    conclusion: '实物样品已存在，仅代表送测批次，不代表消费者订单开始发货。',
    conflicts: '2 位博主未声明内容来源，按规则标记「来源未说明」，不计入送测组，也不做软广认定。',
  },
  {
    id: 'ev-first-arrival',
    title: '首位普通用户到货：首发当天订单晒单（已人工复核）',
    summary: '复核内容：账号 3 年历史、无商业合作声明、订单时间 06-20、开箱语境完整。',
    category: '到货',
    identity: 'user',
    regions: ['cn'],
    sources: [src('bilibili', '用户晒单视频', 'cn', '08-12 19:30', '08-12 19:55')],
    evidenceCount: 1,
    firstSeenAt: '08-12 19:55',
    updatedAt: '08-13 09:00',
    important: true,
    confidence: '中',
    confidenceReason: '单一账号单一证据，经人工复核账号身份、商品对应关系与语境后采信。',
    conclusion: '首位可确认的普通消费者到货，订单为首发当天（06-20）下单。',
    statusChange: '送测样品 → 首位普通用户到货',
  },
  {
    id: 'ev-multi-arrival',
    title: '多名独立用户确认到货：7 个独立账号，均为首发订单',
    summary: '7 个账号在 08-13 至 08-15 间发布开箱/晒单，转载与重复账号未计数。',
    category: '到货',
    identity: 'user',
    regions: ['cn'],
    sources: [
      src('bilibili', '独立账号 ×4', 'cn', '08-13 12:00', '08-13 12:30'),
      src('weibo', '独立账号 ×3', 'cn', '08-14 09:00', '08-14 09:20'),
    ],
    evidenceCount: 7,
    firstSeenAt: '08-13 12:30',
    updatedAt: '08-15 21:00',
    important: true,
    confidence: '高',
    confidenceReason: '7 个独立账号交叉验证，发布时间与物流节奏一致，无相互转载痕迹。',
    conclusion: '首发批次已进入实际履约，到货集中在首发当天订单。',
    statusChange: '首位普通用户到货 → 多名独立用户到货',
  },
  {
    id: 'ev-quality-gap',
    title: '质量反馈：键帽缝隙与轴体一致性，累计 9 条，待官方回应',
    summary: '反馈集中在空格键缝隙与两颗轴体手感偏差；样本尚小，暂不构成批次结论。',
    category: '质量',
    identity: 'user',
    regions: ['cn'],
    sources: [
      src('bilibili', '到货用户评论区', 'cn', '08-14 22:00', '08-14 22:30'),
      src('weibo', '用户反馈帖', 'cn', '08-15 10:00', '08-15 10:15'),
    ],
    evidenceCount: 9,
    firstSeenAt: '08-14 22:30',
    updatedAt: '08-16 06:40',
    confidence: '中',
    confidenceReason: '9 条独立反馈方向一致，但相对到货总量样本偏小。',
    conclusion: '存在个别键帽缝隙与轴体一致性反馈，暂无法判定为批次性问题。',
    conflicts: '另有 4 条到货反馈表示做工良好、无异常。',
  },
  {
    id: 'ev-global-promo',
    title: '国际服发布 2.1 联动宣传：国服暂未发现对应公告',
    summary: '国际服 X 与 YouTube 于今日凌晨发布联动 PV，截至抓取时国服各官方渠道无对应内容。',
    category: '公告',
    identity: 'official',
    regions: ['global'],
    sources: [
      src('x', 'Wuthering Waves', 'global', '08-16 03:20', '08-16 03:26', true),
      src('youtube', 'Wuthering Waves', 'global', '08-16 03:30', '08-16 03:41', true),
    ],
    evidenceCount: 2,
    firstSeenAt: '08-16 03:26',
    updatedAt: '08-16 07:50',
    confidence: '中',
    confidenceReason: '国际服发布确认；「国服未发布」仅在已接入的国服来源范围内成立。',
    conclusion: '国际服先行宣传，国服存在时间差；将持续监控国服渠道。',
    regionNote: '国服暂未发现对应公告（截至 08-16 07:50）',
  },
  {
    id: 'ev-media-review',
    title: '媒体评测：手感与量产一致性整体正面，提及包装成本取舍',
    summary: '两家媒体完成长评，量化测试了延迟与键噪表现，均基于送测样机。',
    category: '评测',
    identity: 'media',
    regions: ['cn'],
    sources: [src('web', '媒体评测文章', 'cn', '08-10 10:00', '08-10 10:20')],
    evidenceCount: 2,
    firstSeenAt: '08-10 10:20',
    updatedAt: '08-10 10:20',
    confidence: '中',
    confidenceReason: '送测样机评测，个体差异未知。',
    conclusion: '媒体结论整体正面，量产一致性待用户样本验证。',
  },
  {
    id: 'ev-correction-recall',
    title: '更正：撤回「第二批已普遍发货」的早期判断',
    summary: '08-15 早间两条「到货」证据经复核为送测合作内容，已移出普通用户证据组，事件状态相应回退。',
    category: '更正',
    identity: 'user',
    regions: ['cn'],
    sources: [src('bilibili', '复核涉及视频 ×2', 'cn', '08-15 09:00', '08-15 21:00')],
    evidenceCount: 2,
    firstSeenAt: '08-15 21:00',
    updatedAt: '08-15 21:30',
    important: true,
    confidence: '高',
    confidenceReason: '人工复核确认作者与官方存在合作关系（作者补充声明）。',
    conclusion: '「普遍发货」结论不成立，当前状态回退至「多名独立用户到货」。',
    corrections: [
      { at: '08-15 09:00', note: '系统根据新增开箱数量，将候选结论升级为「普遍到货」（低置信度候选）' },
      { at: '08-15 21:00', note: '人工复核认定 2 条关键证据为送测内容，结论回退，订阅者收到更正通知' },
    ],
  },
  {
    id: 'ev-unbox-more',
    title: '普通用户开箱视频持续增加：今日 +3',
    summary: '新增 3 条独立开箱，含典藏版首个晒单。',
    category: '开箱',
    identity: 'user',
    regions: ['cn'],
    sources: [src('bilibili', '独立账号 ×3', 'cn', '08-16 08:00', '08-16 08:10')],
    evidenceCount: 3,
    firstSeenAt: '08-16 08:10',
    updatedAt: '08-16 09:30',
    confidence: '中',
    confidenceReason: '独立账号，商品对应关系明确。',
    conclusion: '开箱证据持续增加，未改变当前履约阶段。',
  },
  {
    id: 'ev-aftersale-reply',
    title: '官方客服回应延期补偿咨询：以正式公告为准',
    summary: '客服渠道答复「补偿方案以公告为准」，暂无具体细则。',
    category: '售后',
    identity: 'official',
    regions: ['cn'],
    sources: [src('kuro', '库街区客服回复截图', 'cn', '08-15 15:00', '08-15 15:30')],
    evidenceCount: 2,
    firstSeenAt: '08-15 15:30',
    updatedAt: '08-15 15:30',
    confidence: '低',
    confidenceReason: '客服口头答复，非正式公告，存在渠道间口径差异可能。',
    conclusion: '暂无正式补偿方案，等待官方公告。',
  },
  {
    id: 'ev-topic-febie',
    title: '菲比主题二创热度：B站单视频播放首次突破 50 万',
    summary: '监控规则（B站 · 播放量 ≥ 500,000）首次触发，已通知订阅者。',
    category: '热度',
    identity: 'user',
    regions: ['cn'],
    sources: [src('bilibili', '二创视频', 'cn', '08-15 20:00', '08-16 07:00')],
    evidenceCount: 1,
    firstSeenAt: '08-16 07:00',
    updatedAt: '08-16 07:00',
    confidence: '高',
    confidenceReason: '平台指标时间快照，首次越限时刻由系统记录。',
    conclusion: '热度阈值首次触发；热度仅代表传播程度，不代表作品质量。',
  },
]

export interface RawItem {
  id: string
  platform: Platform
  account: string
  region: Region
  title: string
  publishedAt: string
  translated?: boolean
  mergedInto?: string
}

export const officialFeed: RawItem[] = [
  { id: 'r1', platform: 'weibo', account: '鸣潮官方微博', region: 'cn', title: '2.1 版本更新预告：8 月 21 日上线', publishedAt: '08-16 09:30' },
  { id: 'r2', platform: 'bilibili', account: '鸣潮官方账号', region: 'cn', title: '2.1 版本 PV 发布', publishedAt: '08-16 08:00' },
  { id: 'r3', platform: 'x', account: 'Wuthering Waves', region: 'global', title: 'Collab PV teaser（联动宣传预告）', publishedAt: '08-16 03:20', translated: true, mergedInto: 'ev-global-promo' },
  { id: 'r4', platform: 'youtube', account: 'Wuthering Waves', region: 'global', title: 'Version 2.1 Special Program', publishedAt: '08-16 03:30', translated: true, mergedInto: 'ev-global-promo' },
  { id: 'r5', platform: 'kuro', account: '库街区官方', region: 'cn', title: '库街区签到福利更新', publishedAt: '08-15 18:00' },
  { id: 'r6', platform: 'web', account: '国服官网', region: 'cn', title: '第二批延期公告（更新于 08-14）', publishedAt: '08-14 12:30', mergedInto: 'ev-delay-2' },
  { id: 'r7', platform: 'weibo', account: '鸣潮官方微博', region: 'cn', title: '第二批延期公告', publishedAt: '08-14 12:00', mergedInto: 'ev-delay-2' },
  { id: 'r8', platform: 'bilibili', account: '鸣潮官方账号', region: 'cn', title: '第二批延期公告', publishedAt: '08-14 12:05', mergedInto: 'ev-delay-2' },
]

export const products: Product[] = [
  {
    slug: 'aymiss-keyboard',
    name: '爱弥斯联名机械键盘',
    publisher: '库洛游戏 × 联名厂商',
    skus: ['标准版', '典藏版'],
    price: '¥749 起',
    presaleAt: '2026-06-20',
    balanceFrom: '07-10 10:00',
    balanceTo: '07-13 24:00',
    originalShipAt: '2026-07-30',
    currentShipNote: '8 月中旬起分批',
    currentStage: 3,
    stages: [
      { label: '尚未发货', state: 'done', note: '预售 06-20 开启，尾款 07-13 截止' },
      { label: '出现送测样品', state: 'done', tone: 'amber', date: '08-05', evidence: 6, note: '仅证明实物样品存在，不代表消费者订单发货' },
      { label: '首位普通用户到货', state: 'done', tone: 'sky', date: '08-12', evidence: 1, note: '人工复核：账号身份、商品对应关系与语境' },
      { label: '多名独立用户到货', state: 'current', tone: 'emerald', date: '08-15', evidence: 7, note: '7 个独立账号，转载未重复计数' },
      { label: '相近购买时间样本到货', state: 'pending', tone: 'emerald', note: '等待 7 月中旬后下单订单的开箱证据' },
      { label: '普遍到货', state: 'pending', tone: 'emerald', note: '多购买时间段样本同时到货' },
    ],
  },
]

export const product = products[0]

export const sourcesHealth: {
  name: string
  platform: Platform
  region: Region
  lastOk: string
  status: 'ok' | 'fail'
  failStreak?: number
  note?: string
}[] = [
  { name: '《鸣潮》官方微博', platform: 'weibo', region: 'cn', lastOk: '10 分钟前', status: 'ok' },
  { name: 'B站《鸣潮》官方账号', platform: 'bilibili', region: 'cn', lastOk: '25 分钟前', status: 'ok' },
  { name: '《鸣潮》国服官网', platform: 'web', region: 'cn', lastOk: '1 小时前', status: 'ok' },
  { name: '库街区公开官方内容', platform: 'kuro', region: 'cn', lastOk: '3 小时前', status: 'ok' },
  { name: 'Wuthering Waves 官方 X', platform: 'x', region: 'global', lastOk: '40 分钟前', status: 'ok' },
  { name: 'Wuthering Waves YouTube', platform: 'youtube', region: 'global', lastOk: '2 小时前', status: 'ok' },
  { name: 'B站用户内容搜索', platform: 'bilibili', region: 'cn', lastOk: '30 分钟前', status: 'ok' },
  {
    name: '微博用户内容搜索',
    platform: 'weibo',
    region: 'cn',
    lastOk: '3 小时前',
    status: 'fail',
    failStreak: 6,
    note: '连续失败 6 次，已标记不可用；该来源范围内的「未发现」不代表无消息',
  },
]

export const watchSlots = [
  {
    id: 'w1',
    type: '商品监控',
    target: '爱弥斯联名键盘',
    purchaseTime: '首发当天',
    channel: '即时邮件 + 每日早报',
    status: '运行中',
  },
]

export const deliveries = [
  { at: '08-16 07:02', type: '即时', event: '质量反馈累计 9 条，方向一致', status: '已送达', rule: '商品监控 · 爱弥斯键盘', eventId: 'ev-quality-gap', unread: true },
  { at: '08-16 08:00', type: '早报', event: '2026-08-16 每日早报', status: '已送达', rule: '每日早报' },
  { at: '08-15 21:02', type: '即时', event: '多名独立用户确认到货', status: '已送达', rule: '商品监控 · 爱弥斯键盘', eventId: 'ev-multi-arrival', unread: true },
  { at: '08-14 12:08', type: '即时', event: '第二批延期公告', status: '已送达', rule: '商品监控 · 爱弥斯键盘', eventId: 'ev-delay-2' },
]

export const topics = [
  { name: '菲比二创', metric: 'B站 52.3万播放' },
  { name: '爱弥斯', metric: '讨论 87 条/日' },
  { name: '联名外设', metric: '证据 34 条' },
]

export const pastReports = [
  {
    date: '2026-08-15',
    items: [
      { text: '官方延期公告（五平台同步）', eventId: 'ev-delay-2' },
      { text: '质量反馈出现：键帽缝隙（首批 3 条）', eventId: 'ev-quality-gap' },
      { text: '新增送测开箱 2 条，均声明送测' },
    ],
  },
  {
    date: '2026-08-14',
    items: [
      { text: '首位普通用户到货（人工复核通过）', eventId: 'ev-first-arrival' },
      { text: '媒体评测发布：整体正面', eventId: 'ev-media-review' },
      { text: '国际服发布联动 PV，国服未发现对应公告', eventId: 'ev-global-promo' },
    ],
  },
]

export const morningReport = {
  date: '2026-08-16',
  sentAt: '08:00',
  cutoff: '07:50',
  sections: [
    {
      title: '重要事件',
      items: [
        { text: '多名独立用户确认到货：7 个独立账号，均为首发订单', eventId: 'ev-multi-arrival' },
        { text: '更正：撤回「第二批已普遍发货」的早期判断', eventId: 'ev-correction-recall' },
      ],
    },
    {
      title: '官方动态',
      items: [
        { text: '国际服发布 2.1 联动宣传，国服暂未发现对应公告', eventId: 'ev-global-promo' },
        { text: '微博 / B站：2.1 版本更新预告（8 月 21 日上线）' },
      ],
    },
    {
      title: '用户内容与证据',
      items: [
        { text: '开箱视频今日 +3，含典藏版首个晒单', eventId: 'ev-unbox-more' },
        { text: '质量反馈累计 9 条，方向一致，样本尚小', eventId: 'ev-quality-gap' },
      ],
    },
    {
      title: '无新增说明',
      items: [{ text: '日服官方账号尚未接入，本期不覆盖日服内容；微博用户内容搜索来源不可用，相关范围内未发现新内容。' }],
    },
  ],
}

export function getEvent(id: string) {
  return events.find((e) => e.id === id)
}

export const latestEvents = () => [...events].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
