export function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function packageManifest(value: unknown) {
  const manifest = record(value) ?? {}
  const skills = Array.isArray(manifest.skills)
    ? manifest.skills.flatMap((value: unknown) => {
        const skill = record(value)
        return skill && typeof skill.name === 'string'
          ? [{ name: skill.name, description: text(skill.description) }]
          : []
      })
    : []
  const servers = Array.isArray(manifest.mcp_servers)
    ? manifest.mcp_servers.flatMap((value: unknown) => {
        const server = record(value)
        return server && typeof server.name === 'string'
          ? [
              {
                name: server.name,
                transport: text(server.transport),
                importable: server.importable === true,
                reason: text(server.reason),
              },
            ]
          : []
      })
    : []
  return {
    name: text(manifest.name),
    description: text(manifest.description),
    version: text(manifest.declared_version),
    format: text(manifest.format),
    license: text(manifest.license),
    skills,
    servers,
    diagnostics: Array.isArray(manifest.diagnostics)
      ? manifest.diagnostics.filter((value: unknown): value is string => typeof value === 'string')
      : [],
  }
}
