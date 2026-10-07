const store = new Map<string, Map<string, unknown>>();
const running = new Map<string, Map<string, Promise<unknown>>>();
const newest = new Map<string, Map<string, Promise<unknown>>>();

export interface RefreshOptions {
	/**
	 * Start a load of its own even while another one for the same key is on its
	 * way. That load may have been started by a view that did not ask for fresh
	 * data, and joining it would answer a request for fresh data with old data.
	 */
	force?: boolean;
}

export interface CachedView<T> {
	readonly value: T | undefined;
	refresh: (options?: RefreshOptions) => Promise<T>;
}

export function cached<T>(
	gameDir: string,
	name: string,
	args: readonly unknown[],
	loader: () => Promise<T>
): CachedView<T> {
	const key = `${name}:${JSON.stringify(args)}`;
	const entries = store.get(gameDir);
	return {
		value: entries === undefined ? undefined : (entries.get(key) as T | undefined),
		refresh: (options?: RefreshOptions): Promise<T> =>
			load(gameDir, key, loader, options?.force === true)
	};
}

/**
 * One load per key at a time: a view asking for something another view is
 * already fetching waits for that request instead of sending a second one. A
 * forced load starts its own and becomes the one later views join. Only the
 * most recently started load may store its answer, so an older one finishing
 * late never overwrites what a newer one brought back.
 */
function load<T>(
	gameDir: string,
	key: string,
	loader: () => Promise<T>,
	force: boolean
): Promise<T> {
	const shared = running.get(gameDir)?.get(key);
	if (shared !== undefined && !force) {
		return shared as Promise<T>;
	}
	const request: Promise<T> = loader().then((value) => {
		if (newest.get(gameDir)?.get(key) === request) {
			remember(store, gameDir, key, value);
		}
		return value;
	});
	remember(running, gameDir, key, request);
	remember(newest, gameDir, key, request);
	return request.finally(() => {
		if (running.get(gameDir)?.get(key) === request) {
			running.get(gameDir)?.delete(key);
		}
	});
}

function remember<T>(
	target: Map<string, Map<string, T>>,
	gameDir: string,
	key: string,
	value: T
): void {
	let entries = target.get(gameDir);
	if (entries === undefined) {
		entries = new Map<string, T>();
		target.set(gameDir, entries);
	}
	entries.set(key, value);
}

export function invalidate(gameDir: string): void {
	store.delete(gameDir);
	running.delete(gameDir);
	newest.delete(gameDir);
}
