import { useQuery } from '@tanstack/react-query'
import { agentServicesApi } from '../../api/agentServices'
import { api } from '../../api/workbench'
import { Button } from '../../components/ui'
import { ServicePublisher } from './ServicePublisher'
import { ApiClients } from './ApiClients'
import { ApiListener } from './ApiListener'
import './apiServices.css'

export function ApiServicesPanel({
  active,
  section = 'all',
}: {
  active: boolean
  section?: 'services' | 'clients' | 'listener' | 'all'
}) {
  const showServices = section === 'all' || section === 'services'
  const showClients = section === 'all' || section === 'clients'
  const showListener = section === 'all' || section === 'listener'
  const services = useQuery({
    queryKey: ['agent-services'],
    queryFn: ({ signal }) => agentServicesApi.list(signal),
    enabled: active && (showServices || showClients),
  })
  const capabilities = useQuery({
    queryKey: ['agent-service-capabilities'],
    queryFn: ({ signal }) => agentServicesApi.capabilities(signal),
    enabled: active && showServices,
  })
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: api.workspaces,
    enabled:
      active && showServices && capabilities.data?.source_sharing?.mode === 'revision_snapshot',
  })
  const catalogError =
    ((showServices || showClients) && services.isError) || (showServices && capabilities.isError)
  return (
    <div className="api-services-panel">
      {section === 'all' && (
        <p className="page-note">
          将选定的 Agent 能力提供给外部应用。分别配置服务、客户端权限和独立 API 监听地址。
        </p>
      )}
      {catalogError && (
        <div role="alert">
          <p className="form-error">
            {showServices ? 'Agent 服务或能力目录读取失败。' : 'Agent 服务列表读取失败。'}
          </p>
          <Button
            onClick={() => {
              void services.refetch()
              if (showServices) void capabilities.refetch()
            }}
          >
            重新读取服务
          </Button>
        </div>
      )}
      {showServices && (
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
      )}
      {showServices && workspaces.isError && (
        <div role="alert">
          <p className="form-error">来源工作区列表读取失败，暂时无法选择共享资料。</p>
          <Button onClick={() => void workspaces.refetch()}>重新读取来源工作区</Button>
        </div>
      )}
      {showClients && <ApiClients active={active} services={services.data ?? []} />}
      {showListener && <ApiListener active={active} />}
    </div>
  )
}
