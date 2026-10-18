import type { ReactNode } from 'react'
import { useRef, useState } from 'react'
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
  onFiles,
  attachmentSummary,
  attachmentsDisabled = false,
  modelDisabled = false,
  readOnly = false,
  sendLabel = '发送消息',
  placeholder = '描述你想完成的工作…',
  options,
  reasoningControl,
  controls,
}: {
  options?: ReactNode
  reasoningControl?: ReactNode
  controls?: ReactNode
  modelDisabled?: boolean
  readOnly?: boolean
  sendLabel?: string
  placeholder?: string
  onAttachments?: () => void
  onFiles?: (files: File[]) => void
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
  const fileInput = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  return (
    <div
      className={`composer-wrap${dragging ? ' composer-dragging' : ''}`}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes('Files')) {
          event.preventDefault()
          if (onFiles && !attachmentsDisabled) setDragging(true)
        }
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
      }}
      onDrop={(event) => {
        setDragging(false)
        if (event.dataTransfer.files.length) {
          event.preventDefault()
          if (!attachmentsDisabled) onFiles?.(Array.from(event.dataTransfer.files))
        }
      }}
    >
      {controls}
      {attachmentSummary}
      <div className="composer">
        <textarea
          aria-label="消息"
          placeholder={placeholder}
          value={value}
          readOnly={readOnly}
          maxLength={1048576}
          rows={3}
          onChange={(event) => onChange(event.target.value)}
          onPaste={(event) => {
            if (event.clipboardData.files.length && onFiles && !attachmentsDisabled) {
              event.preventDefault()
              onFiles(Array.from(event.clipboardData.files))
            }
          }}
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
              if (!disabledReason && !sending && !readOnly && value.trim()) onSend()
            }
          }}
        />
        <div className="composer-toolbar">
          <div className="composer-tools">
            <IconButton
              icon={Paperclip}
              label="上传附件"
              onClick={() => fileInput.current?.click()}
              disabled={!onFiles || attachmentsDisabled}
            />
            <input
              ref={fileInput}
              type="file"
              multiple
              className="sr-only"
              aria-label="上传附件文件"
              disabled={!onFiles || attachmentsDisabled}
              onChange={(event) => {
                onFiles?.(Array.from(event.target.files ?? []))
                event.target.value = ''
              }}
            />
            {onAttachments && (
              <button
                type="button"
                className="composer-library"
                onClick={onAttachments}
                disabled={attachmentsDisabled}
              >
                文件库
              </button>
            )}
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
            {reasoningControl}
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
            <button
              className="send-button"
              type="button"
              disabled={!!disabledReason || readOnly || sending || !value.trim()}
              aria-label={sending ? '正在发送' : sendLabel}
              title={disabledReason ?? sendLabel}
              onClick={onSend}
            >
              <ArrowUp size={19} />
            </button>
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
