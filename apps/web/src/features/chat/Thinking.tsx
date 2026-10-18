import { useState } from 'react'
import { Brain, ChevronRight } from 'lucide-react'
import { MessageContent } from './MessageContent'

export function Thinking({
  text,
  active,
  workspaceId,
}: {
  text: string
  active: boolean
  workspaceId: string
}) {
  const [open, setOpen] = useState(false)
  if (!text.trim()) return null
  return (
    <section className="message-thinking">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open)
        }}
      >
        <Brain size={14} />
        <span>{active ? '正在思考' : '思考过程'}</span>
        <ChevronRight className={open ? 'expanded' : ''} size={13} />
      </button>
      {open && (
        <div className="thinking-content">
          <MessageContent content={text} workspaceId={workspaceId} />
        </div>
      )}
    </section>
  )
}
