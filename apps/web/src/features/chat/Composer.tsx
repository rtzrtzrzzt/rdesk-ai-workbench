import type { ReactNode } from 'react'
import { useRef } from 'react'
import { ArrowUp, Paperclip, Square } from 'lucide-react'
import { IconButton } from '../../components/ui'

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  disabledReason,
  sending = false,
  stopping = false,
  running = false,
  models = [],
  modelId,
  onModelChange,
  onAttachments,
  attachmentSummary,
  attachmentsDisabled = false,
  modelDisabled = false,
  readOnly = false,
  sendWhileRunning = false,
  sendLabel = '发送消息',
  placeholder = '描述你想完成的工作…',
  options,
  controls,
}: {
  options?: ReactNode
  controls?: ReactNode
  modelDisabled?: boolean
  readOnly?: boolean
  sendWhileRunning?: boolean
  sendLabel?: string
  placeholder?: string
  onAttachments?: () => void
  attachmentSummary?: ReactNode
  attachmentsDisabled?: boolean
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onStop?: () => void
  disabledReason?: string
  sending?: boolean
  stopping?: boolean
  running?: boolean
  models?: { id: string; name: string }[]
  modelId?: string
  onModelChange?: (id: string) => void
}) {
  const composing = useRef(false)
  return (
    <div className="composer-wrap">
      {controls}
      {attachmentSummary}
      <div className="composer">
        <textarea
          aria-label="消息"
          placeholder={placeholder}
          value={value}
          readOnly={readOnly}
          maxLength={20000}
          rows={3}
          onChange={(event) => onChange(event.target.value)}
          onCompositionStart={() => {
            composing.current = true
          }}
          onCompositionEnd={() => {
            composing.current = false
          }}
          onKeyDown={(event) => {
            if (
              event.key === 'Enter' &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing &&
              !composing.current
            ) {
              event.preventDefault()
              if (!disabledReason && !sending && (!running || sendWhileRunning) && value.trim())
                onSend()
            }
          }}
        />
        <div className="composer-toolbar">
          <div className="composer-tools">
            <IconButton
              icon={Paperclip}
              label="选择本次附件"
              onClick={onAttachments}
              disabled={!onAttachments || attachmentsDisabled}
            />
            {options}
            <label className="model-select-label">
              <span className="sr-only">本次任务使用的模型</span>
              <select
                aria-label="本次任务使用的模型"
                value={modelId ?? ''}
                disabled={!models.length || modelDisabled}
                onChange={(event) => onModelChange?.(event.target.value)}
              >
                {!models.length && <option value="">尚无可用模型</option>}
                {models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="composer-actions">
            {running && (
              <button
                className="send-button stop-button"
                type="button"
                disabled={!onStop || stopping}
                aria-label={stopping ? '正在停止' : '停止任务'}
                onClick={onStop}
              >
                <Square size={14} fill="currentColor" />
              </button>
            )}
            {(!running || sendWhileRunning) && (
              <button
                className="send-button"
                type="button"
                disabled={!!disabledReason || sending || !value.trim()}
                aria-label={sending ? '正在发送' : sendLabel}
                title={disabledReason ?? sendLabel}
                onClick={onSend}
              >
                <ArrowUp size={19} />
              </button>
            )}
          </div>
        </div>
      </div>
      <p className="composer-hint" role="status">
        {stopping
          ? '停止请求已提交，正在等待任务确认。'
          : (disabledReason ?? 'Enter 发送 · Shift + Enter 换行')}
      </p>
    </div>
  )
}
