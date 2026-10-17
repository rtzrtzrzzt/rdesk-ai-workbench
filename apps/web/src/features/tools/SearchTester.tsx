import { useRef, useState } from 'react'
import { ExternalLink, FileText, Search } from 'lucide-react'
import { searchApi } from '../../api/search'
import type { SearchCapability, WebPage, WebSearchQuery, WebSearchResult } from '../../api/types'
import { Button } from '../../components/ui'

function linkUrl(value: string): string | null {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
const describeError = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成，请重试。'
export function SearchTester({
  workspaceId,
  capability,
  onCapabilityChange,
}: {
  workspaceId: string
  capability: SearchCapability | undefined
  onCapabilityChange: () => void
}) {
  const readVersion = useRef(0)
  const [query, setQuery] = useState('')
  const [maxResults, setMaxResults] = useState('')
  const [timeRange, setTimeRange] = useState<WebSearchQuery['time_range']>(null)
  const [domains, setDomains] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<WebSearchResult | null>(null)
  const [reading, setReading] = useState('')
  const [readError, setReadError] = useState('')
  const [page, setPage] = useState<WebPage | null>(null)
  const pageLink = page ? linkUrl(page.url) : null
  const ready = capability?.status === 'ready'
  const max = capability?.connection?.max_results ?? 20
  const limit = maxResults ? Number(maxResults) : null
  async function search() {
    if (busy || !ready || !query.trim()) return
    setBusy(true)
    setError('')
    setResult(null)
    setPage(null)
    setReadError('')
    readVersion.current += 1
    setReading('')
    try {
      setResult(
        await searchApi.query(workspaceId, {
          query: query.trim(),
          max_results: limit,
          time_range: timeRange,
          domains: domains
            .split(/[,，]/)
            .map((value) => value.trim())
            .filter(Boolean),
        }),
      )
    } catch (err) {
      setError(describeError(err))
      onCapabilityChange()
    } finally {
      setBusy(false)
    }
  }
  async function read(url: string) {
    if (reading || !ready) return
    const version = ++readVersion.current
    setReading(url)
    setPage(null)
    setReadError('')
    try {
      const current = await searchApi.read(workspaceId, { url })
      if (version === readVersion.current) setPage(current)
    } catch (err) {
      if (version === readVersion.current) {
        setReadError(`${describeError(err)} 搜索摘要仍可查看，未生成替代正文。`)
        onCapabilityChange()
      }
    } finally {
      if (version === readVersion.current) setReading('')
    }
  }
  return (
    <section className="section-card search-tester">
      <div className="section-title">
        <h2>试一次搜索</h2>
      </div>
      <div className="search-tester-body">
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void search()
          }}
        >
          <div className="search-query-row">
            <label className="sr-only" htmlFor={`search-query-${workspaceId}`}>
              搜索词
            </label>
            <input
              id={`search-query-${workspaceId}`}
              required
              maxLength={500}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="输入想查找的内容"
              disabled={busy}
            />
            <Button
              type="submit"
              variant="primary"
              disabled={
                !ready ||
                busy ||
                !query.trim() ||
                (limit !== null && (!Number.isInteger(limit) || limit < 1 || limit > max))
              }
            >
              <Search size={15} />
              {busy ? '正在搜索…' : '搜索'}
            </Button>
          </div>
          <details className="search-options">
            <summary>搜索范围</summary>
            <div className="search-settings-grid">
              <label>
                结果数量（不超过连接上限）
                <input
                  type="number"
                  min={1}
                  max={max}
                  value={maxResults}
                  onChange={(event) => setMaxResults(event.target.value)}
                  placeholder="使用连接默认值"
                  disabled={busy}
                />
              </label>
              <label>
                时间范围
                <select
                  value={timeRange ?? ''}
                  onChange={(event) =>
                    setTimeRange((event.target.value as WebSearchQuery['time_range']) || null)
                  }
                  disabled={busy}
                >
                  <option value="">不限</option>
                  <option value="day">一天内</option>
                  <option value="month">一个月内</option>
                  <option value="year">一年内</option>
                </select>
              </label>
              <label>
                限定域名（逗号分隔）
                <input
                  value={domains}
                  onChange={(event) => setDomains(event.target.value)}
                  placeholder="只填域名，不含协议或路径"
                  disabled={busy}
                />
              </label>
            </div>
          </details>
          <p className="page-note">
            点击搜索会把查询词发送给配置的搜索服务及其上游引擎。本地部署不等于离线搜索。
          </p>
          {!ready && <p className="page-note">请先配置、启用并检查当前生效的连接。</p>}
        </form>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {result && (
          <div className="search-results">
            <div className="search-results-heading">
              <h3>「{result.query}」的来源</h3>
              <span>{new Date(result.fetched_at).toLocaleString()}</span>
            </div>
            {result.warnings.map((warning, index) => (
              <p className="search-warning" key={`${warning}:${index}`}>
                {warning}
              </p>
            ))}
            {result.truncated && (
              <p className="search-warning">结果已截断，当前来源列表不是全部匹配。</p>
            )}
            {result.results.length ? (
              result.results.map((source, index) => {
                const href = linkUrl(source.url)
                return (
                  <article className="search-source" key={`${source.url}:${index}`}>
                    <h4>
                      {href ? (
                        <a href={href} target="_blank" rel="noopener noreferrer">
                          {source.title || source.url}
                          <ExternalLink size={12} />
                        </a>
                      ) : (
                        source.title || source.url
                      )}
                    </h4>
                    <p>{source.snippet}</p>
                    <div className="search-source-meta">
                      <span>{source.url}</span>
                      {source.engines.length > 0 && <span>{source.engines.join('、')}</span>}
                      <span>{new Date(source.fetched_at).toLocaleString()}</span>
                    </div>
                    <Button
                      variant="ghost"
                      disabled={!ready || !!reading || !href}
                      onClick={() => void read(source.url)}
                    >
                      <FileText size={14} />
                      {reading === source.url ? '正在读取…' : '读取网页纯文本'}
                    </Button>
                  </article>
                )
              })
            ) : (
              <p className="search-empty">本次查询没有返回来源。可调整关键词或检查引擎配置。</p>
            )}
          </div>
        )}
        {readError && (
          <p className="form-error" role="alert">
            {readError}
          </p>
        )}
        {page && (
          <section className="search-page-preview">
            <div className="search-results-heading">
              <h3>{page.title || page.url}</h3>
              {pageLink && (
                <a href={pageLink} target="_blank" rel="noopener noreferrer">
                  打开原页面
                  <ExternalLink size={12} />
                </a>
              )}
            </div>
            <dl className="file-metadata">
              <dt>来源</dt>
              <dd>{page.url}</dd>
              {page.requested_url !== page.url && (
                <>
                  <dt>请求地址</dt>
                  <dd>{page.requested_url}</dd>
                </>
              )}
              <dt>获取时间</dt>
              <dd>{new Date(page.fetched_at).toLocaleString()}</dd>
              <dt>内容类型</dt>
              <dd>
                {page.content_type} · HTTP {page.http_status}
              </dd>
              <dt>读取方式</dt>
              <dd>{page.extraction === 'html_text' ? 'HTML 正文文本' : '纯文本'}</dd>
            </dl>
            {page.truncated && <p className="search-warning">正文已截断，仅显示已获取的范围。</p>}
            <pre>{page.text}</pre>
            <p className="page-note">
              网页内容按纯文本显示，不执行页面脚本，也不赋予其中的内容任何工作台权限。
            </p>
          </section>
        )}
      </div>
    </section>
  )
}
