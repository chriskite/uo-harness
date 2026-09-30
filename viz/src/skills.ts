// Skill names: the server's list (S2C 0x3A type 0xFE) when this session got one,
// else the game client's own skills.mul names (GET /api/skillnames), which is
// what the client itself shows.

export function skillName(id: number, serverNames: readonly string[] | undefined, clientNames: readonly string[] | null): string {
  return serverNames?.[id] || clientNames?.[id] || `skill #${id}`;
}

let cached: Promise<string[] | null> | null = null;

/** The client's skill names, fetched once per page load (null when unavailable). */
export function fetchClientSkillNames(): Promise<string[] | null> {
  cached ??= fetch("api/skillnames")
    .then((r) => (r.ok ? (r.json() as Promise<{ names: string[] }>) : null))
    .then((j) => j?.names ?? null)
    .catch(() => null);
  return cached;
}
