import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { commandsApi, COMMAND_PAGE_BYTES } from '../../api/commands'
import type { CommandStream } from '../../api/commands'
import type { CommandView } from '../../api/types'
import { filesApi } from '../../api/files'
import { Button } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'

type Cursor = { offset: number; previous: number[] }
function LogArtifact({
  workspaceId,
  id,
  commandId,
  stream,
  active,
}: {
  workspaceId: string
  id: string
  commandId: string
  stream: CommandStream
  active: boolean
}) {
  const artifact = useQuery({
    queryKey: ['artifact', workspaceId, id],
    queryFn: () => filesApi.artifact(workspaceId, id),
    enabled: active,
  })
  const verified =
    artifact.data &&
    artifact.data.status === 'available' &&
    artifact.data.kind === 'command_log' &&
    artifact.data.metadata.command_id === commandId &&
    artifact.data.metadata.stream === stream
  return (
    <div className="command-log-artifact">
      {verified ? (
        <>
          <a
            className="button button-secondary"
            href={filesApi.downloadUrl(workspaceId, artifact.data.id)}
            download
          >
            下载完整 {stream} 日志
          </a>
          <small>日志版本：{artifact.data.version}</small>
        </>
      ) : (
        <p className={artifact.error || artifact.data ? 'form-error' : 'page-note'}>
          {artifact.error?.message ??
            (artifact.data ? '日志文件类型或来源不符，未打开替代文件。' : '正在核对结束日志…')}
        </p>
      )}
    </div>
  )
}
export function CommandLogs({
  workspaceId,
  command,
  active,
}: {
  workspaceId: string
  command: CommandView
  active: boolean
}) {
  const [stream, setStream] = useState<CommandStream>('stdout')
  const [cursors, setCursors] = useState<Record<CommandStream, Cursor>>({
    stdout: { offset: 0, previous: [] },
    stderr: { offset: 0, previous: [] },
  })
  const [copyNotice, setCopyNotice] = useState('')
  const cursor = cursors[stream]
  const output = useQuery({
    queryKey: ['command-output', workspaceId, command.id, stream, cursor.offset],
    queryFn: () => commandsApi.output(workspaceId, command.id, stream, cursor.offset),
    enabled: active,
  })
  const page = output.data
  const matches =
    page &&
    page.command.id === command.id &&
    page.stream === stream &&
    page.requested_offset === cursor.offset
  const canNext = matches && !page.eof && page.next_offset > cursor.offset
  const canReadNew = matches && page.eof && !page.command.owned_resources_closed
  const artifactId = command.owned_resources_closed ? command.log_artifacts[stream] : undefined
  useEffect(() => setCopyNotice(''), [stream, page?.start_offset, page?.next_offset])
  function move(offset: number) {
    if (!Number.isSafeInteger(offset) || offset < 0) return
    if (offset === cursor.offset) {
      void output.refetch()
      return
    }
    setCursors((old) => ({
      ...old,
      [stream]: { offset, previous: [...old[stream].previous.slice(-99), old[stream].offset] },
    }))
  }
  function previous() {
    setCursors((old) => {
      const history = old[stream].previous
      const offset = history.at(-1)
      return offset === undefined
        ? old
        : { ...old, [stream]: { offset, previous: history.slice(0, -1) } }
    })
  }
  async function copy() {
    if (!matches || !page.text) return
    try {
      if (!navigator.clipboard?.writeText)
        throw new Error('当前浏览器未提供剪贴板写入，请在日志区域手动选择。')
      await navigator.clipboard.writeText(page.text)
      setCopyNotice('当前页已复制。')
    } catch (error) {
      setCopyNotice(error instanceof Error ? error.message : '复制未完成，请手动选择当前页。')
    }
  }
  return (
    <section className="command-logs" aria-label={`命令 ${command.id} 的日志`}>
      <div className="segmented command-streams" role="group" aria-label="日志流">
        {(['stdout', 'stderr'] as const).map((item) => (
          <button
            type="button"
            key={item}
            aria-pressed={stream === item}
            onClick={() => setStream(item)}
          >
            {item === 'stdout' ? '标准输出 stdout' : '标准错误 stderr'}
          </button>
        ))}
      </div>
      <p className="page-note">
        每页最多请求 {COMMAND_PAGE_BYTES} 字节，按服务端返回的 UTF-8 边界翻页。stdout 与 stderr
        分别读取，不推测两者的时间顺序。
      </p>
      <div className="button-row command-log-pagination">
        <Button disabled={output.isFetching} onClick={() => move(0)}>
          回到开头
        </Button>
        <Button disabled={!cursor.previous.length || output.isFetching} onClick={previous}>
          上一页
        </Button>
        <Button
          disabled={!canNext || output.isFetching}
          onClick={() => {
            if (page) move(page.next_offset)
          }}
        >
          下一页
        </Button>
        <Button disabled={output.isFetching} onClick={() => void output.refetch()}>
          刷新当前页
        </Button>
        <Button
          disabled={!canReadNew || output.isFetching}
          onClick={() => {
            if (page) move(page.next_offset)
          }}
        >
          读取页尾后的新输出
        </Button>
        <Button disabled={!matches || !page.text} onClick={() => void copy()}>
          复制当前页
        </Button>
      </div>
      {output.error && (
        <p role="alert" className="form-error">
          {output.error.message}
        </p>
      )}
      {page && !matches && (
        <p role="alert" className="form-error">
          日志分页回执与当前命令、流或偏移不一致，未显示替代内容。
        </p>
      )}
      {matches ? (
        <>
          <p className="page-note">
            请求偏移 {page.requested_offset} · 实际范围 [{page.start_offset}, {page.next_offset})
            字节 · 当前流共 {formatBytes(page.total_bytes)}
            {page.eof ? ' · 已到此读数的页尾' : ' · 此读数尚未到流末尾'}
          </p>
          {page.utf8_boundary_adjusted && (
            <p className="page-note">服务端已调整 UTF-8 字节边界，以保持当前页字符完整。</p>
          )}
          {!page.eof && page.next_offset <= cursor.offset && (
            <p className="page-note">
              当前位置尚无可前进的完整字符，可刷新当前页等待后续字节；不会按字符数自行跳过原始字节。
            </p>
          )}
          {page.decode_replacements !== false && page.decode_replacements !== 0 && (
            <p className="file-partial-warning">
              当前页存在解码替代字符
              {typeof page.decode_replacements === 'number'
                ? `（${page.decode_replacements}）`
                : ''}
              ；原始字节请在结束日志中核对。
            </p>
          )}
          <pre
            tabIndex={0}
            role="region"
            aria-label={`${stream} 当前日志页，可用键盘滚动`}
            className="command-log-output"
          >
            <code>{page.text}</code>
          </pre>
          {!page.text && <p className="page-note">当前读取范围没有输出文本。</p>}
        </>
      ) : (
        !output.error && (
          <p className="page-note">{active ? '正在读取日志页…' : '展开后按需读取日志。'}</p>
        )
      )}
      {copyNotice && (
        <p role="status" className="page-note">
          {copyNotice}
        </p>
      )}
      {artifactId ? (
        <LogArtifact
          workspaceId={workspaceId}
          id={artifactId}
          commandId={command.id}
          stream={stream}
          active={active}
        />
      ) : (
        <p className="page-note">
          {command.owned_resources_closed
            ? '服务器尚未提供此流的完整日志产物。'
            : '受管资源未闭合，完整结束日志尚不可下载。'}
        </p>
      )}
    </section>
  )
}
