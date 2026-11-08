import { useEffect, useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

// Native validation must be able to focus controls in any nested disclosure.
export function revealSettingsControl(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return
  let parent = target.parentElement
  while (parent) {
    if (parent instanceof HTMLDetailsElement) parent.open = true
    parent = parent.parentElement
  }
}

export function SettingsDisclosure({
  title,
  summary,
  children,
  attention = false,
  defaultOpen = false,
  className = '',
}: {
  title: ReactNode
  summary?: ReactNode
  children: ReactNode
  attention?: boolean
  defaultOpen?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  useEffect(() => {
    if (attention) setOpen(true)
  }, [attention])
  return (
    <details
      className={`settings-disclosure ${className}`}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <ChevronRight size={14} aria-hidden="true" />
        <span>{title}</span>
        {summary && <small>{summary}</small>}
      </summary>
      <div className="settings-disclosure-content">{children}</div>
    </details>
  )
}
