/** All product HTTP requests pass through this module. Endpoint methods live beside the confirmed API contract. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}
let csrfToken: string | null = null
let identityGeneration = 0
export const authGeneration = () => identityGeneration
export function advanceAuthGeneration() {
  return ++identityGeneration
}
export function setCsrfToken(value: string | null) {
  csrfToken = value
}
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const generation = identityGeneration
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    credentials: 'same-origin',
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...(csrfToken && options.method && options.method !== 'GET'
        ? { 'X-CSRF-Token': csrfToken }
        : {}),
      ...options.headers,
    },
  })
  const body: unknown =
    response.status === 204 ? undefined : await response.json().catch(() => undefined)
  if (generation !== identityGeneration)
    throw new DOMException('会话已改变，忽略旧会话响应。', 'AbortError')
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/session' && path !== '/auth/bootstrap')
      window.dispatchEvent(new Event('workbench:unauthorized'))
    const error = body as { error?: { message?: string }; detail?: string } | undefined
    const message =
      error?.error?.message ??
      (typeof error?.detail === 'string' ? error.detail : `请求未完成（${response.status}）`)
    throw new ApiError(response.status, message, body)
  }
  return body as T
}
