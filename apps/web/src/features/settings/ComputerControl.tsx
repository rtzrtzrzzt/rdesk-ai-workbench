import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { automationApi } from '../../api/automation'
import { Button } from '../../components/ui'
import { useSettingsAttention } from './SettingsGroup'

export function ComputerControl() {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['automation-settings'], queryFn: automationApi.settings })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useSettingsAttention('computer-control', !!error || query.isError, '电脑操作设置需要核对')
  async function change() {
    if (!query.data || saving || error) return
    setSaving(true)
    try {
      const updated = await automationApi.saveSettings({
        enabled: !query.data.enabled,
        expected_version: query.data.version,
        request_id: crypto.randomUUID(),
      })
      client.setQueryData(['automation-settings'], updated)
      await client.invalidateQueries({ queryKey: ['automation-settings'] })
      await client.invalidateQueries({ queryKey: ['automation-sessions'] })
      await client.invalidateQueries({ queryKey: ['automation-policy'] })
    } catch (error) {
      setError(error instanceof Error ? error.message : '保存结果需要核对。')
    } finally {
      setSaving(false)
    }
  }
  return (
    <section className="settings-preference computer-control-setting">
      <div>
        <h3>电脑操作</h3>
        <p>允许 AI 操作浏览器和桌面，操作由智能审核自动决定。关闭后停止现有会话并阻止新操作。</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-label="电脑操作"
        aria-checked={query.data?.enabled ?? false}
        className="settings-switch"
        disabled={!query.data || query.isError || saving || !!error}
        onClick={() => void change()}
      >
        <span />
      </button>
      {(query.isError || error) && (
        <div className="settings-preference-error" role="alert">
          <p>{error || '电脑操作设置读取失败。'}</p>
          <Button
            disabled={saving || query.isFetching}
            onClick={() => {
              void query.refetch().then((result) => {
                if (!result.error) setError('')
              })
            }}
          >
            重新读取
          </Button>
        </div>
      )}
    </section>
  )
}
