/** Versioned, read-only workbench catalog. This is not an A2UI wire protocol. */
export type SurfaceSource = { artifact_id: string; version: string }
export type ChartProps = {
  kind: 'bar' | 'line'
  x_label: string
  y_label: string
  unit: string
  points: { label: string; value: number | null }[]
}
export type SurfaceProps = {
  Text: { text: string }
  Heading: { text: string; level: 2 | 3 }
  Card: { title?: string }
  Column: { title?: string }
  Row: { title?: string }
  Details: { title?: string }
  List: { items: string[] }
  Table: { columns: string[]; rows: (string | number | null)[][] }
  Chart: ChartProps
  FileLink: { artifact_id: string; label: string }
}
export type SurfaceComponent = {
  [K in keyof SurfaceProps]: { id: string; type: K; props: SurfaceProps[K]; children: string[] }
}[keyof SurfaceProps]
export type TrustedSurfaceDocument = {
  protocol: 'workbench-ui/1'
  catalog_version: 'workbench-core/1'
  title: string
  root_id: string
  components: SurfaceComponent[]
  sources: SurfaceSource[]
  artifact_id?: string
  run_id?: string
}
export type FileLinkRequest = {
  artifact_id: string
  label: string
  version?: string
  surface_artifact_id?: string
  run_id?: string
}
export type TrustedSurfaceProps = {
  /** Raw API data is checked again at the rendering boundary. */
  surface: unknown
  onFileLink?: (request: FileLinkRequest) => void | Promise<void>
}
