import { useId, useState } from 'react'
import type { AutomationSession, BrowserAction, DesktopAction } from '../../api/automation'
import { Button } from '../../components/ui'
import { errorText, number, record, text } from './data'

type Command =
  { kind: 'browser'; action: BrowserAction } | { kind: 'desktop'; action: DesktopAction }
const browserKeys: NonNullable<BrowserAction['key']>[] = [
  'Enter',
  'Tab',
  'Escape',
  'ArrowUp',
  'ArrowDown',
  'Home',
  'End',
  'Space',
]
const desktopKeys: NonNullable<DesktopAction['key']>[] = [
  'Enter',
  'Tab',
  'Escape',
  'Backspace',
  'Delete',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'Home',
  'End',
  'PageUp',
  'PageDown',
  'Space',
]
export function ActionForm({
  session,
  observation,
  disabled,
  onSubmit,
}: {
  session: AutomationSession
  observation: Record<string, unknown> | null
  disabled: boolean
  onSubmit: (command: Command) => Promise<void>
}) {
  const id = useId()
  const browser = session.kind === 'browser'
  const [kind, setKind] = useState<BrowserAction['kind']>('click')
  const [target, setTarget] = useState('')
  const [url, setUrl] = useState('')
  const [value, setValue] = useState('')
  const [key, setKey] = useState<NonNullable<DesktopAction['key']>>('Enter')
  const [x, setX] = useState(''),
    [y, setY] = useState(''),
    [delta, setDelta] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState('')
  const elements = Array.isArray(observation?.elements)
    ? observation.elements
        .map(record)
        .flatMap((item) => (item && text(item.ref) ? [String(item.ref)] : []))
    : []
  const observationId = text(observation?.observation_id)
  const browserTarget = kind === 'navigate' ? 'page' : target
  async function send() {
    setError('')
    try {
      if (disabled || !confirmed || !observationId)
        throw new Error('需要一次真实观察，并明确确认本次动作。')
      const base = {
        request_id: crypto.randomUUID(),
        session_id: session.id,
        last_observation: observationId,
      }
      let command: Command
      if (browser) {
        if (!browserTarget || (browserTarget !== 'page' && !elements.includes(browserTarget)))
          throw new Error('请选择当前观察中的元素引用。')
        const action: BrowserAction = { ...base, kind, target_ref: browserTarget }
        if (kind === 'navigate') {
          if (!url.trim()) throw new Error('请输入目标网址。')
          action.url = url.trim()
        }
        if (kind === 'type') {
          if (!value || value.length > 16000) throw new Error('网页输入文字须为 1–16000 字符。')
          action.text = value
        }
        if (kind === 'key') {
          if (!browserKeys.includes(key as NonNullable<BrowserAction['key']>))
            throw new Error('按键不适用于浏览器。')
          action.key = key as NonNullable<BrowserAction['key']>
        }
        if (kind === 'scroll') {
          const n = Number(delta)
          if (!delta.trim() || !Number.isInteger(n) || n < -3000 || n > 3000)
            throw new Error('网页滚动量须为 -3000 至 3000 的整数。')
          action.delta_y = n
        }
        command = { kind: 'browser', action }
      } else {
        const generation = number(observation?.generation),
          geometryId = text(observation?.geometry_id),
          windowId = text(observation?.window_id)
        if (
          generation === null ||
          !Number.isInteger(generation) ||
          generation < 1 ||
          !geometryId ||
          geometryId.length !== 64 ||
          !windowId ||
          kind === 'navigate'
        )
          throw new Error('桌面观察缺少有效 generation、几何或目标窗口信息，请重新观察。')
        const action: DesktopAction = {
          ...base,
          kind,
          generation,
          geometry_id: geometryId,
          target_window_id: windowId,
        }
        if (kind === 'click' || kind === 'scroll') {
          const nx = Number(x),
            ny = Number(y)
          if (
            !x.trim() ||
            !y.trim() ||
            !Number.isInteger(nx) ||
            !Number.isInteger(ny) ||
            nx < 0 ||
            ny < 0 ||
            nx > 32768 ||
            ny > 32768
          )
            throw new Error('请输入原始截图中的非负整数坐标（最大 32768）。')
          const width = number(observation?.image_width),
            height = number(observation?.image_height)
          if (width === null || height === null || nx >= width || ny >= height)
            throw new Error('坐标超出原始截图范围，或缺少真实尺寸。')
          action.x = nx
          action.y = ny
        }
        if (kind === 'type') {
          if (!value || value.length > 256) throw new Error('桌面输入文字须为 1–256 字符。')
          action.text = value
        }
        if (kind === 'key') action.key = key
        if (kind === 'scroll') {
          const n = Number(delta)
          if (!delta.trim() || !Number.isInteger(n) || n < -10 || n > 10)
            throw new Error('桌面滚动步数须为 -10 至 10 的整数。')
          action.scroll_steps = n
        }
        command = { kind: 'desktop', action }
      }
      setConfirmed(false)
      await onSubmit(command)
    } catch (error) {
      setError(errorText(error))
    }
  }
  return (
    <details className="automation-action-form">
      <summary>提交一次{browser ? '网页' : '桌面'}动作</summary>
      <p className="page-note">
        绑定观察 {observationId ?? '尚无'}
        。执行不代表网页或应用业务结果已成功；桌面动作后必须重新观察。
      </p>
      <fieldset
        disabled={disabled || !observationId}
        className="automation-fields"
        onChange={() => setConfirmed(false)}
      >
        <label htmlFor={`${id}-kind`}>
          动作
          <select
            aria-label="动作"
            id={`${id}-kind`}
            value={kind}
            onChange={(e) => setKind(e.target.value as BrowserAction['kind'])}
          >
            {(browser
              ? ['navigate', 'click', 'type', 'scroll', 'key']
              : ['click', 'type', 'scroll', 'key']
            ).map((item) => (
              <option value={item} key={item}>
                {
                  (
                    {
                      navigate: '导航',
                      click: '点击',
                      type: '输入文字',
                      scroll: '滚动',
                      key: '按键',
                    } as Record<string, string>
                  )[item]
                }
              </option>
            ))}
          </select>
        </label>
        {browser && kind !== 'navigate' && (
          <label htmlFor={`${id}-target`}>
            当前 DOM 目标
            <select
              aria-label="当前 DOM 目标"
              id={`${id}-target`}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            >
              <option value="">请选择</option>
              <option value="page">page · 页面</option>
              {elements
                .filter((item) => item !== 'page')
                .map((item) => (
                  <option value={item} key={item}>
                    {item}
                  </option>
                ))}
            </select>
          </label>
        )}
        {kind === 'navigate' && browser && (
          <label htmlFor={`${id}-url`}>
            目标网址
            <input id={`${id}-url`} value={url} onChange={(e) => setUrl(e.target.value)} />
          </label>
        )}
        {!browser && (kind === 'click' || kind === 'scroll') && (
          <div className="automation-grid">
            <label htmlFor={`${id}-x`}>
              原图 X
              <input
                type="number"
                min={0}
                max={32768}
                id={`${id}-x`}
                value={x}
                onChange={(e) => setX(e.target.value)}
              />
            </label>
            <label htmlFor={`${id}-y`}>
              原图 Y
              <input
                type="number"
                min={0}
                max={32768}
                id={`${id}-y`}
                value={y}
                onChange={(e) => setY(e.target.value)}
              />
            </label>
          </div>
        )}
        {kind === 'type' && (
          <label htmlFor={`${id}-text`}>
            本次输入文字
            <textarea
              id={`${id}-text`}
              value={value}
              maxLength={browser ? 16000 : 256}
              onChange={(e) => setValue(e.target.value)}
            />
          </label>
        )}
        {kind === 'type' && !browser && (
          <p className="muted">
            Linux 桌面目前只支持英文键盘字符。部分输入法可能拦截按键；执行后请重新观察目标窗口，
            确认文字确实写入再继续。执行回执不代表目标应用已接收文字，请勿直接重复输入。
          </p>
        )}
        {kind === 'scroll' && (
          <label htmlFor={`${id}-delta`}>
            {browser ? '滚动像素（-3000 至 3000）' : '滚动步数（-10 至 10）'}
            <input
              id={`${id}-delta`}
              type="number"
              value={delta}
              onChange={(e) => setDelta(e.target.value)}
            />
          </label>
        )}
        {kind === 'key' && (
          <label htmlFor={`${id}-key`}>
            按键
            <select
              aria-label="按键"
              id={`${id}-key`}
              value={key}
              onChange={(e) => setKey(e.target.value as NonNullable<DesktopAction['key']>)}
            >
              {(browser ? browserKeys : desktopKeys).map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
        )}
      </fieldset>
      <label className="automation-check">
        <input
          type="checkbox"
          disabled={disabled || !observationId}
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        我确认以上目标、观察与本次动作；提交后不会自动重发
      </label>
      <Button
        variant="primary"
        disabled={disabled || !observationId || !confirmed}
        onClick={() => void send()}
      >
        确认并提交一次动作
      </Button>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </details>
  )
}
