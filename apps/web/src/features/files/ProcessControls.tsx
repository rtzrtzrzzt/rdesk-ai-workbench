import { useId, useState } from 'react'
import { Play } from 'lucide-react'
import type { ArtifactView, ProcessFile, ProcessorCapability } from '../../api/types'
import { Button } from '../../components/ui'
import { artifactFormat, operationLabels, relevantProcessors } from './shared'
import './processing.css'

export type ProcessSettings = Omit<ProcessFile, 'request_id'>
type Operation = NonNullable<ProcessFile['operation']>
type Language = NonNullable<NonNullable<ProcessFile['ocr']>['languages']>[number]
const languages: { id: Language; label: string }[] = [
  { id: 'eng', label: '英文' },
  { id: 'chi_sim', label: '简体中文' },
  { id: 'chi_tra', label: '繁体中文' },
]
export function parseOCRPages(value: string): number[] {
  if (!value.trim()) throw new Error('PDF OCR 必须明确选择 1–8 个页码。')
  const pages = value
    .trim()
    .split(/[,，\s]+/)
    .map(Number)
  if (
    !pages.length ||
    pages.length > 8 ||
    new Set(pages).size !== pages.length ||
    pages.some((page) => !Number.isInteger(page) || page < 1 || page > 1000000)
  )
    throw new Error('页码须为 1–1000000 的整数，最多 8 页且不得重复；用逗号或空格分隔。')
  return pages
}
export function videoSelection(
  start: string,
  end: string,
  frames: string,
): NonNullable<ProcessFile['video']> {
  const from = Number(start),
    to = Number(end),
    count = Number(frames)
  if (
    !start.trim() ||
    !end.trim() ||
    !Number.isFinite(from) ||
    !Number.isFinite(to) ||
    from < 0 ||
    to > 86400 ||
    !(to - from > 0 && to - from <= 120)
  )
    throw new Error('请明确填写 0–86400 秒内、长度大于 0 且不超过 120 秒的起止区间。')
  if (!frames.trim() || !Number.isInteger(count) || count < 1 || count > 12)
    throw new Error('请明确填写 1–12 的最大帧数。')
  return { start_seconds: from, end_seconds: to, max_frames: count }
}
export function ProcessControls({
  artifact,
  capabilities,
  busy,
  blocked,
  onProcess,
}: {
  artifact: ArtifactView
  capabilities: ProcessorCapability[]
  busy: boolean
  blocked: boolean
  onProcess: (input: ProcessSettings) => void
}) {
  const id = useId()
  const [operation, setOperation] = useState<Operation | ''>('')
  const [start, setStart] = useState('1'),
    [limit, setLimit] = useState('50')
  const [selectedLanguages, setSelectedLanguages] = useState<Language[]>([])
  const [pages, setPages] = useState(''),
    [dpi, setDpi] = useState('150')
  const [videoStart, setVideoStart] = useState(''),
    [videoEnd, setVideoEnd] = useState(''),
    [frames, setFrames] = useState('')
  const [error, setError] = useState('')
  const [asrLanguage, setAsrLanguage] = useState('auto')
  const relevant = artifact.status === 'available' ? relevantProcessors(artifact, capabilities) : []
  const available = relevant.filter((item) => item.status === 'available')
  const allowed = new Set(available.flatMap((item) => item.operations))
  if (!available.some((item) => item.id === 'asr' && item.operations.includes('transcribe')))
    allowed.delete('transcribe')
  const pdf = artifactFormat(artifact) === 'pdf' || artifact.mime_type === 'application/pdf'
  function readyLanguage(lang: Language) {
    return available.some((item) => item.id === `ocr_${lang}` && item.operations.includes('ocr'))
  }
  function submit() {
    setError('')
    try {
      if (!operation || !allowed.has(operation) || blocked || busy)
        throw new Error('请明确选择本文件可调用的处理操作。')
      let input: ProcessSettings = { operation }
      if (operation === 'extract') {
        const from = Number(start),
          count = Number(limit)
        if (
          !start.trim() ||
          !limit.trim() ||
          !Number.isInteger(from) ||
          from < 1 ||
          from > 1000000 ||
          !Number.isInteger(count) ||
          count < 1 ||
          count > 200
        )
          throw new Error('起始位置须为 1–1000000，范围上限须为 1–200，均为整数。')
        input = { operation, start: from, limit: count }
      } else if (operation === 'ocr') {
        if (!selectedLanguages.length || selectedLanguages.some((lang) => !readyLanguage(lang)))
          throw new Error('请明确选择当前可用的 OCR 语言包。')
        const resolution = Number(dpi)
        if (!dpi.trim() || !Number.isInteger(resolution) || resolution < 150 || resolution > 200)
          throw new Error('OCR DPI 须为 150–200 的整数。')
        input = {
          operation,
          ocr: {
            languages: selectedLanguages,
            pages: pdf ? parseOCRPages(pages) : [],
            dpi: resolution,
          },
        }
      } else if (operation === 'transcribe') {
        const language = asrLanguage.trim()
        if (!/^[a-z]{2,4}$/.test(language))
          throw new Error(
            '请填写 auto 或当前引擎支持的 2–4 位小写语言码；具体支持范围由服务器核对。',
          )
        input = { operation, asr: { language, timestamps: true } }
      } else if (operation === 'video_frames')
        input = { operation, video: videoSelection(videoStart, videoEnd, frames) }
      onProcess(input)
    } catch (error) {
      setError(error instanceof Error ? error.message : '处理范围无效。')
    }
  }
  return (
    <section className="file-processing">
      <h3>明确选择处理操作</h3>
      <fieldset className="file-enhanced-controls" disabled={busy || blocked}>
        <label htmlFor={`${id}-operation`}>
          操作
          <select
            aria-label="操作"
            id={`${id}-operation`}
            value={operation}
            onChange={(e) => {
              setOperation(e.target.value as Operation | '')
              setError('')
            }}
          >
            <option value="">请选择操作</option>
            {(Object.keys(operationLabels) as Operation[]).map((item) => (
              <option key={item} value={item} disabled={!allowed.has(item)}>
                {operationLabels[item]}
                {allowed.has(item) ? '' : ' · 未就绪或不适用'}
              </option>
            ))}
          </select>
        </label>
        {operation === 'extract' && (
          <>
            <div className="file-processing-inputs">
              <label htmlFor={`${id}-start`}>
                起始位置
                <input
                  id={`${id}-start`}
                  type="number"
                  min={1}
                  max={1000000}
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
              <label htmlFor={`${id}-limit`}>
                范围上限
                <input
                  id={`${id}-limit`}
                  type="number"
                  min={1}
                  max={200}
                  value={limit}
                  onChange={(e) => setLimit(e.target.value)}
                />
              </label>
            </div>
            <p className="page-note">
              范围单位：PDF 页、Office 文档块 / 幻灯片 / 每表行、文本行、CSV
              记录或归档条目；媒体提取仅读取元数据。
            </p>
          </>
        )}
        {operation === 'ocr' && (
          <>
            <fieldset className="file-ocr-languages">
              <legend>明确选择 OCR 语言</legend>
              {languages.map((lang) => (
                <label className="checkbox-label" key={lang.id}>
                  <input
                    type="checkbox"
                    disabled={!readyLanguage(lang.id)}
                    checked={selectedLanguages.includes(lang.id)}
                    onChange={(e) =>
                      setSelectedLanguages((old) =>
                        e.target.checked
                          ? [...old, lang.id]
                          : old.filter((item) => item !== lang.id),
                      )
                    }
                  />
                  {lang.label}
                  {readyLanguage(lang.id) ? '' : ' · 当前语言包或格式适配不可用'}
                </label>
              ))}
            </fieldset>
            {pdf ? (
              <label htmlFor={`${id}-pages`}>
                PDF 页码 · 1–8 个不重复页码
                <input
                  id={`${id}-pages`}
                  value={pages}
                  onChange={(e) => setPages(e.target.value)}
                />
                <small>页码从 1 开始，以逗号或空格分隔，不会默认识别整份 PDF。</small>
              </label>
            ) : (
              <p className="page-note">处理明确选中的单帧图像，不传 PDF 页码。</p>
            )}
            <label htmlFor={`${id}-dpi`}>
              DPI（150–200）
              <input
                id={`${id}-dpi`}
                type="number"
                min={150}
                max={200}
                value={dpi}
                onChange={(e) => setDpi(e.target.value)}
              />
            </label>
            <p className="page-note">OCR 文字需要与原件核对，不代表版面、印章或模型视觉理解。</p>
          </>
        )}
        {operation === 'video_frames' && (
          <>
            <div className="file-processing-inputs">
              <label htmlFor={`${id}-video-start`}>
                起始秒数
                <input
                  id={`${id}-video-start`}
                  type="number"
                  min={0}
                  max={86400}
                  step="any"
                  value={videoStart}
                  onChange={(e) => setVideoStart(e.target.value)}
                />
              </label>
              <label htmlFor={`${id}-video-end`}>
                结束秒数
                <input
                  id={`${id}-video-end`}
                  type="number"
                  min={0}
                  max={86400}
                  step="any"
                  value={videoEnd}
                  onChange={(e) => setVideoEnd(e.target.value)}
                />
              </label>
              <label htmlFor={`${id}-frames`}>
                最大帧数（1–12）
                <input
                  id={`${id}-frames`}
                  type="number"
                  min={1}
                  max={12}
                  value={frames}
                  onChange={(e) => setFrames(e.target.value)}
                />
              </label>
            </div>
            <p className="page-note">
              须明确选择最多 120
              秒区间；帧结果保留实际时间戳与来源。不转写音轨，不代表模型原生视频理解或已完成真实样本验证。
            </p>
          </>
        )}
        {operation === 'transcribe' && (
          <>
            <label htmlFor={`${id}-asr-language`}>
              转录语言
              <input
                id={`${id}-asr-language`}
                list={`${id}-asr-languages`}
                pattern="[a-z]{2,4}"
                required
                value={asrLanguage}
                onChange={(event) => setAsrLanguage(event.target.value)}
              />
              <datalist id={`${id}-asr-languages`}>
                <option value="auto">自动识别语言</option>
                <option value="zh">中文</option>
                <option value="en">英文</option>
              </datalist>
              <small>auto 自动识别；也可明确填写引擎支持语言码，服务端会拒绝不支持的值。</small>
            </label>
            <p className="page-note">
              始终保留实际分段时间戳。本地 CPU 语音转录当前仅处理不超过 120
              秒的音频原件；不会自动下载模型，不代表转录准确率已验收，也不代表视频理解。
            </p>
          </>
        )}
        {operation === 'convert_pdf' && (
          <p className="page-note">
            将当前 Office 原件生成独立 PDF 派生件，保留原件。仅支持无宏 DOCX / XLSX / PPTX / ODT /
            ODS / ODP；拒绝嵌入对象与外部关系，版面需核对。
          </p>
        )}
        {operation === 'preview' && (
          <p className="page-note">
            为当前图像生成安全预览，保留原件；预览可能缩小，不代表模型原生支持。
          </p>
        )}
        <Button variant="primary" disabled={!operation || !allowed.has(operation)} onClick={submit}>
          <Play size={14} />
          {busy ? '正在提交…' : '按上述选择创建处理任务'}
        </Button>
      </fieldset>
      {!allowed.size && (
        <p className="page-note">
          本文件当前没有可调用操作。原件仍可下载；组件缺失原因见处理器详情。
        </p>
      )}
      {relevant.map((processor) => (
        <p className="page-note" key={processor.id}>
          {processor.name} ·{' '}
          {processor.status === 'available'
            ? '组件可调用'
            : processor.status === 'not_installed'
              ? '未安装'
              : processor.status === 'not_configured'
                ? '未配置'
                : processor.status}
          ：{processor.limitation}
        </p>
      ))}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </section>
  )
}
