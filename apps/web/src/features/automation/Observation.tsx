import { useQuery } from '@tanstack/react-query'
import { filesApi } from '../../api/files'
import { json, number, record, text } from './data'
import { Button } from '../../components/ui'
import { observedDownloadLinks } from './downloads'
import type { ObservedDownloadLink } from './downloads'

function Screenshot({
  workspaceId,
  screenshot,
}: {
  workspaceId: string
  screenshot: Record<string, unknown>
}) {
  const artifactId = text(screenshot.artifact_id),
    previewId = text(screenshot.preview_artifact_id),
    previewVersion = text(screenshot.preview_version)
  const original = useQuery({
    queryKey: ['automation-screenshot', workspaceId, artifactId],
    queryFn: () => filesApi.artifact(workspaceId, artifactId ?? ''),
    enabled: !!artifactId,
  })
  const preview = useQuery({
    queryKey: ['automation-screenshot', workspaceId, previewId],
    queryFn: () => filesApi.artifact(workspaceId, previewId ?? ''),
    enabled: !!previewId,
  })
  const image = preview.data
  const validImage =
    image &&
    image.id === previewId &&
    image.version === previewVersion &&
    image.kind === 'preview' &&
    image.mime_type === 'image/png'
  const validOriginal =
    original.data && original.data.id === artifactId && original.data.kind === 'screenshot'
  return (
    <div className="automation-screenshot">
      <p>原始截图：{artifactId ?? '缺少受管文件引用'}</p>
      <p>
        原始尺寸 {number(screenshot.original_width) ?? '未知'} ×{' '}
        {number(screenshot.original_height) ?? '未知'}；缩略图{' '}
        {number(screenshot.preview_width) ?? '未知'} × {number(screenshot.preview_height) ?? '未知'}
      </p>
      {validImage ? (
        <>
          <img
            src={filesApi.previewImageUrl(workspaceId, image.id)}
            alt="最近一次明确观察得到的窗口缩略图"
          />
          <small>缩略图版本：{image.version}</small>
        </>
      ) : (
        <p className="page-note">
          {preview.error?.message ??
            (image ? '缩略图类型或版本不符，未显示。' : '正在核对受管缩略图…')}
        </p>
      )}
      {validOriginal ? (
        <>
          <a href={filesApi.downloadUrl(workspaceId, original.data.id)} download>
            下载原始截图，核对动作坐标
          </a>
          <small>原件版本：{original.data.version}</small>
        </>
      ) : (
        original.error && <p role="alert">{original.error.message}</p>
      )}
      <p className="page-note">坐标使用原始截图像素。此缩略图不提供点击映射。</p>
    </div>
  )
}
export function Observation({
  workspaceId,
  value,
  onDownload,
  downloadDisabled = true,
}: {
  workspaceId: string
  value: Record<string, unknown> | null
  onDownload?: (link: ObservedDownloadLink) => void
  downloadDisabled?: boolean
}) {
  if (!value) return <p className="page-note">尚未观察；没有网页内容或截图。</p>
  const screenshot = record(value.screenshot)
  const elements = Array.isArray(value.elements)
    ? value.elements.map(record).filter((item) => item !== null)
    : []
  const links = observedDownloadLinks(value)
  return (
    <div className="automation-observation">
      <dl className="run-meta">
        <dt>观察 ID</dt>
        <dd>{text(value.observation_id) ?? '未知'}</dd>
        <dt>观察时间</dt>
        <dd>{text(value.captured_at) ?? '未知'}</dd>
        <dt>Generation</dt>
        <dd>{number(value.generation) ?? '未知'}</dd>
      </dl>
      {text(value.title) && <p>{text(value.title)}</p>}
      {text(value.url) && <p className="automation-wrap">{text(value.url)}</p>}
      {text(value.text) !== null && (
        <details>
          <summary>网页纯文本{value.truncated === true ? '（已截断）' : ''}</summary>
          <pre>{text(value.text)}</pre>
        </details>
      )}
      {Array.isArray(value.elements) && (
        <details>
          <summary>DOM 元素引用（{elements.length}）</summary>
          {elements.length ? (
            <div className="automation-table">
              <table>
                <thead>
                  <tr>
                    <th>引用</th>
                    <th>标签 / 角色</th>
                    <th>名称</th>
                    <th>输入类型</th>
                    <th>允许的链接</th>
                  </tr>
                </thead>
                <tbody>
                  {elements.map((element, index) => (
                    <tr key={`${text(element.ref)}-${index}`}>
                      <td>{text(element.ref)}</td>
                      <td>
                        {text(element.tag)} / {text(element.role)}
                      </td>
                      <td>{text(element.name)}</td>
                      <td>{text(element.input_type) ?? '—'}</td>
                      <td>
                        {(() => {
                          const link = links.find((item) => item.targetRef === text(element.ref))
                          return link ? (
                            <div className="automation-download-link">
                              <span>{link.url}</span>
                              {onDownload && (
                                <Button
                                  disabled={downloadDisabled}
                                  onClick={() => onDownload(link)}
                                >
                                  下载此链接
                                </Button>
                              )}
                            </div>
                          ) : (
                            '—'
                          )
                        })()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="page-note">此观察没有可用元素引用。</p>
          )}
        </details>
      )}
      {screenshot && <Screenshot workspaceId={workspaceId} screenshot={screenshot} />}
      <details>
        <summary>观察元数据与几何信息</summary>
        <pre>{json(value)}</pre>
      </details>
    </div>
  )
}
