import { Component, lazy, Suspense, useId, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { ChartProps, FileLinkRequest, SurfaceComponent, TrustedSurfaceProps } from './types'
import type { InspectedSurface } from './validation'
import { inspectSurface, plainJSON } from './validation'
import './generated.css'
const SurfaceChart = lazy(() => import('./SurfaceChart'))
function Fallback({ reason, value }: { reason: string; value?: unknown }) {
  return (
    <section className="wb-generated-fallback" role="status">
      <p>{reason}</p>
      {value !== undefined && (
        <details>
          <summary>只读数据</summary>
          <pre>{plainJSON(value)}</pre>
        </details>
      )}
    </section>
  )
}
class NodeBoundary extends Component<{ children: ReactNode; value: unknown }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? (
      <Fallback reason="此组件无法呈现，已保留只读数据。" value={this.props.value} />
    ) : (
      this.props.children
    )
  }
}
function ManagedFile({
  request,
  onFileLink,
}: {
  request: FileLinkRequest
  onFileLink: TrustedSurfaceProps['onFileLink']
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  return (
    <span className="wb-managed-file">
      <button
        type="button"
        disabled={!onFileLink || busy}
        onClick={() => {
          if (!onFileLink) return
          setBusy(true)
          setError('')
          void Promise.resolve()
            .then(() => onFileLink(request))
            .catch((reason) =>
              setError(reason instanceof Error ? reason.message : '文件读取未完成'),
            )
            .finally(() => setBusy(false))
        }}
      >
        {request.label || '查看受管文件'}
        {busy ? ' · 正在读取…' : ''}
      </button>
      {!onFileLink && <small>宿主文件入口未接入</small>}
      {error && (
        <small role="alert" className="wb-generated-error">
          {error}
        </small>
      )}
    </span>
  )
}
function DataTable({ columns, rows }: { columns: string[]; rows: (string | number | null)[][] }) {
  const [filter, setFilter] = useState('')
  const id = useId()
  const visible = filter.trim()
    ? rows.filter((row) =>
        row.some((cell) =>
          String(cell ?? '')
            .toLocaleLowerCase()
            .includes(filter.trim().toLocaleLowerCase()),
        ),
      )
    : rows
  return (
    <div className="wb-surface-table">
      <label htmlFor={id}>筛选当前表格</label>
      <input id={id} value={filter} onChange={(event) => setFilter(event.target.value)} />
      {rows.length === 0 ? (
        <p className="wb-generated-note">没有数据行。</p>
      ) : (
        <>
          <p className="wb-generated-note">
            显示 {visible.length} / {rows.length} 行；null 表示缺失，不作为零。
          </p>
          <div className="wb-surface-table-scroll" tabIndex={0} role="region" aria-label="数据表格">
            <table>
              <caption className="wb-generated-sr">来源绑定的数据表</caption>
              <thead>
                <tr>
                  {columns.map((column, index) => (
                    <th scope="col" key={index}>
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((row, index) => (
                  <tr key={index}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex}>{cell === null ? '缺失' : String(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!visible.length && <p className="wb-generated-note">没有符合筛选的数据行。</p>}
        </>
      )}
    </div>
  )
}
function Chart({ chart }: { chart: ChartProps }) {
  return (
    <figure className="wb-surface-figure">
      {chart.points.length === 0 ? (
        <p className="wb-generated-note">没有图表数据。</p>
      ) : chart.points.every((point) => point.value === null) ? (
        <p className="wb-generated-note">所有数值均缺失，未绘制图形。</p>
      ) : (
        <NodeBoundary value={chart}>
          <Suspense fallback={<p className="wb-generated-note">正在载入图表；数据表已可读取。</p>}>
            <SurfaceChart chart={chart} />
          </Suspense>
        </NodeBoundary>
      )}
      <details open={chart.points.length === 0}>
        <summary>查看图表数据与缺失值</summary>
        <DataTable
          columns={[chart.x_label, `${chart.y_label}${chart.unit ? `（${chart.unit}）` : ''}`]}
          rows={chart.points.map((point) => [point.label, point.value])}
        />
      </details>
    </figure>
  )
}
function RenderDocument({
  document,
  errors,
  onFileLink,
}: {
  document: InspectedSurface
  errors: Map<string, string>
  onFileLink: TrustedSurfaceProps['onFileLink']
}) {
  const nodes = new Map(document.components.map((node) => [node.id, node]))
  const file = (artifactId: string, label: string): FileLinkRequest => ({
    artifact_id: artifactId,
    label,
    ...(document.sources.find((source) => source.artifact_id === artifactId)
      ? { version: document.sources.find((source) => source.artifact_id === artifactId)?.version }
      : {}),
    ...(document.artifact_id ? { surface_artifact_id: document.artifact_id } : {}),
    ...(document.run_id ? { run_id: document.run_id } : {}),
  })
  function render(id: string): ReactNode {
    const inspected = nodes.get(id)
    if (!inspected) return <Fallback reason="组件引用不存在。" />
    const reason = errors.get(id)
    if (reason || !inspected.component)
      return <Fallback key={id} reason={reason ?? '组件未通过验证。'} value={inspected.raw} />
    const node = inspected.component
    const children = () => node.children.map((child) => <div key={child}>{render(child)}</div>)
    function content(node: SurfaceComponent): ReactNode {
      switch (node.type) {
        case 'Text':
          return <p className="wb-surface-text">{node.props.text}</p>
        case 'Heading':
          return node.props.level === 2 ? <h2>{node.props.text}</h2> : <h3>{node.props.text}</h3>
        case 'Card':
          return (
            <section className="wb-surface-card">
              {node.props.title && <h3>{node.props.title}</h3>}
              {children()}
            </section>
          )
        case 'Column':
        case 'Row':
          return (
            <div className={`wb-surface-${node.type.toLowerCase()}`}>
              {node.props.title && <h3>{node.props.title}</h3>}
              {children()}
            </div>
          )
        case 'Details':
          return (
            <details className="wb-surface-details">
              <summary>{node.props.title || '查看详情'}</summary>
              {children()}
            </details>
          )
        case 'List':
          return node.props.items.length ? (
            <ul className="wb-surface-list">
              {node.props.items.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : (
            <p className="wb-generated-note">没有列表项。</p>
          )
        case 'Table':
          return <DataTable columns={node.props.columns} rows={node.props.rows} />
        case 'Chart':
          return <Chart chart={node.props} />
        case 'FileLink':
          return (
            <ManagedFile
              request={file(node.props.artifact_id, node.props.label)}
              onFileLink={onFileLink}
            />
          )
      }
    }
    return (
      <NodeBoundary key={`${id}:${plainJSON(node)}`} value={node}>
        {content(node)}
      </NodeBoundary>
    )
  }
  return (
    <section className="wb-trusted-surface">
      <header>
        {document.title && <h2>{document.title}</h2>}
        <p className="wb-generated-note">workbench-ui/1 · workbench-core/1 · 只读组合</p>
      </header>
      {render(document.root_id)}
      <details className="wb-surface-sources">
        <summary>来源与产物追溯</summary>
        {document.artifact_id && <p>界面产物：{document.artifact_id}</p>}
        {document.run_id && <p>Run：{document.run_id}</p>}
        {document.sources.length ? (
          <ul>
            {document.sources.map((source) => (
              <li key={source.artifact_id}>
                <ManagedFile
                  request={file(source.artifact_id, source.artifact_id)}
                  onFileLink={onFileLink}
                />
                <small>版本：{source.version}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p>未绑定数据来源；表格与图表不允许显示。</p>
        )}
        <p className="wb-generated-note">
          来源版本由服务端核验；此渲染器只验证目录与数据结构，不证明数值已复核。
        </p>
      </details>
    </section>
  )
}
export function TrustedSurface({ surface, onFileLink }: TrustedSurfaceProps) {
  const inspection = useMemo(() => {
    try {
      return inspectSurface(surface)
    } catch {
      return { document: null, errors: new Map<string, string>(), reason: '产物无法安全解析。' }
    }
  }, [surface])
  if (!inspection.document)
    return (
      <div className="wb-trusted-surface">
        <Fallback reason={inspection.reason ?? '产物结构无效。'} value={surface} />
      </div>
    )
  return (
    <NodeBoundary key={plainJSON(surface)} value={surface}>
      <RenderDocument
        document={inspection.document}
        errors={inspection.errors}
        onFileLink={onFileLink}
      />
    </NodeBoundary>
  )
}
