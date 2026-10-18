import { memo, useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Check, Copy } from 'lucide-react'
import Markdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ManagedFileLink } from './FilePreview'
import { createContext, useContext } from 'react'
const WorkspaceContext = createContext<string | undefined>(undefined)
function FileAnchor({ href, children }: { href: string; children?: ReactNode }) {
  return (
    <ManagedFileLink href={href} workspaceId={useContext(WorkspaceContext)}>
      {children}
    </ManagedFileLink>
  )
}

type MessageText = { role: string; content?: string | null; reasoning?: string | null }

/** Tool-call assistant records may have no visible text; attachment-only user messages remain visible. */
export function shouldRenderMessage(
  message: MessageText,
  streamedContent?: string,
  streamedReasoning?: string,
): boolean {
  return (
    message.role !== 'assistant' ||
    (streamedContent ?? message.content ?? '').trim().length > 0 ||
    (streamedReasoning ?? message.reasoning ?? '').trim().length > 0
  )
}

function CodeBlock({
  code,
  language,
  children,
}: {
  code: string
  language?: string
  children: ReactNode
}) {
  const [copyState, setCopyState] = useState<'idle' | 'copying' | 'copied' | 'failed'>('idle')
  const latestCode = useRef(code)
  useEffect(() => {
    latestCode.current = code
    setCopyState((current) => (current === 'copied' ? 'idle' : current))
  }, [code])
  useEffect(() => {
    if (copyState !== 'copied') return
    const timer = window.setTimeout(() => setCopyState('idle'), 2000)
    return () => window.clearTimeout(timer)
  }, [copyState])
  async function copy() {
    if (copyState === 'copying') return
    setCopyState('copying')
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(code)
      setCopyState(latestCode.current === code ? 'copied' : 'idle')
    } catch {
      setCopyState('failed')
    }
  }
  return (
    <section className="chat-code-block">
      <div className="chat-code-heading">
        <span>{language || '代码'}</span>
        <button
          type="button"
          className="chat-code-copy"
          onClick={() => void copy()}
          disabled={!code || copyState === 'copying'}
          aria-label={copyState === 'copied' ? '代码已复制' : '复制代码'}
        >
          {copyState === 'copied' ? <Check size={13} /> : <Copy size={13} />}
          {copyState === 'copied' ? '已复制' : copyState === 'copying' ? '正在复制…' : '复制'}
        </button>
      </div>
      <pre tabIndex={0}>{children}</pre>
      <span className="chat-copy-status" role="status">
        {copyState === 'failed'
          ? '浏览器未允许自动复制，请选中代码后手动复制。'
          : copyState === 'copied'
            ? '代码已复制。'
            : ''}
      </span>
    </section>
  )
}

// Stable component identities preserve code-block state while streamed Markdown grows.
const components: Components = {
  pre({ node, children }) {
    const element = node?.children.find(
      (child) => child.type === 'element' && child.tagName === 'code',
    )
    const code =
      element?.type === 'element'
        ? element.children.map((child) => (child.type === 'text' ? child.value : '')).join('')
        : ''
    const classes = element?.type === 'element' ? element.properties.className : undefined
    const language = Array.isArray(classes)
      ? classes.find((value) => typeof value === 'string' && value.startsWith('language-'))
      : undefined
    return (
      <CodeBlock
        code={code}
        language={typeof language === 'string' ? language.slice('language-'.length) : undefined}
      >
        {children}
      </CodeBlock>
    )
  },
  a({ href, children }) {
    if (!href) return <span>{children}</span>
    return <FileAnchor href={href}>{children}</FileAnchor>
  },
  img({ src, alt }) {
    return typeof src === 'string' && src ? (
      <FileAnchor href={src}>{alt || '查看图片'}</FileAnchor>
    ) : (
      <span>{alt || '图片地址不可用'}</span>
    )
  },
  table({ children }) {
    return (
      <div className="chat-markdown-table">
        <table>{children}</table>
      </div>
    )
  },
}

export const MessageContent = memo(function MessageContent({
  content,
  role = 'assistant',
  workspaceId,
}: {
  content: string
  role?: string
  workspaceId?: string
}) {
  const markdownId = useId()
  if (role === 'user') return <div className="chat-message-text">{content}</div>
  return (
    <WorkspaceContext value={workspaceId}>
      <div className="chat-markdown">
        <Markdown
          remarkPlugins={[remarkGfm]}
          remarkRehypeOptions={{ clobberPrefix: `message-${markdownId}-` }}
          components={components}
          skipHtml
        >
          {content}
        </Markdown>
      </div>
    </WorkspaceContext>
  )
})
