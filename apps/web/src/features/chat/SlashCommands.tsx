import type { LucideIcon } from 'lucide-react'

export type ComposerCommand = {
  name: string
  aliases: string[]
  description: string
  icon: LucideIcon
  disabledReason?: string
  run: () => void
}

export function SlashCommands({
  id,
  commands,
  selected,
  onSelect,
  onHighlight,
}: {
  id: string
  commands: ComposerCommand[]
  selected: number
  onSelect: (command: ComposerCommand) => void
  onHighlight: (index: number) => void
}) {
  return (
    <div className="slash-commands">
      <div className="slash-commands-caption">
        快捷命令 <span>↑ ↓ 选择 · Enter 执行 · Esc 关闭</span>
      </div>
      <div id={id} role="listbox" aria-label="快捷命令">
        {commands.map((command, index) => (
          <button
            key={command.name}
            id={`${id}-${index}`}
            type="button"
            role="option"
            tabIndex={-1}
            aria-selected={index === selected}
            aria-disabled={!!command.disabledReason}
            onMouseEnter={() => onHighlight(index)}
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => onSelect(command)}
          >
            <command.icon size={17} aria-hidden="true" />
            <span>
              <strong>/{command.name}</strong>
              <small>{command.disabledReason ?? command.description}</small>
            </span>
            <span className="slash-command-alias">/{command.aliases[0]}</span>
          </button>
        ))}
      </div>
      {!commands.length && <p>没有匹配的命令。按 Esc 可作为普通消息发送。</p>}
    </div>
  )
}
