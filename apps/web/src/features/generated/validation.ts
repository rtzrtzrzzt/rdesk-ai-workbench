import type { SurfaceComponent, SurfaceSource, TrustedSurfaceDocument } from './types'
export function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    value !== null &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)
  )
}
export function exactKeys(value: Record<string, unknown>, allowed: readonly string[]) {
  return Object.keys(value).every((key) => allowed.includes(key))
}
/** Bounded read-only preview: malformed payloads cannot force unbounded JSON rendering. */
export function plainJSON(value: unknown): string {
  try {
    const seen = new WeakSet<object>()
    let budget = 180
    function preview(item: unknown, depth: number): unknown {
      if (--budget < 0) return '[预览条目预算已满]'
      if (typeof item === 'string')
        return item.length > 2000 ? `${item.slice(0, 2000)}…[文本已截断]` : item
      if (item === null || typeof item === 'number' || typeof item === 'boolean') return item
      if (typeof item !== 'object') return '[非 JSON 值]'
      if (seen.has(item)) return '[重复或循环引用]'
      if (depth > 8) return '[预览深度上限]'
      seen.add(item)
      if (Array.isArray(item))
        return [
          ...item.slice(0, 50).map((child) => preview(child, depth + 1)),
          ...(item.length > 50 ? ['[更多条目已省略]'] : []),
        ]
      if (!isRecord(item)) return '[非纯数据对象]'
      const keys = Object.keys(item)
      return Object.fromEntries([
        ...keys.slice(0, 50).map((key) => [key, preview(item[key], depth + 1)]),
        ...(keys.length > 50 ? [['[预览已截断]', '更多字段已省略']] : []),
      ])
    }
    const result = JSON.stringify(preview(value, 0), null, 2)
    return result.length > 16000 ? `${result.slice(0, 16000)}\n…只读预览已截断` : result
  } catch {
    return '此数据无法安全显示为 JSON。'
  }
}
const text = (value: unknown): value is string =>
  typeof value === 'string' && value.length <= 100000
const identifier = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= 256
const strings = (value: unknown, max: number): value is string[] =>
  Array.isArray(value) && value.length <= max && value.every(text)
