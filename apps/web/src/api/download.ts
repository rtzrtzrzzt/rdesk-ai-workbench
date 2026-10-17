import { ApiError } from './client'

async function readBounded(
  response: Response,
  limit: number,
  limitMessage: string,
): Promise<ArrayBuffer[]> {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('下载响应没有文件内容，请重试。')
  const chunks: ArrayBuffer[] = []
  let bytes = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) return chunks
      bytes += value.byteLength
      if (bytes > limit) {
        await reader.cancel()
        throw new Error(limitMessage)
      }
      chunks.push(Uint8Array.from(value).buffer)
    }
  } finally {
    reader.releaseLock()
  }
}

function errorMessage(body: unknown, status: number) {
  if (body && typeof body === 'object') {
    if ('error' in body && body.error && typeof body.error === 'object') {
      if ('message' in body.error && typeof body.error.message === 'string')
        return body.error.message
    }
    if ('detail' in body && typeof body.detail === 'string') return body.detail
  }
  return status === 401 ? '登录已失效，请重新登录后下载。' : `下载未完成（${status}）。`
}

export async function fetchDownload(
  path: string,
  {
    contentType: expectedType,
    filename,
    maxBytes,
    limitMessage,
    signal,
  }: {
    contentType: string
    filename: string
    maxBytes: number
    limitMessage: string
    signal: AbortSignal
  },
) {
  const response = await fetch(`/api/v1${path}`, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: expectedType },
    signal,
  })
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('workbench:unauthorized'))
    let body: unknown
    try {
      body = JSON.parse(
        await new Blob(await readBounded(response, 64 * 1024, '错误响应过大。')).text(),
      )
    } catch {
      // Error pages are never passed to the browser as downloadable files.
    }
    throw new ApiError(response.status, errorMessage(body, response.status), body)
  }
  const contentType = response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase()
  const disposition = response.headers.get('Content-Disposition')
  const receivedFilename = disposition?.match(/^attachment;\s*filename="([^"]+)"\s*$/i)?.[1]
  if (contentType !== expectedType || receivedFilename !== filename) {
    await response.body?.cancel()
    throw new Error('服务器没有返回预期的下载文件，请稍后重试。')
  }
  const declaredBytes = Number(response.headers.get('Content-Length'))
  if (Number.isFinite(declaredBytes) && declaredBytes > maxBytes) {
    await response.body?.cancel()
    throw new Error(limitMessage)
  }
  const chunks = await readBounded(response, maxBytes, limitMessage)
  if (!chunks.length) throw new Error('下载文件为空，请重试。')
  return { blob: new Blob(chunks, { type: expectedType }), filename }
}
