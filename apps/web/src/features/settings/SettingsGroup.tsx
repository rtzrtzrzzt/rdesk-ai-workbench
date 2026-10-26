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
  children,
}: {
  id: string
  title: string
  description: string
  active: boolean
  onAttentionChange: (id: string, attention: boolean) => void
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
        <h2 id={`settings-${id}-title`}>{title}</h2>
        <p>{description}</p>
      </div>
      {needsAttention && (
        <p className="settings-attention" role="status">
          {messages.join('；')}
        </p>
      )}
      <div className="settings-panel-content">
        <AttentionContext.Provider value={report}>{children}</AttentionContext.Provider>
      </div>
    </section>
  )
}
