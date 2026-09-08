/**
 * A queued channel between the shell and the pages: a request survives until
 * the page that handles it is mounted and drains it.
 */
export type Intent =
	| { kind: 'sync' }
	| { kind: 'update-all' }
	| { kind: 'change-folder' }
	| { kind: 'snapshot' }
	| { kind: 'launch'; profileId: string }
	| { kind: 'reveal-mod'; modId: number };

export type IntentKind = Intent['kind'];

const pending = new Map<IntentKind, Intent>();
const listeners = new Set<() => void>();

/** At most one request per kind is pending; a newer one replaces the older. */
export function requestIntent(intent: Intent): void {
	pending.set(intent.kind, intent);
	for (const listener of [...listeners]) {
		listener();
	}
}

export function takeIntents<K extends IntentKind>(
	kinds: readonly K[]
): Extract<Intent, { kind: K }>[] {
	const taken: Extract<Intent, { kind: K }>[] = [];
	for (const kind of kinds) {
		const intent = pending.get(kind);
		if (intent !== undefined) {
			pending.delete(kind);
			taken.push(intent as Extract<Intent, { kind: K }>);
		}
	}
	return taken;
}

export function onIntents(fn: () => void): () => void {
	listeners.add(fn);
	return () => {
		listeners.delete(fn);
	};
}
