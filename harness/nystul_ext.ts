// omp extension for Nystul the Wizard (the viz AI assistant).
//
// Registers the uo_* lookup tools. Each call runs harness/nystul_tools.py with an argv
// (no shell): the Python side enforces the read-only, secret-free surface. viz_server
// (harness/nystul.py) loads this file with `omp -e` and restricts the run to these tools
// with `--tools`, so omp's own read/grep/bash are never available to the model.
//
// Env (set by harness/nystul.py): NYSTUL_PY (interpreter), NYSTUL_ROOT (repo root).
// No imports, so the file needs no node_modules.

// The slice of omp's ExtensionAPI this file uses (typed locally: no imports).
interface Schema {
	optional(): Schema;
	int(): Schema;
}
interface Zod {
	object(shape: Record<string, Schema>): Schema;
	string(): Schema;
	number(): Schema;
	enum(values: readonly string[]): Schema;
	array(item: Schema): Schema;
}
interface ExecResult {
	code: number;
	stdout: string;
	stderr: string;
	killed?: boolean;
}
interface ExtensionApi {
	zod: Zod;
	exec(cmd: string, args: string[], opts: { signal?: AbortSignal }): Promise<ExecResult>;
	registerTool(def: object): void;
}

export default function (pi: ExtensionApi) {
	const z = pi.zod;
	const py = process.env.NYSTUL_PY ?? "python";
	const root = process.env.NYSTUL_ROOT ?? ".";
	const sep = root.includes("\\") ? "\\" : "/";
	const script = [root.replace(/[\\/]+$/, ""), "harness", "nystul_tools.py"].join(sep);

	const TOOLS = [
		{
			name: "uo_api",
			label: "Scrying pool",
			description:
				"GET a read-only route of the viz server and return its JSON (compact). " +
				"route must be one of /api/state, /api/health, /api/gate, /api/jobs, /api/jobs/plan, /api/overseer, " +
				"/api/captcha, /api/lumber/grove, /api/skillnames, /api/cliloc, /api/facet (a ?query string is allowed, " +
				"e.g. /api/jobs?job=lumber). path is an optional dotted projection with numeric list indices " +
				"(negative counts from the end), e.g. world.self, world.mobiles, movement, events.-1. " +
				"max_chars caps the output (default 20000). Example: {\"route\": \"/api/state\", \"path\": \"world.self\"}",
			parameters: z.object({
				route: z.string(),
				path: z.string().optional(),
				max_chars: z.number().int().optional(),
			}),
		},
		{
			name: "uo_ctl",
			label: "Seer's instruments",
			description:
				"Run a read-only harness ctl command and return its text output. command is one of status " +
				"(character, position, hits, mana, stamina, weight), journal (recent game messages), npcs (nearby mobiles), " +
				"map (ascii map around the character), runes (args[0] must be libraries, find or near), junctures (the " +
				"Seer's decision points), chat (the Seer chat log). args are extra ctl arguments. " +
				"Example: {\"command\": \"runes\", \"args\": [\"find\", \"britain\"]}",
			parameters: z.object({
				command: z.enum(["status", "journal", "npcs", "map", "runes", "junctures", "chat"]),
				args: z.array(z.string()).optional(),
			}),
		},
		{
			name: "uo_sql",
			label: "Archives",
			description:
				"Run one read-only SELECT on a sqlite database and return a header line plus tab-separated rows. " +
				"db is harness (the memory store: sessions, events, walk_moves, harvest_nodes, harvest_attempts, episodes, " +
				"junctures, chat, job_events, teleporters, guard_points, knowledge, lumber_spots, prices, meta), discord " +
				"(raw Outlands Discord messages) or discord_kb (vetted Discord facts). limit caps rows (default 100, max 500). " +
				"Writes, ATTACH and most PRAGMAs are refused; PRAGMA table_info(...) works. Discover tables with " +
				"SELECT name, sql FROM sqlite_master. Example: {\"db\": \"harness\", \"query\": \"SELECT id, job, outcome FROM episodes ORDER BY id DESC\", \"limit\": 20}",
			parameters: z.object({
				db: z.enum(["harness", "discord", "discord_kb"]),
				query: z.string(),
				limit: z.number().int().optional(),
			}),
		},
		{
			name: "uo_knowledge",
			label: "Codex",
			description:
				"Search the harness's long-term game knowledge (the Codex: facts, procedures, places, prices, lessons) by " +
				"meaning and words. kind optionally filters to one of fact, procedure, episode, preference, insight. " +
				"limit default 10, max 30. Returns entries as #id kind [topic] (confidence, importance, status, updated): content. " +
				"Example: {\"query\": \"hatchet durability\", \"limit\": 5}",
			parameters: z.object({
				query: z.string(),
				kind: z.string().optional(),
				limit: z.number().int().optional(),
			}),
		},
		{
			name: "uo_discord",
			label: "Town criers",
			description:
				"Search the Outlands community Discord. source facts (default) returns vetted facts as " +
				"[verdict confidence] topic: statement plus a message link; source messages returns raw chat messages. " +
				"k caps results (default 8, max 20). channel and since (YYYY-MM-DD) filter messages. " +
				"Example: {\"query\": \"best lumberjacking spot\", \"source\": \"facts\", \"k\": 5}",
			parameters: z.object({
				query: z.string(),
				source: z.enum(["facts", "messages"]).optional(),
				k: z.number().int().optional(),
				channel: z.string().optional(),
				since: z.string().optional(),
			}),
		},
		{
			name: "uo_read",
			label: "Tome",
			description:
				"Read lines start..end (1-based, inclusive; default 1..400, max span 1000) of a text file in the harness " +
				"repository, numbered as N: text. path is relative to the repository root. Secrets and databases are sealed. " +
				"Example: {\"path\": \"docs/NOTES.md\", \"start\": 1, \"end\": 120}",
			parameters: z.object({
				path: z.string(),
				start: z.number().int().optional(),
				end: z.number().int().optional(),
			}),
		},
		{
			name: "uo_grep",
			label: "Search tomes",
			description:
				"Case-insensitive regex search over text files of the harness repository. path limits the search to a " +
				"file or directory (default the whole repository); glob filters relative paths (fnmatch, e.g. docs/*.md or " +
				"logs/session_*.jsonl). max caps matches (default 50, max 200). Returns relpath:line: text. " +
				"Example: {\"pattern\": \"hatchet\", \"glob\": \"docs/*.md\"}",
			parameters: z.object({
				pattern: z.string(),
				path: z.string().optional(),
				glob: z.string().optional(),
				max: z.number().int().optional(),
			}),
		},
		{
			name: "uo_list",
			label: "Library",
			description:
				"List a directory of the harness repository (non-recursive) with sizes and modified times, or with glob " +
				"list recursive matches under it (e.g. **/*.md). path defaults to the repository root. " +
				"Example: {\"path\": \"docs\"}",
			parameters: z.object({
				path: z.string().optional(),
				glob: z.string().optional(),
			}),
		},
	];

	for (const t of TOOLS) {
		pi.registerTool({
			...t,
			loadMode: "essential",
			approval: "read",
			async execute(_id: string, params: unknown, signal?: AbortSignal) {
				const b64 = Buffer.from(JSON.stringify(params ?? {})).toString("base64");
				const r = await pi.exec(py, [script, t.name, b64], { signal });
				if (r.killed) throw new Error("cancelled");
				if (r.code === 0) return { content: [{ type: "text", text: r.stdout }] };
				throw new Error((r.stdout ?? "").trim() || (r.stderr ?? "").trim() || `exit ${r.code}`);
			},
		});
	}
}
