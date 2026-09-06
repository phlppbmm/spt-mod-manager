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

interface FakeMediaQuery {
	matches: boolean;
	addEventListener: ReturnType<typeof vi.fn>;
	removeEventListener: ReturnType<typeof vi.fn>;
}

let mediaQuery: FakeMediaQuery;
let setAttribute: ReturnType<typeof vi.fn>;

function emitSystemChange(matches: boolean): void {
	const listener = mediaQuery.addEventListener.mock.calls[0][1] as (
		event: { matches: boolean }
	) => void;
	listener({ matches });
}

beforeEach(() => {
	mediaQuery = { matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() };
	setAttribute = vi.fn();
	vi.stubGlobal('localStorage', createMemoryStorage());
	vi.stubGlobal('matchMedia', vi.fn().mockReturnValue(mediaQuery));
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
		mediaQuery.matches = true;

		expect(systemTheme()).toBe('dark');
	});

	it('reports light while the operating system prefers a light colour scheme', () => {
		expect(systemTheme()).toBe('light');
	});

	it('asks for the prefers-color-scheme media query', () => {
		systemTheme();

		expect(matchMedia).toHaveBeenCalledWith('(prefers-color-scheme: dark)');
	});
});

describe('resolveTheme', () => {
	it.each(['light', 'dark'] as const)('returns the explicit preference %s unchanged', (theme) => {
		expect(resolveTheme(theme)).toBe(theme);
	});

	it('resolves "system" against the operating system setting', () => {
		mediaQuery.matches = true;

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

		expect(mediaQuery.removeEventListener).toHaveBeenCalledWith(
			'change',
			mediaQuery.addEventListener.mock.calls[0][1]
		);
	});
});
