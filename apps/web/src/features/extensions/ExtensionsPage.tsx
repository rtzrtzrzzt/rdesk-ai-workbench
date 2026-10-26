import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { PackagePlus, RefreshCw } from 'lucide-react'
import { ApiError, authGeneration } from '../../api/client'
import { extensionsApi, type ExtensionList, type ExtensionView } from '../../api/extensions'
import { closePendingRequest } from '../../api/requests'
import type { Workspace } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, Status } from '../../components/ui'
import { useSettingsAttention } from '../settings/SettingsGroup'
import { ExtensionInstallForm, type ReviewedPackage } from './ExtensionInstallForm'
import { PackageContents } from './PackageContents'
import { packageManifest, record } from './manifest'
import { operationLabels, pendingKey, readPending, type PendingOperation } from './pending'
import './extensions.css'

type Editor = { kind: 'install' } | { kind: 'upgrade'; installation: ExtensionView }

export function ExtensionsPage({
  workspace,
  userId,
  active,
}: {
  workspace: Workspace
  userId: string
  active: boolean
}) {
  const client = useQueryClient()
  const key = pendingKey(userId, workspace.id)
  const [restored] = useState(() => readPending(key))
  const [pending, setPending] = useState<PendingOperation | null>(restored.pending)
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [message, setMessage] = useState('')
  const [failure, setFailure] = useState(false)
  const queryKey = ['extensions', userId, workspace.id]
  const listing = useQuery({
    queryKey,
    queryFn: ({ signal }) => extensionsApi.list(workspace.id, signal),
    enabled: active,
    retry: false,
  })
  const locked = busy || !!pending || !!restored.error
  useSettingsAttention(
    'extensions-pending',
    !!pending || !!restored.error,
    '插件与 Skill 有请求需要核对。',
  )

  function store(command: PendingOperation | null) {
    if (command) localStorage.setItem(key, JSON.stringify(command))
    else localStorage.removeItem(key)
    setPending(command)
  }
  function updateInstallation(result: ExtensionView) {
    client.setQueryData<ExtensionList>(queryKey, (previous) => ({
      installations: [
        ...(result.status === 'installed' ? [result] : []),
        ...(previous?.installations ?? []).filter((row) => row.id !== result.id),
      ],
    }))
  }
  async function completed(command: PendingOperation) {
    store(null)
    if (command.kind === 'install' || command.kind === 'upgrade') setEditor(null)
    setFailure(false)
    setMessage(
      command.kind === 'mcp'
        ? 'MCP 声明已导入为禁用连接，未启动程序或连接服务。请到「工具与扩展」配置、授权和启用。'
        : command.kind === 'remove'
          ? '已卸载；原始来源文件未删除，历史任务引用的受管版本仍保留。'
          : command.kind === 'upgrade'
            ? '更新已保存，新任务将使用新版本。'
            : command.kind === 'install'
              ? '扩展已安装。'
              : '启用状态已更新。',
    )
    await client.invalidateQueries({ queryKey: ['extensions'] })
    if (command.kind === 'mcp')
      await client.invalidateQueries({ queryKey: ['mcp-connections', workspace.id] })
  }
  async function submit(command: PendingOperation, original = false) {
    if (inFlight.current || restored.error || (!original && pending)) return
    const generation = authGeneration()
    try {
      store(command)
    } catch {
      setFailure(true)
      setMessage('无法保留本次请求标识，尚未发送。请检查浏览器存储后重试。')
      return
    }
    inFlight.current = true
    setBusy(true)
    setFailure(false)
    setMessage('')
    try {
      switch (command.kind) {
        case 'install':
          updateInstallation(await extensionsApi.install(workspace.id, command.input))
          break
        case 'upgrade':
          updateInstallation(await extensionsApi.upgrade(workspace.id, command.id, command.input))
          break
        case 'change':
          updateInstallation(await extensionsApi.change(workspace.id, command.id, command.input))
          break
        case 'remove':
          updateInstallation(await extensionsApi.remove(workspace.id, command.id, command.input))
          break
        case 'mcp':
          await extensionsApi.importMcp(workspace.id, command.id, command.input)
          break
      }
      if (generation !== authGeneration()) return
      await completed(command)
    } catch (error) {
      if (generation !== authGeneration()) return
      const uncertain = !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      let retainRequest = uncertain || original
      if (!retainRequest) {
        try {
          store(null)
        } catch {
          retainRequest = true
        }
      }
      setFailure(true)
      setMessage(
        `${error instanceof Error ? error.message : '操作未完成。'}${
          retainRequest
            ? ' 原参数与请求标识已保留，请先核对结果。'
            : error instanceof ApiError && error.status === 409
              ? ' 输入已保留；请重新检查包内容。若配置已变化，请关闭编辑窗口并刷新列表后重新操作。'
              : ''
        }`,
      )
      if (error instanceof ApiError && error.status === 409)
        void client.invalidateQueries({ queryKey: ['extensions'] })
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  async function readReceipt(command: PendingOperation) {
    const receipt = record(await extensionsApi.receipt(command.input.request_id))
    const valid =
      command.kind === 'mcp'
        ? receipt && typeof receipt.id === 'string' && record(receipt.configuration)
        : receipt &&
          typeof receipt.id === 'string' &&
          typeof receipt.revision_id === 'string' &&
          typeof receipt.version === 'number' &&
          (command.kind === 'install' || receipt.id === command.id)
    if (!valid) throw new Error('回执与本次扩展操作不匹配，待核对记录已保留。')
    await completed(command)
  }
  async function reconcile(close = false) {
    if (!pending || inFlight.current) return
    const command = pending
    const generation = authGeneration()
    inFlight.current = true
    setBusy(true)
    setMessage('')
    setFailure(false)
    try {
      if (close) {
        const result = await closePendingRequest(command.input.request_id)
        if (generation !== authGeneration()) return
        if (result.status === 'sealed') {
          store(null)
          setMessage('已结束本次待核对请求，原标识不能再次提交。请查看实际列表后再操作。')
          await client.invalidateQueries({ queryKey: ['extensions'] })
          return
        }
      }
      await readReceipt(command)
    } catch (error) {
      if (generation !== authGeneration()) return
      setFailure(true)
      setMessage(
        error instanceof ApiError && error.status === 404
          ? '尚未查到回执，请求可能仍在处理。可稍后核对，或使用原参数和标识重试。'
          : error instanceof Error
            ? error.message
            : '核对未完成，原请求仍保留。',
      )
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  function installReviewed(reviewed: ReviewedPackage) {
    if (!editor || locked) return
    const request_id = crypto.randomUUID()
    void submit(
      editor.kind === 'upgrade'
        ? {
            kind: 'upgrade',
            id: editor.installation.id,
            input: {
              source: reviewed.source,
              expected_content_digest: reviewed.digest,
              expected_version: editor.installation.version,
              request_id,
            },
          }
        : {
            kind: 'install',
            input: {
              source: reviewed.source,
              expected_content_digest: reviewed.digest,
              scope: reviewed.scope,
              enabled: reviewed.enabled,
              request_id,
            },
          },
    )
  }
  const status = (
    <>
      {restored.error && (
        <p className="form-error" role="alert">
          {restored.error}
        </p>
      )}
      {pending && (
        <section className="extension-pending" aria-label="待核对扩展操作">
          <p role="status">
            {busy
              ? `正在${operationLabels[pending.kind]}，来源读取可能需要几十秒，请稍候…`
              : `上次${operationLabels[pending.kind]}的结果待核对。原参数与标识已保留。`}
          </p>
          <p className="extension-request-id">请求标识：{pending.input.request_id}</p>
          <div className="button-row">
            <Button disabled={busy} onClick={() => void reconcile()}>
              核对本次操作
            </Button>
            <Button disabled={busy} onClick={() => void submit(pending, true)}>
              使用原请求重试
            </Button>
            <Button disabled={busy} onClick={() => void reconcile(true)}>
              结束核对并解锁编辑
            </Button>
          </div>
        </section>
      )}
      {message && (
        <p className={failure ? 'form-error' : 'page-note'} role={failure ? 'alert' : 'status'}>
          {message}
        </p>
      )}
    </>
  )
  return (
    <section className="extensions-page">
      <div className="extension-page-actions button-row">
        <Button disabled={listing.isFetching} onClick={() => void listing.refetch()}>
          <RefreshCw size={15} />
          刷新列表
        </Button>
        <Button
          variant="primary"
          disabled={locked}
          onClick={() => {
            setMessage('')
            setEditor({ kind: 'install' })
          }}
        >
          <PackagePlus size={15} />
          安装插件或 Skill
        </Button>
      </div>
      <p className="page-note">
        安装本机目录、ZIP 或 Git 中的插件与
        Skill。运行中的任务固定原版本；更新用于新任务，停用或卸载会立即撤销后续使用权限。
      </p>
      {!editor && status}
      {listing.error && (
        <p className="form-error" role="alert">
          {listing.error.message} 已有列表保留，可点击刷新重试。
        </p>
      )}
      {listing.isPending ? (
        <p className="page-note" role="status">
          正在读取已安装扩展…
        </p>
      ) : listing.data?.installations.length ? (
        <div className="extension-installations">
          {listing.data.installations.map((installation) => {
            const manifest = packageManifest(installation.manifest)
            return (
              <article key={installation.id} className="extension-installation">
                <div className="extension-installation-heading">
                  <div>
                    <h3>{installation.name}</h3>
                    <p className="page-note">
                      {installation.scope === 'user' ? '全局' : `当前工作区 · ${workspace.name}`} ·{' '}
                      {manifest.version ? `版本 ${manifest.version}` : '未声明版本'}
                    </p>
                  </div>
                  <Status tone={installation.effective_enabled ? 'good' : 'neutral'}>
                    {installation.effective_enabled
                      ? '已启用'
                      : !installation.enabled
                        ? '已停用'
                        : '当前工作区停用'}
                  </Status>
                </div>
                {manifest.description && (
                  <p className="extension-description">{manifest.description}</p>
                )}
                <div className="extension-installation-actions">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      disabled={locked}
                      checked={installation.enabled}
                      onChange={(event) =>
                        void submit({
                          kind: 'change',
                          id: installation.id,
                          input: {
                            scope: 'installation',
                            enabled: event.target.checked,
                            expected_version: installation.version,
                            request_id: crypto.randomUUID(),
                          },
                        })
                      }
                    />
                    <span>{installation.scope === 'user' ? '全局启用' : '启用'}</span>
                  </label>
                  {installation.scope === 'user' && (
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        disabled={locked || !installation.enabled}
                        checked={installation.enabled && installation.workspace_override !== false}
                        onChange={(event) =>
                          void submit({
                            kind: 'change',
                            id: installation.id,
                            input: {
                              scope: 'workspace',
                              enabled: event.target.checked,
                              expected_version: installation.version,
                              request_id: crypto.randomUUID(),
                            },
                          })
                        }
                      />
                      <span>当前工作区使用</span>
                    </label>
                  )}
                  <div className="button-row">
                    <Button
                      disabled={locked}
                      onClick={() => {
                        setMessage('')
                        setEditor({ kind: 'upgrade', installation })
                      }}
                    >
                      检查更新
                    </Button>
                    <Button
                      variant="danger"
                      disabled={locked}
                      onClick={() =>
                        void submit({
                          kind: 'remove',
                          id: installation.id,
                          input: {
                            expected_version: installation.version,
                            request_id: crypto.randomUUID(),
                          },
                        })
                      }
                    >
                      卸载
                    </Button>
                  </div>
                </div>
                <details className="extension-details">
                  <summary>Skill、MCP 与来源详情</summary>
                  <PackageContents
                    manifest={installation.manifest}
                    installation={installation}
                    disabled={locked}
                    onImportMcp={(server_name) =>
                      void submit({
                        kind: 'mcp',
                        id: installation.id,
                        input: {
                          server_name,
                          expected_version: installation.version,
                          request_id: crypto.randomUUID(),
                        },
                      })
                    }
                  />
                  <dl className="extension-source-details">
                    <dt>来源</dt>
                    <dd>
                      {installation.source.kind} · {installation.source.location}
                    </dd>
                    {installation.source.revision && (
                      <>
                        <dt>Git 版本</dt>
                        <dd>{installation.source.revision}</dd>
                      </>
                    )}
                    {installation.source_revision && (
                      <>
                        <dt>实际 commit</dt>
                        <dd>{installation.source_revision}</dd>
                      </>
                    )}
                    {installation.source.subdirectory &&
                      installation.source.subdirectory !== '.' && (
                        <>
                          <dt>子目录</dt>
                          <dd>{installation.source.subdirectory}</dd>
                        </>
                      )}
                    <dt>受管版本</dt>
                    <dd>{installation.revision_id}</dd>
                  </dl>
                </details>
              </article>
            )
          })}
        </div>
      ) : (
        !listing.error && (
          <div className="inline-empty">
            <h3>尚未安装插件或 Skill</h3>
            <p>先检查包内容，再选择当前工作区或全局安装。</p>
          </div>
        )
      )}
      {editor && (
        <Dialog
          title={editor.kind === 'upgrade' ? '检查扩展更新' : '安装插件或 Skill'}
          onClose={() => {
            if (!busy && !pending) setEditor(null)
          }}
        >
          {status}
          <ExtensionInstallForm
            workspace={workspace}
            initial={editor.kind === 'upgrade' ? editor.installation : undefined}
            locked={locked}
            onSubmit={installReviewed}
            onCancel={() => setEditor(null)}
          />
        </Dialog>
      )}
    </section>
  )
}
