import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	THEME_KEY,
	applyTheme,
	loadPreference,
	resolveTheme,
	savePreference,
	systemTheme,
	toggledPreference,
	watchSystemTheme
} from './theme';

/**
 * `theme.ts` talks to `localStorage`, `matchMedia` and `document`, none of
 * which exist in Vitest's `node` environment. Minimal stand-ins are the
 * allowed system-edge stubs here, as in `gamedir.test.ts`.
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

type SystemChangeListener = (event: { matches: boolean }) => void;

interface FakeMediaQuery {
	matches: boolean;
	addEventListener: (type: string, listener: SystemChangeListener) => void;
	removeEventListener: (type: string, listener: SystemChangeListener) => void;
}

let systemPrefersDark: boolean;
let listeners: Set<SystemChangeListener>;
let setAttribute: ReturnType<typeof vi.fn>;

/**
 * Only the `prefers-color-scheme: dark` query reports the simulated system
 * setting or accepts listeners; every other query behaves like a media
 * feature that never matches and never changes, as a browser would. Asking
 * for the wrong query therefore reports "light" and never notifies.
 */
function mediaQueryFor(query: string): FakeMediaQuery {
	const isDarkQuery = query === '(prefers-color-scheme: dark)';
	return {
		matches: isDarkQuery && systemPrefersDark,
		addEventListener: (_type, listener) => {
			if (isDarkQuery) listeners.add(listener);
		},
		removeEventListener: (_type, listener) => {
			listeners.delete(listener);
		}
	};
}

function emitSystemChange(matches: boolean): void {
	for (const listener of [...listeners]) listener({ matches });
}

beforeEach(() => {
	systemPrefersDark = false;
	listeners = new Set<SystemChangeListener>();
	setAttribute = vi.fn();
	vi.stubGlobal('localStorage', createMemoryStorage());
	vi.stubGlobal('matchMedia', (query: string) => mediaQueryFor(query));
	vi.stubGlobal('document', { documentElement: { setAttribute } });
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('loadPreference', () => {
	it('defaults to "system" when nothing has been saved yet', () => {
		expect(loadPreference()).toBe('system');
	});

	it('falls back to "system" for a value it does not recognise', () => {
		localStorage.setItem(THEME_KEY, 'sepia');

		expect(loadPreference()).toBe('system');
	});

	it.each(['system', 'light', 'dark'] as const)('returns the stored preference %s', (stored) => {
		localStorage.setItem(THEME_KEY, stored);

		expect(loadPreference()).toBe(stored);
	});
});

describe('savePreference', () => {
	it('persists a preference that loadPreference subsequently returns', () => {
		savePreference('dark');

		expect(loadPreference()).toBe('dark');
	});

	it('overwrites a previously saved preference', () => {
		savePreference('dark');
		savePreference('light');

		expect(loadPreference()).toBe('light');
	});
});

describe('systemTheme', () => {
	it('reports dark while the operating system prefers a dark colour scheme', () => {
		systemPrefersDark = true;

		expect(systemTheme()).toBe('dark');
	});

	it('reports light while the operating system prefers a light colour scheme', () => {
		expect(systemTheme()).toBe('light');
	});
});

describe('resolveTheme', () => {
	it.each(['light', 'dark'] as const)('returns the explicit preference %s unchanged', (theme) => {
		expect(resolveTheme(theme)).toBe(theme);
	});

	it('resolves "system" against the operating system setting', () => {
		systemPrefersDark = true;

		expect(resolveTheme('system')).toBe('dark');
	});
});

describe('applyTheme', () => {
	it.each(['light', 'dark'] as const)('marks the document root as %s', (theme) => {
		applyTheme(theme);

		expect(setAttribute).toHaveBeenCalledWith('data-theme', theme);
	});
});

describe('toggledPreference', () => {
	it('switches from dark to light', () => {
		expect(toggledPreference('dark')).toBe('light');
	});

	it('switches from light to dark', () => {
		expect(toggledPreference('light')).toBe('dark');
	});
});

describe('watchSystemTheme', () => {
	it.each([
		{ id: 'dark', matches: true, expected: 'dark' },
		{ id: 'light', matches: false, expected: 'light' }
	])('reports a system switch to $id', ({ matches, expected }) => {
		const handler = vi.fn();
		watchSystemTheme(handler);

		emitSystemChange(matches);

		expect(handler).toHaveBeenCalledWith(expected);
	});

	it('stops reporting once the returned unsubscribe is called', () => {
		const handler = vi.fn();

		const unsubscribe = watchSystemTheme(handler);
		unsubscribe();
		emitSystemChange(true);

		expect(handler).not.toHaveBeenCalled();
	});
});
