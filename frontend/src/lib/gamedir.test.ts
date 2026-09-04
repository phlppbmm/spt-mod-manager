import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { forgetGameDir, loadGameDir, saveGameDir } from './gamedir';

/**
 * `gamedir.ts` talks directly to the browser's `localStorage`, which does not
 * exist as a global in Vitest's `node` test environment. A minimal in-memory
 * `Storage` stand-in is the allowed system-edge stub here, analogous to the
 * Tauri `invoke` mock used for `api/client.ts`.
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

describe('loadGameDir', () => {
	it('returns null when nothing has been saved yet', () => {
		expect(loadGameDir()).toBeNull();
	});
});

describe('saveGameDir', () => {
	it('persists a path that loadGameDir subsequently returns', () => {
		saveGameDir('/home/user/SPT');

		expect(loadGameDir()).toBe('/home/user/SPT');
	});

	it('overwrites a previously saved path', () => {
		saveGameDir('/first/path');
		saveGameDir('/second/path');

		expect(loadGameDir()).toBe('/second/path');
	});
});

describe('forgetGameDir', () => {
	it('removes a previously saved path so loadGameDir returns null again', () => {
		saveGameDir('/home/user/SPT');

		forgetGameDir();

		expect(loadGameDir()).toBeNull();
	});

	it('is a no-op when nothing was saved', () => {
		forgetGameDir();

		expect(loadGameDir()).toBeNull();
	});
});
