const store = new Map<string, Map<string, unknown>>();

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
		refresh: async (): Promise<T> => {
			const value = await loader();
			let target = store.get(gameDir);
			if (target === undefined) {
				target = new Map<string, unknown>();
				store.set(gameDir, target);
			}
			target.set(key, value);
			return value;
		}
	};
}

export function invalidate(gameDir: string): void {
	store.delete(gameDir);
}
