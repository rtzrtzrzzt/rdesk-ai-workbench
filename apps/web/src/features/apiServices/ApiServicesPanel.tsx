import { useQuery } from '@tanstack/react-query'
import { agentServicesApi } from '../../api/agentServices'
import { api } from '../../api/workbench'
import { Button } from '../../components/ui'
import { ServicePublisher } from './ServicePublisher'
import { ApiClients } from './ApiClients'
import { ApiListener } from './ApiListener'
import './apiServices.css'

export function ApiServicesPanel({ active }: { active: boolean }) {
  const services = useQuery({
    queryKey: ['agent-services'],
    queryFn: ({ signal }) => agentServicesApi.list(signal),
    enabled: active,
  })
  const capabilities = useQuery({
    queryKey: ['agent-service-capabilities'],
    queryFn: ({ signal }) => agentServicesApi.capabilities(signal),
    enabled: active,
  })
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: api.workspaces,
    enabled: active && capabilities.data?.source_sharing?.mode === 'revision_snapshot',
  })
  return (
    <div className="api-services-panel">
      <p className="page-note">
        将选定的 Agent 能力提供给外部应用。分别配置服务、客户端权限和独立 API 监听地址。
      </p>
      {(services.isError || capabilities.isError) && (
        <div role="alert">
          <p className="form-error">Agent 服务或能力目录读取失败。</p>
          <Button
            onClick={() => {
              void services.refetch()
              void capabilities.refetch()
            }}
          >
            重新读取服务
          </Button>
        </div>
      )}
      <ServicePublisher
        services={services.data ?? []}
        capabilities={capabilities.data}
        workspaces={(workspaces.data ?? []).filter(
          (workspace) => !workspace.archived && !workspace.removed_at,
        )}
        sourcesLoading={workspaces.isPending}
        loading={services.isPending || capabilities.isPending}
        onRefresh={async () => {
          await services.refetch()
        }}
      />
      {workspaces.isError && (
        <div role="alert">
          <p className="form-error">来源工作区列表读取失败，暂时无法选择共享资料。</p>
          <Button onClick={() => void workspaces.refetch()}>重新读取来源工作区</Button>
        </div>
      )}
      <ApiClients active={active} services={services.data ?? []} />
      <ApiListener active={active} />
    </div>
  )
}
