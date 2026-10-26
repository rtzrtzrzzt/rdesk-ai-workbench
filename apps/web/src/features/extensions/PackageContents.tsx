import type { ExtensionView } from '../../api/extensions'
import { Button } from '../../components/ui'
import { packageManifest } from './manifest'

export function PackageContents({
  manifest,
  installation,
  disabled = false,
  onImportMcp,
}: {
  manifest: unknown
  installation?: ExtensionView
  disabled?: boolean
  onImportMcp?: (server: string) => void
}) {
  const details = packageManifest(manifest)
  return (
    <div className="extension-package-contents">
      <p className="page-note">
        {details.format || '未提供格式'} · {details.skills.length} 个 Skill ·{' '}
        {details.servers.length} 个 MCP 声明
        {details.license && ` · ${details.license}`}
      </p>
      {!!details.skills.length && (
        <ul className="extension-components">
          {details.skills.map((skill) => (
            <li key={skill.name}>
              <strong>{skill.name}</strong>
              {skill.description && <p>{skill.description}</p>}
            </li>
          ))}
        </ul>
      )}
      {!!details.servers.length && (
        <div>
          <h4>MCP 声明</h4>
          <ul className="extension-components">
            {details.servers.map((server) => (
              <li key={server.name}>
                <div className="extension-component-heading">
                  <strong>
                    {server.name}
                    {server.transport && ` · ${server.transport}`}
                  </strong>
                  {installation && onImportMcp && (
                    <Button
                      disabled={disabled || !installation.effective_enabled || !server.importable}
                      onClick={() => onImportMcp(server.name)}
                    >
                      导入禁用连接
                    </Button>
                  )}
                </div>
                <p>
                  {server.reason ||
                    (server.importable
                      ? '可导入为禁用连接，随后在「工具与扩展」中配置和启用。'
                      : '此声明需要在「工具与扩展」中手动配置。')}
                </p>
              </li>
            ))}
          </ul>
          {installation && !installation.effective_enabled && (
            <p className="page-note">扩展在当前工作区启用后，才可导入其 MCP 声明。</p>
          )}
        </div>
      )}
      {!!details.diagnostics.length && (
        <div className="extension-diagnostics" role="status">
          <h4>兼容性说明</h4>
          <ul>
            {details.diagnostics.map((diagnostic, index) => (
              <li key={index}>{diagnostic}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