const containers = new Set(['Card', 'Column', 'Row', 'Details'])
function componentError(value: Record<string, unknown>, sources: SurfaceSource[]): string | null {
  if (!identifier(value.id) || typeof value.type !== 'string') return '组件必须有明确 ID 与类型。'
  if (!exactKeys(value, ['id', 'type', 'props', 'children']))
    return '组件含目录外字段，不接受样式、URL 或动作。'
  if (!strings(value.children, 64) || !value.children.every(identifier))
    return 'children 必须是受限的组件 ID 数组。'
  if (new Set(value.children).size !== value.children.length)
    return '同一父节点不能重复引用子节点。'
  if (!isRecord(value.props)) return '组件 props 必须为纯数据对象。'
  const p = value.props
  if (!containers.has(value.type) && value.children.length > 0) return '只有布局容器可持有子节点。'
  switch (value.type) {
    case 'Text':
      return exactKeys(p, ['text']) && text(p.text) ? null : 'Text 只接受 text 文本。'
    case 'Heading':
      return exactKeys(p, ['text', 'level']) && text(p.text) && (p.level === 2 || p.level === 3)
        ? null
        : 'Heading 只接受文本和 level 2/3。'
    case 'Card':
    case 'Column':
    case 'Row':
    case 'Details':
      return exactKeys(p, ['title']) && (p.title === undefined || text(p.title))
        ? null
        : '容器只接受可选 title。'
    case 'List':
      return exactKeys(p, ['items']) && strings(p.items, 1000)
        ? null
        : 'List 只接受至多 1000 项文本。'
    case 'Table': {
      if (!sources.length) return '表格没有绑定来源，不能显示为可信数据。'
      if (
        !exactKeys(p, ['columns', 'rows']) ||
        !strings(p.columns, 50) ||
        p.columns.length === 0 ||
        !Array.isArray(p.rows) ||
        p.rows.length > 1000
      )
        return 'Table 需要 1–50 列与至多 1000 行。'
      const columns = p.columns.length
      return p.rows.every(
        (row) =>
          Array.isArray(row) &&
          row.length === columns &&
          row.every(
            (cell) =>
              cell === null || text(cell) || (typeof cell === 'number' && Number.isFinite(cell)),
          ),
      )
        ? null
        : '表格每行须与列数一致，仅接受文本、有限数值与 null。'
    }
    case 'Chart': {
      if (!sources.length) return '图表没有绑定来源，不能显示为可信数据。'
      if (
        !exactKeys(p, ['kind', 'x_label', 'y_label', 'unit', 'points']) ||
        (p.kind !== 'bar' && p.kind !== 'line') ||
        !text(p.x_label) ||
        !text(p.y_label) ||
        !text(p.unit) ||
        !Array.isArray(p.points) ||
        p.points.length > 2000
      )
        return 'Chart 只接受 bar/line、轴名、单位与至多 2000 个点。'
      return p.points.every(
        (point) =>
          isRecord(point) &&
          exactKeys(point, ['label', 'value']) &&
          text(point.label) &&
          (point.value === null ||
            (typeof point.value === 'number' && Number.isFinite(point.value))),
      )
        ? null
        : '图表点仅接受 label 与有限数值或 null。'
    }
    case 'FileLink':
      return exactKeys(p, ['artifact_id', 'label']) && identifier(p.artifact_id) && text(p.label)
        ? null
        : 'FileLink 只接受 artifact_id 和 label，不能接收 URL。'
    default:
      return `不支持组件类型：${value.type.slice(0, 100)}。`
  }
}
export type InspectedNode = {
  id: string
  children: string[]
  raw: Record<string, unknown>
  component?: SurfaceComponent
  error?: string
}
export type InspectedSurface = Omit<TrustedSurfaceDocument, 'components'> & {
  components: InspectedNode[]
}
export type SurfaceInspection = {
  document: InspectedSurface | null
  errors: Map<string, string>
  reason?: string
}
/** Structure errors stop the surface; a malformed leaf only degrades its own subtree. */
export function inspectSurface(value: unknown): SurfaceInspection {
  const invalid = (reason: string): SurfaceInspection => ({
    document: null,
    errors: new Map(),
    reason,
  })
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'protocol',
      'catalog_version',
      'title',
      'root_id',
      'components',
      'sources',
      'artifact_id',
      'run_id',
    ])
  )
    return invalid('产物必须是已知字段组成的纯数据对象。')
  if (value.protocol !== 'workbench-ui/1' || value.catalog_version !== 'workbench-core/1')
    return invalid('不支持此协议或组件目录版本。')
  if (
    !text(value.title) ||
    !identifier(value.root_id) ||
    (value.artifact_id !== undefined && !identifier(value.artifact_id)) ||
    (value.run_id !== undefined && !identifier(value.run_id))
  )
    return invalid('标题、根节点或追溯标识无效。')
  if (
    !Array.isArray(value.sources) ||
    value.sources.length > 64 ||
    !value.sources.every(
      (source) =>
        isRecord(source) &&
        exactKeys(source, ['artifact_id', 'version']) &&
        identifier(source.artifact_id) &&
        typeof source.version === 'string' &&
        /^[a-f0-9]{64}$/.test(source.version),
    )
  )
    return invalid('来源必须是 artifact_id 与 SHA-256 版本列表。')
  const sources: SurfaceSource[] = value.sources.map((source) => ({
    artifact_id: source.artifact_id,
    version: source.version,
  }))
  if (new Set(sources.map((source) => source.artifact_id)).size !== sources.length)
    return invalid('来源 ID 不得重复。')
  if (
    !Array.isArray(value.components) ||
    value.components.length === 0 ||
    value.components.length > 64
  )
    return invalid('组件数量须为 1–64。')
  const nodes = new Map<string, { raw: Record<string, unknown>; children: string[] }>()
  for (const component of value.components) {
    if (!isRecord(component) || !identifier(component.id))
      return invalid('每个组件必须有可识别的唯一 ID。')
    if (nodes.has(component.id)) return invalid('组件 ID 重复，无法确定引用目标。')
    if (!strings(component.children, 64) || !component.children.every(identifier))
      return invalid(`组件 ${component.id} 的子节点引用无效。`)
    nodes.set(component.id, { raw: component, children: component.children })
  }
  if (!nodes.has(value.root_id)) return invalid('根节点不存在。')
  const parents = new Map<string, number>()
  for (const node of nodes.values())
    for (const child of node.children) {
      if (!nodes.has(child)) return invalid(`子节点 ${child} 不存在。`)
      parents.set(child, (parents.get(child) ?? 0) + 1)
      if ((parents.get(child) ?? 0) > 1) return invalid('每个节点只能被一个父节点引用。')
    }
  if (parents.has(value.root_id)) return invalid('根节点不能被其他节点引用。')
  const visiting = new Set<string>(),
    visited = new Set<string>()
  function walk(id: string, depth: number): string | null {
    if (depth > 8) return '组件深度超过 8。'
    if (visiting.has(id)) return '组件引用存在循环。'
    if (visited.has(id)) return null
    visiting.add(id)
    for (const child of nodes.get(id)?.children ?? []) {
      const reason = walk(child, depth + 1)
      if (reason) return reason
    }
    visiting.delete(id)
    visited.add(id)
    return null
  }
  const reason = walk(value.root_id, 1)
  if (reason) return invalid(reason)
  if (visited.size !== nodes.size) return invalid('存在未从根节点连接的组件，拒绝隐藏或孤立结构。')
  const errors = new Map<string, string>()
  const components: InspectedNode[] = []
  for (const [id, node] of nodes) {
    const error = componentError(node.raw, sources)
    if (error) {
      errors.set(id, error)
      components.push({ id, children: node.children, raw: node.raw, error })
    }
    // Only a node whose full discriminated catalog validation succeeded gets the renderable type.
    else
      components.push({
        id,
        children: node.children,
        raw: node.raw,
        component: node.raw as SurfaceComponent,
      })
  }
  return {
    document: {
      protocol: 'workbench-ui/1',
      catalog_version: 'workbench-core/1',
      title: value.title,
      root_id: value.root_id,
      components,
      sources,
      ...(value.artifact_id !== undefined ? { artifact_id: value.artifact_id } : {}),
      ...(value.run_id !== undefined ? { run_id: value.run_id } : {}),
    },
    errors,
  }
}
