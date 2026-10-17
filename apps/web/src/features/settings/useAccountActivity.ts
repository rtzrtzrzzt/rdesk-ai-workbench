import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { accountApi } from '../../api/account'

export const ACTIVITY_INTERVAL_MS = 60_000
export function shouldRecordActivity(
  trusted: boolean,
  visible: boolean,
  now: number,
  lastSent: number,
) {
  return trusted && visible && now - lastSent >= ACTIVITY_INTERVAL_MS
}
/** No timer or background traffic can renew an idle session. */
export function useAccountActivity(userId: string | undefined) {
  const client = useQueryClient()
  const previous = useRef<{ userId: string; sentAt: number } | null>(null)
  useEffect(() => {
    if (!userId) {
      previous.current = null
      return
    }
    const onActivity = (event: Event) => {
      const now = performance.now()
      const last = previous.current?.userId === userId ? previous.current.sentAt : -Infinity
      if (!shouldRecordActivity(event.isTrusted, document.visibilityState === 'visible', now, last))
        return
      // Throttle attempts too, including offline failures; never queue an automatic retry.
      previous.current = { userId, sentAt: now }
      void accountApi
        .activity()
        .then(() => client.invalidateQueries({ queryKey: ['account-security', userId] }))
        .catch(() => undefined)
    }
    window.addEventListener('pointerdown', onActivity, { passive: true, capture: true })
    window.addEventListener('keydown', onActivity, { capture: true })
    return () => {
      window.removeEventListener('pointerdown', onActivity, { capture: true })
      window.removeEventListener('keydown', onActivity, { capture: true })
    }
  }, [client, userId])
}
