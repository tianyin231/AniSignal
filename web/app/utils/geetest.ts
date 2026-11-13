/** 使用官方极验组件完成人机验证，结果交回B站校验。 */
export interface CaptchaTicket {
  token: string
  geetest: { gt: string; challenge: string }
}

interface Verification {
  geetest_challenge: string
  geetest_validate: string
  geetest_seccode: string
}

interface Captcha {
  onReady(callback: () => void): Captcha
  onSuccess(callback: () => void): Captcha
  onClose(callback: () => void): Captcha
  onError(callback: () => void): Captcha
  verify(): void
  getValidate(): Verification | false
  destroy(): void
}

type InitGeetest = (options: Record<string, unknown>, callback: (captcha: Captcha) => void) => void
declare global {
  interface Window { initGeetest?: InitGeetest }
}

let loading: Promise<InitGeetest> | undefined

function loadGeetest(): Promise<InitGeetest> {
  if (window.initGeetest) return Promise.resolve(window.initGeetest)
  if (loading) return loading
  loading = new Promise<InitGeetest>((resolve, reject) => {
    const script = document.createElement('script')
    const fail = () => {
      clearTimeout(timer)
      script.remove()
      reject(new Error('人机验证组件加载失败，请检查网络或改用扫码登录'))
    }
    const timer = setTimeout(fail, 15000)
    script.src = 'https://static.geetest.com/static/js/gt.0.5.0.js'
    script.async = true
    script.onerror = fail
    script.onload = () => {
      if (!window.initGeetest) return fail()
      clearTimeout(timer)
      resolve(window.initGeetest)
    }
    document.head.appendChild(script)
  }).catch((error) => { loading = undefined; throw error })
  return loading
}

export async function verifyGeetest(ticket: CaptchaTicket, signal: AbortSignal) {
  const init = await loadGeetest()
  if (signal.aborted) throw new Error('已取消登录')
  return new Promise<{ token: string; challenge: string; validate: string; seccode: string }>((resolve, reject) => {
    let widget: Captcha | undefined
    let settled = false
    const finish = (error?: Error, value?: Verification) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal.removeEventListener('abort', cancel)
      widget?.destroy()
      if (error) reject(error)
      else resolve({ token: ticket.token, challenge: value!.geetest_challenge, validate: value!.geetest_validate, seccode: value!.geetest_seccode })
    }
    const cancel = () => finish(new Error('已取消人机验证'))
    const timer = setTimeout(() => finish(new Error('人机验证已超时，请重试')), 120000)
    signal.addEventListener('abort', cancel, { once: true })
    init({ ...ticket.geetest, offline: false, new_captcha: true, product: 'bind', lang: 'zh-cn', https: true }, (captcha) => {
      if (settled) { captcha.destroy(); return }
      widget = captcha
      captcha.onReady(() => captcha.verify())
        .onSuccess(() => {
          const value = captcha.getValidate()
          if (value) finish(undefined, value)
          else finish(new Error('人机验证未通过，请重试'))
        })
        .onClose(cancel)
        .onError(() => finish(new Error('人机验证暂时不可用，请重试或改用扫码登录')))
    })
  })
}
