import { useState } from 'react'
import type { CapabilitiesUpdate, ModelConnection } from '../../api/types'
import { Button, Status } from '../../components/ui'
import './capabilities.css'
const names: Record<string, string> = {
  text: '文本',
  image: '图像',
  audio: '音频',
  video: '视频',
  native_document: '原生文档',
  image_output: '图像输出',
  audio_output: '音频输出',
  structured_output: '结构化输出',
  tool_calling: '工具调用',
  stream: '流式输出',
  prefix_cache: '前缀缓存',
}
const directions: Record<string, string> = {
  input: '输入',
  output: '输出',
  input_output: '输入 / 输出',
  behavior: '运行行为',
}
function directionLabel(value: string | undefined) {
  return value ? (directions[value] ?? value) : '方向未提供'
}
function AdapterRoutes({ model }: { model: ModelConnection }) {
  return (
    <details className="model-adapter-routes">
      <summary>协议适配路线与限制</summary>
      <p className="page-note">
        这是工作台已实现的协议路线。模型支持声明、启用状态和验证证据独立，不会因路线可用而自动启用模型能力。
      </p>
      <div className="adapter-route-list">
        {Object.entries(model.adapter_routes).map(([name, value]) => {
          const direction = typeof value.direction === 'string' ? value.direction : undefined
          const status = typeof value.status === 'string' ? value.status : undefined
          const formats = Array.isArray(value.formats)
            ? value.formats.filter((item): item is string => typeof item === 'string')
            : null
          const verification =
            typeof value.verification === 'string' ? value.verification : undefined
          return (
            <div key={name}>
              <div className="run-line">
                <strong>
                  {names[name] ?? name} · {directionLabel(direction)}
                </strong>
                <Status>
                  {status === 'available'
                    ? '协议路线可用'
                    : status === 'unavailable'
                      ? '协议路线未实现'
                      : (status ?? '路线状态未提供')}
                </Status>
              </div>
              <p>
                {typeof value.reason === 'string' ? value.reason : '服务端未给出路线限制说明。'}
              </p>
              <p className="page-note">
                格式：
                {formats
                  ? formats.length
                    ? formats.join('、')
                    : '未声明具体格式'
                  : '服务端未提供'}
              </p>
              <p className="page-note">
                路线验真：
                {verification === 'not_evaluated'
                  ? '尚未评估连接模型'
                  : (verification ?? '服务端未提供')}
              </p>
            </div>
          )
        })}
      </div>
    </details>
  )
}
const evidence: Record<string, string> = {
  declared: '服务声明',
  manually_asserted: '用户声明',
  verified: '已验证',
  verification_failed: '验证失败',
  stale: '证据已过期',
}
export function Capabilities({
  model,
  onSave,
  onImport,
  onReload,
}: {
  model: ModelConnection
  onSave: (input: CapabilitiesUpdate) => Promise<void>
  onImport: () => Promise<void>
  onReload: () => Promise<void>
}) {
  const [selected, setSelected] = useState('text')
  const [support, setSupport] = useState<'supported' | 'unsupported' | 'unknown'>(
    model.capabilities.text?.support ?? 'unknown',
  )
  const [enabled, setEnabled] = useState(model.capabilities.text?.enabled ?? true)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const execute = async (action: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="model-capabilities">
      <summary>能力与证据</summary>
      <p className="page-note">
        连接在线仅说明服务可访问。能力方向、模型支持声明、启用状态与验证证据分别记录。
      </p>
      <div className="capability-list">
        {Object.entries(model.capabilities).map(([name, value]) => (
          <div key={name}>
            <strong>{names[name] ?? name}</strong>
            <small>方向：{directionLabel(value.direction)}</small>
            <span>
              {value.support === 'supported'
                ? '支持'
                : value.support === 'unsupported'
                  ? '不支持'
                  : '未知'}{' '}
              · {value.enabled ? '已启用' : '已停用'}
            </span>
            <Status tone={value.evidence_status === 'verified' ? 'good' : 'neutral'}>
              {evidence[value.evidence_status ?? ''] ?? '无验证证据'}
            </Status>
            {value.evidence_source && <small>{value.evidence_source}</small>}
            {value.checked_at && <small>{new Date(value.checked_at).toLocaleString()}</small>}
            {value.deployment_revision && <small>部署版本：{value.deployment_revision}</small>}
          </div>
        ))}
      </div>
      <AdapterRoutes model={model} />
      <Button disabled={busy} onClick={() => void execute(onImport)}>
        导入已验证的本机部署证据
      </Button>
      <p className="page-note">仅导入与当前端点和模型精确匹配的受管部署证据。</p>
      <details>
        <summary>手动声明能力</summary>
        <form
          className="stacked-form"
          onSubmit={(e) => {
            e.preventDefault()
            void execute(() =>
              onSave({
                expected_version: model.version,
                declarations: { [selected]: { support, enabled, note } },
              }),
            )
          }}
        >
          <label>
            能力
            <select
              value={selected}
              onChange={(e) => {
                const value = model.capabilities[e.target.value]
                setSelected(e.target.value)
                setSupport(value?.support ?? 'unknown')
                setEnabled(value?.enabled ?? true)
                setNote('')
              }}
            >
              {Object.entries(names).map(([id, label]) => (
                <option key={id} value={id}>
                  {label} · {directionLabel(model.capabilities[id]?.direction)}
                </option>
              ))}
            </select>
          </label>
          <label>
            支持声明
            <select
              value={support}
              onChange={(e) =>
                setSupport(
                  e.target.value === 'supported'
                    ? 'supported'
                    : e.target.value === 'unsupported'
                      ? 'unsupported'
                      : 'unknown',
                )
              }
            >
              <option value="unknown">未知</option>
              <option value="supported">支持</option>
              <option value="unsupported">不支持</option>
            </select>
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>允许使用此能力</span>
          </label>
          <label>
            声明依据
            <textarea value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
          </label>
          <p className="page-note">手动声明不会被标为已验证，也不会自动发起模型请求。</p>
          <Button type="submit" disabled={busy}>
            保存本项声明
          </Button>
        </form>
      </details>
      {error && (
        <div role="alert">
          <p className="form-error">{error}</p>
          <Button disabled={busy} onClick={() => void execute(onReload)}>
            重新读取当前版本
          </Button>
        </div>
      )}
    </details>
  )
}
