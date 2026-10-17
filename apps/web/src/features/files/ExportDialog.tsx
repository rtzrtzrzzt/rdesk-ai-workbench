import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { filesApi } from '../../api/files'
import type { ArtifactView, ExportCreate, FileTask } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, Status } from '../../components/ui'
import { isOfficeSource } from './shared'

type Spec = ExportCreate['spec']
type Format = Spec['format']
type JSONValue = Extract<Spec, { format: 'json' }>['value']
type Cell = Extract<Spec, { format: 'csv' }>['rows'][number][number]
const formats: Format[] = [
  'txt',
  'md',
  'docx',
  'pptx',
  'csv',
  'json',
  'xlsx',
  'png',
  'jpeg',
  'webp',
  'pdf',
]
const pendingKey = (id: string) => `workbench.export-request.${id}`
function isJSONValue(value: unknown): value is JSONValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isJSONValue)
  return typeof value === 'object' && Object.values(value).every(isJSONValue)
}
function isCell(value: unknown): value is Cell {
  return (
    value === null ||
    typeof value === 'boolean' ||
    (typeof value === 'string' && value.length <= 32767) ||
    (typeof value === 'number' && Number.isFinite(value))
  )
}
function parseRows(text: string): Cell[][] {
  if (!text.trim()) throw new Error('请输入实际行数据 JSON；不会自动填充示例。')
  const value: unknown = JSON.parse(text)
  if (
    !Array.isArray(value) ||
    value.length > 5000 ||
    !value.every(
      (row): row is Cell[] => Array.isArray(row) && row.length <= 100 && row.every(isCell),
    )
  )
    throw new Error(
      '行数据须为二维 JSON 数组，最多 5000 行、每行 100 列，单元格为文本、有限数值、布尔值或 null。',
    )
  return value
}
function readPending(id: string): ExportCreate | null {
  try {
    const value = JSON.parse(localStorage.getItem(pendingKey(id)) ?? 'null') as ExportCreate | null
    return value &&
      typeof value.request_id === 'string' &&
      typeof value.filename === 'string' &&
      value.spec &&
      formats.includes(value.spec.format)
      ? value
      : null
  } catch {
    return null
  }
}
export function ExportDialog({
  workspaceId,
  artifacts,
  onCreated,
  onClose,
}: {
  workspaceId: string
  artifacts: ArtifactView[]
  onCreated: (task: FileTask) => Promise<void>
  onClose: () => void
}) {
  const [format, setFormat] = useState<Format>('txt')
  const [filename, setFilename] = useState('')
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [sheet, setSheet] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [width, setWidth] = useState('')
  const [height, setHeight] = useState('')
  const [frame, setFrame] = useState('')
  const [quality, setQuality] = useState(90)
  const [background, setBackground] = useState('#FFFFFF')
  const [preserve, setPreserve] = useState(false)
  const [pending, setPending] = useState(() => readPending(workspaceId))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const capabilities = useQuery({
    queryKey: ['export-capabilities', workspaceId],
    queryFn: () => filesApi.exportCapabilities(workspaceId),
  })
  const capability = capabilities.data?.find((item) => item.formats.includes(format))
  const image = format === 'png' || format === 'jpeg' || format === 'webp'
  function createSpec(): Spec {
    if (format === 'txt' || format === 'md') {
      if (!text.trim()) throw new Error('请输入要导出的实际文本。')
      return { format, text }
    }
    if (format === 'json') {
      if (!text.trim()) throw new Error('请输入实际 JSON 内容。')
      const value: unknown = JSON.parse(text)
      if (!isJSONValue(value)) throw new Error('内容包含无效 JSON 数值。')
      return { format, value }
    }
    if (format === 'csv')
      return { format, rows: parseRows(text), formula_policy: preserve ? 'preserve' : 'escape' }
    if (format === 'xlsx') {
      if (!sheet.trim()) throw new Error('请输入工作表名称。')
      return { format, sheets: [{ name: sheet.trim(), rows: parseRows(text) }] }
    }
    if (format === 'docx') {
      if (!text.trim()) throw new Error('请输入正文。')
      return { format, paragraphs: text.split(/\r?\n/), tables: [] }
    }
    if (format === 'pptx') {
      if (!text.trim() && !title.trim()) throw new Error('请输入幻灯片标题或正文。')
      return {
        format,
        slides: [{ title, paragraphs: text ? text.split(/\r?\n/) : [], table: null }],
      }
    }
    if (image) {
      const source = artifacts.find((a) => a.id === sourceId)
      if (!source) throw new Error('请选择要转换的真实源文件。')
      return {
        format,
        source: { artifact_id: source.id, expected_version: source.version },
        frame_index: frame === '' ? null : Number(frame),
        max_width: width === '' ? null : Number(width),
        max_height: height === '' ? null : Number(height),
        quality,
        jpeg_background: background,
      }
    }
    if (format === 'pdf') {
      const source = artifacts.find(
        (artifact) => artifact.id === sourceId && isOfficeSource(artifact),
      )
      if (!source) throw new Error('请选择一个实际可用的无宏 Office 源文件。')
      return { format, source: { artifact_id: source.id, expected_version: source.version } }
    }
    throw new Error('请选择支持的导出格式。')
  }
  async function submit() {
    if (busy) return
    setError('')
    let command: ExportCreate
    try {
      command = pending ?? {
        request_id: crypto.randomUUID(),
        filename: filename.trim(),
        spec: createSpec(),
        sources: [],
      }
      if (!command.filename) throw new Error('请输入包含目标扩展名的文件名。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '内容不符合导出格式')
      return
    }
    try {
      localStorage.setItem(pendingKey(workspaceId), JSON.stringify(command))
    } catch {
      setError('无法保存待确认导出请求；本次尚未提交，请释放浏览器存储或缩小内容后重试。')
      return
    }
    setBusy(true)
    setPending(command)
    try {
      const task = await filesApi.export(workspaceId, command)
      setPending(null)
      localStorage.removeItem(pendingKey(workspaceId))
      await onCreated(task)
      onClose()
    } catch (e) {
      const uncertain = e instanceof TypeError || (e instanceof ApiError && e.status >= 500)
      if (!uncertain) {
        setPending(null)
        localStorage.removeItem(pendingKey(workspaceId))
      }
      setError(
        (e instanceof Error ? e.message : '导出请求未完成') +
          (uncertain ? ' 结果未确认，核对将复用完整内容与请求标识。' : ''),
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog title="创建导出产物" onClose={onClose}>
      <form
        className="stacked-form"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <p className="page-note">
          仅使用你输入的内容，经持久文件任务生成产物。提交后在文件任务中查看真实状态、预览与下载。
        </p>
        {pending && (
          <div className="memory-conflict">
            <strong>有导出请求需要确认</strong>
            <p>
              {pending.filename} · {pending.spec.format}
              。核对使用原内容，不会应用下方更改。为原样重试，请求正文暂存在此浏览器，确认后清除。
            </p>
            <Button disabled={busy} onClick={() => void submit()}>
              核对本次导出请求
            </Button>
          </div>
        )}
        <fieldset disabled={busy || !!pending} className="export-fields">
          <label>
            格式
            <select
              value={format}
              onChange={(e) => {
                const chosen = formats.find((f) => f === e.target.value)
                if (chosen) setFormat(chosen)
              }}
            >
              {formats.map((f) => (
                <option
                  key={f}
                  value={f}
                  disabled={
                    !capabilities.data?.some(
                      (c) => c.formats.includes(f) && c.status === 'available',
                    )
                  }
                >
                  {f.toUpperCase()}
                  {capabilities.data?.some((c) => c.formats.includes(f) && c.status === 'available')
                    ? ''
                    : ' · 不可用'}
                </option>
              ))}
            </select>
          </label>
          <label>
            文件名（含扩展名）
            <input
              required
              maxLength={255}
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
            />
          </label>
          {capability && (
            <div>
              <Status tone={capability.status === 'available' ? 'good' : 'neutral'}>
                {capability.status === 'available' ? '适配器可调用' : '暂不可用'}
              </Status>
              <p className="page-note">
                {capability.limitation}
                {capability.version ? ` 组件版本 ${capability.version}` : ''}
              </p>
            </div>
          )}
          {format === 'xlsx' && (
            <label>
              工作表名称
              <input
                required
                maxLength={31}
                value={sheet}
                onChange={(e) => setSheet(e.target.value)}
              />
            </label>
          )}
          {format === 'pptx' && (
            <label>
              单页幻灯片标题
              <input maxLength={300} value={title} onChange={(e) => setTitle(e.target.value)} />
            </label>
          )}
          {image ? (
            <>
              <label>
                源文件
                <select required value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                  <option value="">选择实际图像或动画文件</option>
                  {artifacts
                    .filter((a) => a.status === 'available')
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.filename}
                      </option>
                    ))}
                </select>
              </label>
              <p className="page-note">
                只转换选择的源文件。动画含多帧时须明确指定帧（从 0
                开始）；缩放会损失细节。输入是否支持由实际适配器检查。
              </p>
              <label>
                帧索引（单帧可留空）
                <input
                  type="number"
                  min={0}
                  max={1000}
                  value={frame}
                  onChange={(e) => setFrame(e.target.value)}
                />
              </label>
              <label>
                最大宽度（留空不指定）
                <input
                  type="number"
                  min={1}
                  max={4096}
                  value={width}
                  onChange={(e) => setWidth(e.target.value)}
                />
              </label>
              <label>
                最大高度（留空不指定）
                <input
                  type="number"
                  min={1}
                  max={4096}
                  value={height}
                  onChange={(e) => setHeight(e.target.value)}
                />
              </label>
              <label>
                质量
                <input
                  type="number"
                  min={1}
                  max={95}
                  required
                  value={quality}
                  onChange={(e) => setQuality(Number(e.target.value))}
                />
              </label>
              {format === 'jpeg' && (
                <label>
                  透明区域的 JPEG 背景颜色
                  <input
                    required
                    pattern="#[a-fA-F0-9]{6}"
                    value={background}
                    onChange={(e) => setBackground(e.target.value)}
                  />
                </label>
              )}
            </>
          ) : format === 'pdf' ? (
            <>
              <label>
                实际 Office 源文件
                <select required value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                  <option value="">请选择无宏 Office 原件</option>
                  {artifacts.filter(isOfficeSource).map((artifact) => (
                    <option key={artifact.id} value={artifact.id}>
                      {artifact.filename} · 版本 {artifact.version.slice(0, 12)}
                    </option>
                  ))}
                </select>
              </label>
              <p className="page-note">
                仅将所选 DOCX / XLSX / PPTX / ODT / ODS / ODP 转换成独立
                PDF。版本在提交时锁定，原件保留；宏、嵌入对象和外部关系会被拒绝。转换版面需要核对，不代表模型原生支持。
              </p>
              {!artifacts.some(isOfficeSource) && (
                <p className="page-note">
                  当前文件库没有可选择的 Office 源文件，请先上传真实文件。
                </p>
              )}
            </>
          ) : (
            <label>
              {format === 'csv' || format === 'xlsx'
                ? '实际行数据（二维 JSON 数组）'
                : format === 'json'
                  ? 'JSON 内容'
                  : '正文（每行一个段落）'}
              <textarea
                rows={10}
                spellCheck={false}
                required={format !== 'pptx'}
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
          )}
          {format === 'csv' && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={preserve}
                onChange={(e) => setPreserve(e.target.checked)}
              />
              <span>保留可能被表格软件解释为公式的文本（默认转义）</span>
            </label>
          )}
          {(format === 'csv' || format === 'xlsx') && (
            <p className="page-note">
              不会自动填充行或推测列。此表单只接受文本、有限数值、布尔值与 null；XLSX
              公式不在此表单中启用。
            </p>
          )}
          {format === 'docx' && (
            <p className="page-note">按文本段落生成 DOCX，不转换 Markdown 样式或复刻原文档布局。</p>
          )}
          {format === 'pptx' && (
            <p className="page-note">生成一页标题与文本幻灯片，不自动拆分内容或生成演示文稿。</p>
          )}
        </fieldset>
        {capabilities.isError && (
          <p className="form-error" role="alert">
            {capabilities.error.message}
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="button-row">
          <Button onClick={onClose}>关闭</Button>
          <Button
            variant="primary"
            type="submit"
            disabled={busy || !!pending || capability?.status !== 'available'}
          >
            {busy ? '正在提交…' : '创建导出任务'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
