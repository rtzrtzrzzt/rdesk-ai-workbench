import { request } from './client'
import type { CommandView, CommandOutput } from './types'
export type CommandStream = CommandOutput['stream']
export const COMMAND_PAGE_BYTES = 8000
const root = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/commands`
const command = (workspaceId: string, id: string) =>
  `${root(workspaceId)}/${encodeURIComponent(id)}`
export const commandsApi = {
  list: (workspaceId: string) => request<CommandView[]>(root(workspaceId)),
  capability: (workspaceId: string) => request<unknown>(`${root(workspaceId)}/capability`),
  inspect: (workspaceId: string, id: string) => request<CommandView>(command(workspaceId, id)),
  output: (workspaceId: string, id: string, stream: CommandStream, offset: number) =>
    request<CommandOutput>(
      `${command(workspaceId, id)}/output?${new URLSearchParams({ stream, offset: String(offset), limit: String(COMMAND_PAGE_BYTES) })}`,
    ),
  stop: (workspaceId: string, id: string) =>
    request<CommandView>(`${command(workspaceId, id)}/stop`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
}
