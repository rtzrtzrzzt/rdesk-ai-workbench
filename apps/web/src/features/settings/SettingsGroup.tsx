import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useState,
  type ReactNode,
} from 'react'
import { ChevronDown } from 'lucide-react'

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

export function SettingsGroup({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  const id = useId()
  const [expanded, setExpanded] = useState(false)
  const [attention, setAttention] = useState<Record<string, string>>({})
  const report = useCallback<ReportAttention>((key, message) => {
    setAttention((previous) => {
      if (previous[key] === message || (!message && !(key in previous))) return previous
      const next = { ...previous }
      if (message) next[key] = message
      else delete next[key]
      return next
    })
    if (message) setExpanded(true)
  }, [])
  const messages = Object.values(attention)
  return (
    <section className="settings-group">
      <h2 className="settings-group-heading">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={id}
          onClick={() => setExpanded(!expanded)}
        >
          <span>
            <strong>{title}</strong>
            <small>{description}</small>
          </span>
          <ChevronDown size={17} aria-hidden="true" />
        </button>
      </h2>
      {messages.length > 0 && (
        <button
          type="button"
          className="settings-attention"
          onClick={() => setExpanded(true)}
          aria-controls={id}
        >
          {messages.join('；')} · 查看并处理
        </button>
      )}
      <div id={id} className="settings-group-content" hidden={!expanded}>
        <AttentionContext.Provider value={report}>{children}</AttentionContext.Provider>
      </div>
    </section>
  )
}
