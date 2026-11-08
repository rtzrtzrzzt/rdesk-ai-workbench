import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'

/** Native exclusive disclosures keep all composer menus on one interaction model. */
export function ComposerMenu({
  label,
  trigger,
  children,
  disabled = false,
  className = '',
}: {
  label: string
  trigger: ReactNode
  children: (close: () => void) => ReactNode
  disabled?: boolean
  className?: string
}) {
  const panel = useRef<HTMLDetailsElement>(null)
  const id = useId()
  function close() {
    if (!panel.current) return
    panel.current.open = false
    panel.current.querySelector('summary')?.focus()
  }
  useEffect(() => {
    if (disabled && panel.current) panel.current.open = false
  }, [disabled])
  useEffect(() => {
    function outside(event: PointerEvent) {
      if (panel.current?.open && !panel.current.contains(event.target as Node))
        panel.current.open = false
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [])
  return (
    <details
      ref={panel}
      name="composer-menu"
      className={`composer-menu ${className}`}
      onKeyDown={(event) => {
        if (!panel.current?.open) return
        if (event.key === 'Escape') {
          event.preventDefault()
          event.stopPropagation()
          close()
        } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          const items = Array.from(
            panel.current.querySelectorAll<HTMLButtonElement>(
              '[role="menuitemradio"]:not(:disabled)',
            ),
          )
          if (!items.length) return
          event.preventDefault()
          const index = items.indexOf(document.activeElement as HTMLButtonElement)
          const next =
            event.key === 'Home'
              ? 0
              : event.key === 'End'
                ? items.length - 1
                : index < 0
                  ? event.key === 'ArrowUp'
                    ? items.length - 1
                    : 0
                  : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
          items[next]?.focus()
        }
      }}
      onBlur={(event) => {
        if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node))
          event.currentTarget.open = false
      }}
    >
      <summary
        role="button"
        aria-label={label}
        aria-controls={id}
        aria-haspopup="menu"
        aria-disabled={disabled || undefined}
        tabIndex={disabled ? -1 : 0}
        onClick={(event) => {
          if (disabled) event.preventDefault()
        }}
      >
        {trigger}
      </summary>
      <div id={id} className="composer-menu-panel" role="menu" aria-label={label}>
        {children(close)}
      </div>
    </details>
  )
}
