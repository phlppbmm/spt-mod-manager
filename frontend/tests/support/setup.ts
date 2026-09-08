import '@testing-library/jest-dom/vitest';

import { afterEach, beforeEach, vi } from 'vitest';

/**
 * Node defines a lazy `globalThis.localStorage` getter that resolves to
 * `undefined` unless the process was started with `--localstorage-file`, and it
 * shadows the one happy-dom installs. An in-memory `Storage` per test restores
 * the browser behaviour the components rely on.
 */
function createMemoryStorage(): Storage {
	const store = new Map<string, string>();
	return {
		getItem: (key: string) => (store.has(key) ? (store.get(key) as string) : null),
		setItem: (key: string, value: string) => {
			store.set(key, value);
		},
		removeItem: (key: string) => {
			store.delete(key);
		},
		clear: () => store.clear(),
		key: (index: number) => Array.from(store.keys())[index] ?? null,
		get length() {
			return store.size;
		}
	};
}

beforeEach(() => {
	vi.stubGlobal('localStorage', createMemoryStorage());
});

afterEach(() => {
	vi.unstubAllGlobals();
});
