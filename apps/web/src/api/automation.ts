import { request } from './client'
import type { components } from './schema'

export type AutomationPolicy = components['schemas']['PolicyView']
export type AutomationConfiguration = components['schemas']['AutomationConfiguration']
export type AutomationSave = components['schemas']['AutomationSave']
export type AutomationCapabilities = components['schemas']['AutomationCapabilities']
export type AutomationSession = components['schemas']['SessionView']
export type AutomationSessionCreate = components['schemas']['SessionCreate']
export type AutomationAction = components['schemas']['ActionView']
export type BrowserAction = components['schemas']['BrowserAction']
export type BrowserDownload = components['schemas']['BrowserDownload']
export type DesktopAction = components['schemas']['DesktopAction']
export type ComputerSettings = components['schemas']['ComputerSettingsView']
type ComputerSettingsSave = components['schemas']['ComputerSettingsSave']
type RuntimeView = components['schemas']['RuntimeView']
const root = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/automation`
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
export const automationApi = {
  settings: () => request<ComputerSettings>('/automation/settings'),
  saveSettings: (input: ComputerSettingsSave) =>
    request<ComputerSettings>('/automation/settings', post(input)),
  policy: (wid: string) => request<AutomationPolicy>(`${root(wid)}/policy`),
  save: (wid: string, input: AutomationSave) =>
    request<AutomationPolicy>(`${root(wid)}/policy`, post(input)),
  capabilities: (wid: string) => request<AutomationCapabilities>(`${root(wid)}/capabilities`),
  runtime: (wid: string, action: 'start' | 'stop', requestId: string) =>
    request<RuntimeView>(`${root(wid)}/runtime/${action}`, post({ request_id: requestId })),
  sessions: (wid: string) => request<AutomationSession[]>(`${root(wid)}/sessions`),
  create: (wid: string, input: AutomationSessionCreate) =>
    request<AutomationSession>(`${root(wid)}/sessions`, post(input)),
  observe: (wid: string, sid: string, screenshot: boolean, requestId: string) =>
    request<AutomationAction>(
      `${root(wid)}/sessions/${encodeURIComponent(sid)}/observe`,
      post({ screenshot, request_id: requestId }),
    ),
  browser: (wid: string, sid: string, action: BrowserAction) =>
    request<AutomationAction>(
      `${root(wid)}/sessions/${encodeURIComponent(sid)}/browser-action`,
      post({ action }),
    ),
  download: (wid: string, sid: string, input: BrowserDownload) =>
    request<AutomationAction>(
      `${root(wid)}/sessions/${encodeURIComponent(sid)}/download`,
      post(input),
    ),
  desktop: (wid: string, sid: string, action: DesktopAction) =>
    request<AutomationAction>(
      `${root(wid)}/sessions/${encodeURIComponent(sid)}/desktop-action`,
      post({ action }),
    ),
  stop: (wid: string, sid: string, action: 'stop' | 'takeover', requestId: string) =>
    request<AutomationSession>(
      `${root(wid)}/sessions/${encodeURIComponent(sid)}/${action}`,
      post({ request_id: requestId }),
    ),
  action: (wid: string, id: string) =>
    request<AutomationAction>(`${root(wid)}/actions/${encodeURIComponent(id)}`),
}
