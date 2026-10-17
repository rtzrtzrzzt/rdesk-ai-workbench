import { useId, useLayoutEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import { IconButton } from './ui'
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const titleId = useId()
  const ref = useRef<HTMLDialogElement>(null)
  useLayoutEffect(() => {
    const dialog = ref.current
    const opener = document.activeElement
    dialog?.showModal()
    // Close before React removes the dialog so keyboard focus can return to its trigger.
    return () => {
      dialog?.close()
      if (opener instanceof HTMLElement && opener.isConnected && !opener.matches(':disabled'))
        opener.focus({ preventScroll: true })
    }
  }, [])
  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="dialog-inner">
        <div className="dialog-title">
          <h2 id={titleId}>{title}</h2>
          <IconButton icon={X} label="关闭" onClick={onClose} />
        </div>
        {children}
      </div>
    </dialog>
  )
}
