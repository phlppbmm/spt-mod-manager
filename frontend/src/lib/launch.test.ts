import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	STOP_SERVER_KEY,
	bannerText,
	isGameUp,
	launchAction,
	loadStopServerOnExit,
	nextPollDelay,
	saveStopServerOnExit,
	type LaunchAction
} from './launch';
import type { LaunchStatus } from './api/client';

/**
 * `launch.ts` talks directly to the browser's `localStorage`, which does not
 * exist as a global in Vitest's `node` test environment. A minimal in-memory
 * `Storage` stand-in is the allowed system-edge stub here, matching the
 * pattern in `gamedir.test.ts`.
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

function status(overrides: Partial<LaunchStatus> = {}): LaunchStatus {
	return {
		server_running: false,
		client_running: false,
		server_pid: 0,
		client_pid: 0,
		profile_id: '',
		...overrides
	};
}

describe('loadStopServerOnExit', () => {
	it('defaults to true when nothing was saved', () => {
		expect(loadStopServerOnExit()).toBe(true);
	});

	it('returns false only for the exact stored string "false"', () => {
		localStorage.setItem(STOP_SERVER_KEY, 'false');

		expect(loadStopServerOnExit()).toBe(false);
	});

	it('returns true for the stored string "true"', () => {
		localStorage.setItem(STOP_SERVER_KEY, 'true');

		expect(loadStopServerOnExit()).toBe(true);
	});

	it('returns true for any other stored value', () => {
		localStorage.setItem(STOP_SERVER_KEY, 'garbage');

		expect(loadStopServerOnExit()).toBe(true);
	});

	it('propagates an error from a storage backend that cannot be read', () => {
		vi.stubGlobal('localStorage', {
			getItem: () => {
				throw new Error('storage unavailable');
			}
		});

		expect(() => loadStopServerOnExit()).toThrow('storage unavailable');
	});
});

describe('saveStopServerOnExit', () => {
	it('stores the exact string "true"', () => {
		saveStopServerOnExit(true);

		expect(localStorage.getItem(STOP_SERVER_KEY)).toBe('true');
	});

	it('stores the exact string "false"', () => {
		saveStopServerOnExit(false);

		expect(localStorage.getItem(STOP_SERVER_KEY)).toBe('false');
	});
});

describe('isGameUp', () => {
	it('is false for a null status', () => {
		expect(isGameUp(null)).toBe(false);
	});

	it('is false when neither process is running', () => {
		expect(isGameUp(status())).toBe(false);
	});

	it('is true when only the client is running', () => {
		expect(isGameUp(status({ client_running: true }))).toBe(true);
	});

	it('is true when only the server is running', () => {
		expect(isGameUp(status({ server_running: true }))).toBe(true);
	});

	it('is true when both are running', () => {
		expect(isGameUp(status({ server_running: true, client_running: true }))).toBe(true);
	});
});

describe('bannerText', () => {
	it('is empty for a null status', () => {
		expect(bannerText(null)).toBe('');
	});

	it('is empty when the game is not running', () => {
		expect(bannerText(status())).toBe('');
	});

	it('names the running state when the game is up', () => {
		expect(bannerText(status({ client_running: true }))).toBe(
			'The game is running, mod changes are paused'
		);
	});
});

describe('launchAction', () => {
	const cases: Array<{
		name: string;
		status: LaunchStatus | null;
		profileId: string;
		busy: boolean;
		expected: LaunchAction;
	}> = [
		{
			name: 'idle, not busy: enabled launch with no hint',
			status: null,
			profileId: 'p1',
			busy: false,
			expected: { label: 'Launch', disabled: false, mode: 'launch', hint: '' }
		},
		{
			name: 'idle, busy: disabled launch, in-flight hint',
			status: null,
			profileId: 'p1',
			busy: true,
			expected: {
				label: 'Starting…',
				disabled: true,
				mode: 'launch',
				hint: 'A launch is running.'
			}
		},
		{
			name: 'this profile running, not busy: enabled stop',
			status: status({ client_running: true, profile_id: 'p1' }),
			profileId: 'p1',
			busy: false,
			expected: {
				label: 'Stop',
				disabled: false,
				mode: 'stop',
				hint: 'Closes the game and the SPT server.'
			}
		},
		{
			name: 'this profile running, busy: disabled stop (busy overrides only "disabled")',
			status: status({ client_running: true, profile_id: 'p1' }),
			profileId: 'p1',
			busy: true,
			expected: {
				label: 'Stop',
				disabled: true,
				mode: 'stop',
				hint: 'Closes the game and the SPT server.'
			}
		},
		{
			name: 'a different profile running, not busy: disabled launch, already-running hint',
			status: status({ client_running: true, profile_id: 'other' }),
			profileId: 'p1',
			busy: false,
			expected: {
				label: 'Launch',
				disabled: true,
				mode: 'launch',
				hint: 'The game is already running.'
			}
		},
		{
			name: 'a different profile running, busy: the busy hint wins',
			status: status({ client_running: true, profile_id: 'other' }),
			profileId: 'p1',
			busy: true,
			expected: {
				label: 'Starting…',
				disabled: true,
				mode: 'launch',
				hint: 'A launch is running.'
			}
		},
		{
			name: 'known but idle status, not busy: enabled launch with no hint',
			status: status({ profile_id: 'p1' }),
			profileId: 'p1',
			busy: false,
			expected: { label: 'Launch', disabled: false, mode: 'launch', hint: '' }
		}
	];

	it.each(cases)('$name', ({ status: s, profileId, busy, expected }) => {
		expect(launchAction(s, profileId, busy)).toEqual(expected);
	});
});

describe('launchAction with a Fika host', () => {
	const cases: Array<{
		name: string;
		status: LaunchStatus | null;
		profileId: string;
		busy: boolean;
		hostLabel: string;
		expected: LaunchAction;
	}> = [
		{
			name: 'idle, not busy: the button names the host it connects to',
			status: null,
			profileId: 'p1',
			busy: false,
			hostLabel: 'pc.local',
			expected: { label: 'Connect to pc.local', disabled: false, mode: 'launch', hint: '' }
		},
		{
			name: 'idle, busy: connecting instead of starting, same in-flight hint',
			status: null,
			profileId: 'p1',
			busy: true,
			hostLabel: 'pc.local',
			expected: {
				label: 'Connecting…',
				disabled: true,
				mode: 'launch',
				hint: 'A launch is running.'
			}
		},
		{
			name: 'a different profile running: named but disabled, already-running hint',
			status: status({ client_running: true, profile_id: 'other' }),
			profileId: 'p1',
			busy: false,
			hostLabel: 'pc.local',
			expected: {
				label: 'Connect to pc.local',
				disabled: true,
				mode: 'launch',
				hint: 'The game is already running.'
			}
		},
		{
			name: 'this profile running: the stop button keeps its label and spares the host',
			status: status({ server_running: true, profile_id: 'p1' }),
			profileId: 'p1',
			busy: false,
			hostLabel: 'pc.local',
			expected: {
				label: 'Stop',
				disabled: false,
				mode: 'stop',
				hint: "Closes the game. The host's server keeps running."
			}
		},
		{
			name: 'an empty host label behaves like the omitted parameter',
			status: null,
			profileId: 'p1',
			busy: false,
			hostLabel: '',
			expected: { label: 'Launch', disabled: false, mode: 'launch', hint: '' }
		},
		{
			name: 'an empty host label while busy still starts rather than connects',
			status: null,
			profileId: 'p1',
			busy: true,
			hostLabel: '',
			expected: {
				label: 'Starting…',
				disabled: true,
				mode: 'launch',
				hint: 'A launch is running.'
			}
		}
	];

	it.each(cases)('$name', ({ status: s, profileId, busy, hostLabel, expected }) => {
		expect(launchAction(s, profileId, busy, hostLabel)).toEqual(expected);
	});
});

describe('nextPollDelay', () => {
	it('polls slowly for a null status', () => {
		expect(nextPollDelay(null)).toBe(10000);
	});

	it('polls slowly while nothing is running', () => {
		expect(nextPollDelay(status())).toBe(10000);
	});

	it('polls quickly while the game is running', () => {
		expect(nextPollDelay(status({ server_running: true }))).toBe(3000);
	});
});
