import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

type ReportAttention = (id: string, message: string | null) => void
const AttentionContext = createContext<ReportAttention | null>(null)

// Report only derived UI state. Request metadata remains owned by the original component.
export function useSettingsAttention(id: string, active: boolean, message: string) {
  const report = useContext(AttentionContext)
  useEffect(() => {
    report?.(id, active ? message : null)
    return () => report?.(id, null)
  }, [report, id, active, message])
}

export function SettingsPanel({
  id,
  title,
  description,
  active,
  onAttentionChange,
  back,
  children,
}: {
  id: string
  title: string
  description: string
  active: boolean
  onAttentionChange: (id: string, attention: boolean) => void
  back?: { label: string; onClick: () => void }
  children: ReactNode
}) {
  const [attention, setAttention] = useState<Record<string, string>>({})
  const report = useCallback<ReportAttention>((key, message) => {
    setAttention((previous) => {
      if (previous[key] === message || (!message && !(key in previous))) return previous
      const next = { ...previous }
      if (message) next[key] = message
      else delete next[key]
      return next
    })
  }, [])
  const messages = Object.values(attention)
  const needsAttention = messages.length > 0
  useEffect(() => {
    onAttentionChange(id, needsAttention)
    return () => onAttentionChange(id, false)
  }, [id, needsAttention, onAttentionChange])
  return (
    <section
      id={`settings-${id}`}
      className="settings-panel"
      aria-labelledby={`settings-${id}-title`}
      hidden={!active}
    >
      <div className="settings-panel-heading">
        {back && (
          <button type="button" className="settings-back" onClick={back.onClick}>
            ← {back.label}
          </button>
        )}
        <h2 id={`settings-${id}-title`} tabIndex={-1}>
          {title}
        </h2>
        <p>{description}</p>
      </div>
      {needsAttention && (
        <p className="settings-attention" role="status">
          {messages.join('；')}
        </p>
      )}
      <div className="settings-panel-content">
        {/* Keep recovery notices, drafts and one-time credentials alive across navigation. */}
        <AttentionContext.Provider value={report}>{children}</AttentionContext.Provider>
      </div>
    </section>
  )
}
