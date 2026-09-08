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
		server_stoppable: false,
		client_stoppable: false,
		...overrides
	};
}

describe('loadStopServerOnExit', () => {
	it.each([
		{ id: 'nothing-stored', stored: null, expected: true },
		{ id: '"false"', stored: 'false', expected: false },
		{ id: '"true"', stored: 'true', expected: true },
		{ id: 'unrecognised-value', stored: 'garbage', expected: true }
	])('$id yields $expected', ({ stored, expected }) => {
		if (stored !== null) localStorage.setItem(STOP_SERVER_KEY, stored);

		expect(loadStopServerOnExit()).toBe(expected);
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
	it.each([
		{ id: 'null-status', running: null, expected: false },
		{ id: 'nothing-running', running: {}, expected: false },
		{ id: 'client-only', running: { client_running: true }, expected: true },
		{ id: 'server-only', running: { server_running: true }, expected: true },
		{ id: 'both-running', running: { server_running: true, client_running: true }, expected: true }
	])('$id is $expected', ({ running, expected }) => {
		expect(isGameUp(running === null ? null : status(running))).toBe(expected);
	});
});

describe('bannerText', () => {
	it.each([
		{ id: 'null-status', running: null, expected: '' },
		{ id: 'not-running', running: {}, expected: '' },
		{
			id: 'running',
			running: { client_running: true },
			expected: 'The game is running, mod changes are paused'
		}
	])('$id yields the banner $expected', ({ running, expected }) => {
		expect(bannerText(running === null ? null : status(running))).toBe(expected);
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
				hint: 'Closes the game, and the SPT server if sptmod started it.'
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
				hint: 'Closes the game, and the SPT server if sptmod started it.'
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
	it.each([
		{ id: 'null-status-slow', running: null, expected: 10000 },
		{ id: 'idle-slow', running: {}, expected: 10000 },
		{ id: 'running-fast', running: { server_running: true }, expected: 3000 }
	])('$id polls after $expected ms', ({ running, expected }) => {
		expect(nextPollDelay(running === null ? null : status(running))).toBe(expected);
	});
});
