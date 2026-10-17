import { record, text } from './data'
export type ObservedDownloadLink = { observationId: string; targetRef: string; url: string }
/** href comes only from the native helper's policy-checked canonical link. */
export function observedDownloadLinks(
  observation: Record<string, unknown> | null,
): ObservedDownloadLink[] {
  const observationId = text(observation?.observation_id)
  if (!observationId || !Array.isArray(observation?.elements)) return []
  return observation.elements.flatMap((value) => {
    const element = record(value)
    const targetRef = text(element?.ref),
      url = text(element?.href)
    return targetRef && /^e[0-9]{1,4}$/.test(targetRef) && url
      ? [{ observationId, targetRef, url }]
      : []
  })
}
