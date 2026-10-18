import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowUp, Folder } from 'lucide-react'
import { workspacesApi } from '../../api/workspaces'
import { Button } from '../../components/ui'
import './workspaces.css'

export function DirectoryPicker({
  initialPath,
  onSelect,
  onCancel,
}: {
  initialPath?: string
  onSelect: (path: string) => void
  onCancel: () => void
}) {
  const [path, setPath] = useState(initialPath)
  const [showHidden, setShowHidden] = useState(false)
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined])
  const after = cursors.at(-1)
  const listing = useQuery({
    queryKey: ['workspace-directories', path, showHidden, after],
    queryFn: ({ signal }) => workspacesApi.directories(path, showHidden, after, signal),
    retry: false,
    staleTime: 0,
  })
  function open(next: string) {
    setPath(next)
    setCursors([undefined])
  }
  return (
    <section className="directory-picker" aria-label="选择项目文件夹">
      <div className="directory-picker-places">
        {(listing.data?.places ?? []).map((place) => (
          <Button key={place.path} onClick={() => open(place.path)}>
            {place.name}
          </Button>
        ))}
      </div>
      <div className="directory-picker-location">
        <Button
          disabled={!listing.data?.parent_path}
          onClick={() => listing.data?.parent_path && open(listing.data.parent_path)}
        >
          <ArrowUp size={14} /> 上一级
        </Button>
        <span aria-label="当前文件夹">{listing.data?.path ?? path ?? '主目录'}</span>
      </div>
      <label className="directory-picker-hidden">
        <input
          type="checkbox"
          checked={showHidden}
          onChange={(event) => {
            setShowHidden(event.target.checked)
            setCursors([undefined])
          }}
        />
        显示隐藏文件夹
      </label>
      {listing.isPending ? (
        <p className="page-note" role="status">
          正在读取文件夹…
        </p>
      ) : listing.error ? (
        <div role="alert" className="form-error">
          <p>{listing.error.message}</p>
          <div className="button-row">
            <Button onClick={() => void listing.refetch()}>重试</Button>
            <Button
              onClick={() => {
                setPath(undefined)
                setCursors([undefined])
              }}
            >
              返回主目录
            </Button>
          </div>
        </div>
      ) : (
        <div className="directory-picker-list" aria-label="子文件夹">
          {listing.data.entries.length ? (
            listing.data.entries.map((directory) => (
              <button
                key={directory.path}
                type="button"
                onClick={() => open(directory.path)}
                aria-label={`打开文件夹：${directory.name}`}
              >
                <Folder size={17} aria-hidden="true" />
                <span>{directory.name}</span>
              </button>
            ))
          ) : (
            <p className="page-note">没有可显示的子文件夹，可选择当前文件夹。</p>
          )}
        </div>
      )}
      {(cursors.length > 1 || listing.data?.next_cursor) && (
        <div className="button-row">
          <Button
            disabled={cursors.length === 1}
            onClick={() => setCursors((old) => old.slice(0, -1))}
          >
            上一页
          </Button>
          <Button
            disabled={!listing.data?.next_cursor}
            onClick={() => {
              if (listing.data?.next_cursor)
                setCursors((old) => [...old, listing.data.next_cursor!])
            }}
          >
            下一页
          </Button>
        </div>
      )}
      <div className="button-row">
        <Button onClick={onCancel}>取消选择</Button>
        <Button
          variant="primary"
          disabled={!listing.data || listing.isFetching || listing.isError}
          onClick={() => listing.data && onSelect(listing.data.path)}
        >
          选择当前文件夹
        </Button>
      </div>
    </section>
  )
}
