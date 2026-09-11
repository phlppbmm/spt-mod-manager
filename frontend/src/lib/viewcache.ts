const store = new Map<string, Map<string, unknown>>();
const running = new Map<string, Map<string, Promise<unknown>>>();

export interface CachedView<T> {
	readonly value: T | undefined;
	refresh: () => Promise<T>;
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
		refresh: (): Promise<T> => load(gameDir, key, loader)
	};
}

/**
 * One load per key at a time: a view asking for something another view is
 * already fetching waits for that request instead of sending a second one.
 */
function load<T>(gameDir: string, key: string, loader: () => Promise<T>): Promise<T> {
	const shared = running.get(gameDir)?.get(key);
	if (shared !== undefined) {
		return shared as Promise<T>;
	}
	const request = loader().then((value) => {
		remember(store, gameDir, key, value);
		return value;
	});
	remember(running, gameDir, key, request);
	return request.finally(() => {
		running.get(gameDir)?.delete(key);
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
}
