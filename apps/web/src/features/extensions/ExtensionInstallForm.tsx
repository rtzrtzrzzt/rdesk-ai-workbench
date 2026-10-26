import { useState } from 'react'
import { FolderOpen } from 'lucide-react'
import { authGeneration } from '../../api/client'
import {
  extensionsApi,
  type ExtensionInspection,
  type ExtensionSource,
  type ExtensionView,
} from '../../api/extensions'
import type { Workspace } from '../../api/types'
import { Button } from '../../components/ui'
import { DirectoryPicker } from '../workspaces/DirectoryPicker'
import { formatBytes } from '../files/FileUpload'
import { packageManifest } from './manifest'
import { PackageContents } from './PackageContents'

export type ReviewedPackage = {
  source: ExtensionSource
  digest: string
  scope: 'user' | 'workspace'
  enabled: boolean
}

export function ExtensionInstallForm({
  workspace,
  initial,
  locked = false,
  onSubmit,
  onCancel,
}: {
  workspace: Workspace
  initial?: ExtensionView
  locked?: boolean
  onSubmit: (reviewed: ReviewedPackage) => void
  onCancel: () => void
}) {
  const [source, setSource] = useState<ExtensionSource>(
    initial?.source ?? { kind: 'directory', location: '', revision: null, subdirectory: '.' },
  )
  const [scope, setScope] = useState<'user' | 'workspace'>(
    initial?.scope === 'user' ? 'user' : 'workspace',
  )
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [inspection, setInspection] = useState<{
    source: ExtensionSource
    result: ExtensionInspection
  } | null>(null)
  const [checking, setChecking] = useState(false)
  const [choosingDirectory, setChoosingDirectory] = useState(false)
  const [error, setError] = useState('')
  const updateSource = (next: ExtensionSource) => {
    setSource(next)
    setInspection(null)
    setError('')
  }
  const disabled = locked || checking
  async function inspect() {
    if (disabled || !source.location.trim() || choosingDirectory) return
    const generation = authGeneration()
    setChecking(true)
    setError('')
    setInspection(null)
    const selected = {
      ...source,
      location: source.location.trim(),
      revision: source.kind === 'git' ? source.revision?.trim() || null : null,
      subdirectory: source.subdirectory?.trim() || '.',
    }
    try {
      const result = await extensionsApi.inspect(workspace.id, selected)
      if (generation !== authGeneration()) return
      setInspection({ source: selected, result })
    } catch (error) {
      if (generation === authGeneration())
        setError(error instanceof Error ? error.message : '检查未完成，来源输入已保留。')
    } finally {
      setChecking(false)
    }
  }
  const manifest = inspection ? packageManifest(inspection.result.manifest) : null
  return (
    <form
      className="stacked-form extension-install-form"
      onSubmit={(event) => {
        event.preventDefault()
        if (disabled || !inspection || !inspection.result.content_digest) return
        onSubmit({
          source: inspection.source,
          digest: inspection.result.content_digest,
          scope,
          enabled,
        })
      }}
    >
      {initial && (
        <p className="page-note">
          更新「{initial.name}」：先检查原来源的新内容，也可更换来源或 Git 版本。
        </p>
      )}
      <label>
        安装来源
        <select
          value={source.kind}
          disabled={disabled}
          onChange={(event) => {
            setChoosingDirectory(false)
            updateSource({
              kind: event.target.value as ExtensionSource['kind'],
              location: '',
              revision: null,
              subdirectory: '.',
            })
          }}
        >
          <option value="directory">本机目录</option>
          <option value="zip">本机 ZIP 文件</option>
          <option value="git">Git 仓库</option>
        </select>
      </label>
      <label>
        {source.kind === 'git'
          ? 'Git 仓库地址（HTTPS）'
          : source.kind === 'zip'
            ? 'ZIP 文件路径'
            : '扩展包文件夹'}
        <input
          required
          type={source.kind === 'git' ? 'url' : 'text'}
          maxLength={4096}
          value={source.location}
          disabled={disabled}
          placeholder={
            source.kind === 'git'
              ? 'https://github.com/组织/仓库.git'
              : source.kind === 'zip'
                ? '运行工作台服务的电脑上的 ZIP 路径'
                : '选择或输入本机文件夹路径'
          }
          onChange={(event) => updateSource({ ...source, location: event.target.value })}
        />
      </label>
      {source.kind === 'directory' && (
        <Button disabled={disabled} onClick={() => setChoosingDirectory(!choosingDirectory)}>
          <FolderOpen size={15} />
          {choosingDirectory ? '收起文件夹' : '选择文件夹'}
        </Button>
      )}
      {choosingDirectory && source.kind === 'directory' && (
        <DirectoryPicker
          initialPath={source.location || workspace.root_path}
          onSelect={(location) => {
            updateSource({ ...source, location })
            setChoosingDirectory(false)
          }}
          onCancel={() => setChoosingDirectory(false)}
        />
      )}
      {source.kind === 'zip' && (
        <p className="page-note">
          填写运行工作台服务的这台电脑上的 ZIP 文件路径；此处不会上传浏览器文件。
        </p>
      )}
      {source.kind === 'git' && (
        <>
          <label>
            Git 版本（可选）
            <input
              value={source.revision ?? ''}
              maxLength={200}
              disabled={disabled}
              placeholder="分支、标签或 commit；留空使用默认版本"
              onChange={(event) => updateSource({ ...source, revision: event.target.value })}
            />
          </label>
          <p className="page-note">使用不含凭据的 HTTPS 地址；安装会记录实际 commit。</p>
        </>
      )}
      <details className="settings-editor" open>
        <summary>包在来源中的子目录（可选）</summary>
        <div className="settings-editor-content">
          <label>
            相对子目录
            <input
              value={source.subdirectory ?? '.'}
              maxLength={1000}
              disabled={disabled}
              placeholder=". 或 skills/my-skill"
              onChange={(event) => updateSource({ ...source, subdirectory: event.target.value })}
            />
          </label>
        </div>
      </details>
      {!initial && (
        <>
          <label>
            作用范围
            <select
              value={scope}
              disabled={disabled}
              onChange={(event) => setScope(event.target.value as 'user' | 'workspace')}
            >
              <option value="workspace">当前工作区 · {workspace.name}</option>
              <option value="user">全局 · 我的所有工作区</option>
            </select>
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={enabled}
              disabled={disabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            <span>安装后启用 Skill，供新任务使用</span>
          </label>
        </>
      )}
      <p className="page-note">
        检查和安装只读取、复制包文件，不执行 hooks、脚本或依赖安装器；MCP 需要单独配置。
      </p>
      <div className="button-row">
        <Button
          disabled={disabled || !source.location.trim() || choosingDirectory}
          onClick={() => void inspect()}
        >
          {checking ? '正在检查…' : inspection ? '重新检查包内容' : '检查包内容'}
        </Button>
      </div>
      {checking && (
        <p className="page-note" role="status">
          正在读取来源并检查包内容；Git 仓库可能需要几十秒，请稍候。
        </p>
      )}
      {inspection && manifest && (
        <section className="extension-inspection" aria-label="检查结果">
          <h3>{manifest.name}</h3>
          {manifest.description && <p>{manifest.description}</p>}
          <p className="page-note">
            {manifest.version ? `版本 ${manifest.version}` : '未声明版本'} ·{' '}
            {inspection.result.file_count} 个文件 · {formatBytes(inspection.result.total_bytes)}
          </p>
          <PackageContents manifest={inspection.result.manifest} />
          {inspection.result.source_revision && (
            <p className="extension-source-revision">
              实际 commit：{inspection.result.source_revision}
            </p>
          )}
        </section>
      )}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="button-row">
        <Button disabled={disabled} onClick={onCancel}>
          取消
        </Button>
        <Button
          type="submit"
          variant="primary"
          disabled={disabled || !inspection?.result.content_digest}
        >
          {locked ? '正在处理…' : initial ? '安装检查后的更新' : '安装检查后的扩展'}
        </Button>
      </div>
    </form>
  )
}
