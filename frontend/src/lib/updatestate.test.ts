import { describe, expect, it, vi } from 'vitest';
import {
	applyProgress,
	describe as describeState,
	progressPercent,
	runCheck,
	runInstall,
	startupNotice
} from './updatestate';
import type { DownloadProgress, UpdateDeps, UpdateState } from './updatestate';

const DEB_HINT =
	'Automatic updates are only available in the AppImage build — download the new version from the releases page.';
const NO_SERVER =
	'The update server could not be reached, so there is no release information available.';
const NO_BUILD = "No update is available for this computer's system.";
const NOT_VERIFIED = 'The update could not be verified.';
const GENERIC = 'The update could not be downloaded. Try again later.';

const IDLE: UpdateState = { kind: 'idle' };
const CHECKING: UpdateState = { kind: 'checking' };
const UP_TO_DATE: UpdateState = { kind: 'up-to-date' };
const AVAILABLE: UpdateState = { kind: 'available', version: '2.4.0', notes: 'Fixes the launcher' };
const DOWNLOADING: UpdateState = {
	kind: 'downloading',
	version: '2.4.0',
	received: 100,
	total: 4096
};
const INSTALLING: UpdateState = { kind: 'installing', version: '2.4.0' };
const READY: UpdateState = { kind: 'ready', version: '2.4.0' };
const FAILED: UpdateState = { kind: 'failed', message: 'disk full' };

/** The states that carry no version and therefore cannot start a download. */
const VERSIONLESS: ReadonlyArray<readonly [string, UpdateState]> = [
	['idle', IDLE],
	['checking', CHECKING],
	['up-to-date', UP_TO_DATE],
	['failed', FAILED]
];

/** Records every state the runner announces, in order. */
function recorder(): { onState: (state: UpdateState) => void; seen: UpdateState[] } {
	const seen: UpdateState[] = [];
	return { onState: (state: UpdateState) => void seen.push(state), seen };
}

describe('describe', () => {
	it.each([
		['idle renders nothing at all', IDLE, ''],
		['checking', CHECKING, 'Looking for a new version…'],
		['up-to-date', UP_TO_DATE, 'This is the newest version.'],
		['available names the version', AVAILABLE, 'Version 2.4.0 is available.'],
		['downloading names the version', DOWNLOADING, 'Downloading version 2.4.0…'],
		['installing names the version', INSTALLING, 'Installing version 2.4.0…'],
		['ready asks for a restart', READY, 'Version 2.4.0 is installed. Restart sptmod to use it.'],
		['failed hides the raw cause', FAILED, GENERIC]
	])('describes %s', (_name, state: UpdateState, expected: string) => {
		expect(describeState(state)).toBe(expected);
	});

	// The raw wordings come from tauri-plugin-updater 2.11.0 src/error.rs.
	it.each([
		['InvalidUpdaterFormat, the .deb case', 'invalid updater binary format', DEB_HINT],
		[
			'ReleaseNotFound',
			'Could not fetch a valid release JSON from the remote',
			NO_SERVER
		],
		[
			'TargetNotFound',
			'the platform `linux-x86_64` was not found in the response `platforms` object',
			NO_BUILD
		],
		[
			'TargetsNotFound',
			'None of the fallback platforms `["linux-x86_64"]` were found in the response `platforms` object',
			NO_BUILD
		],
		['a rejected signature', 'Signature verification failed', NOT_VERIFIED],
		['an undecodable payload', 'the release payload could not be decoded', NOT_VERIFIED],
		[
			'InsecureTransportProtocol',
			'The configured updater endpoint must use a secure protocol like `https`.',
			NOT_VERIFIED
		]
	])('turns %s into a plain sentence', (_name, message: string, expected: string) => {
		expect(describeState({ kind: 'failed', message })).toBe(expected);
	});

	it('recognises a known wording regardless of its casing', () => {
		expect(describeState({ kind: 'failed', message: 'Invalid Updater Binary Format' })).toBe(
			DEB_HINT
		);
	});

	it('reports an undecodable signature as unverified rather than as a download problem', () => {
		const message =
			'The signature abc could not be decoded, please check if it is a valid base64 string.';

		expect(describeState({ kind: 'failed', message })).toBe(NOT_VERIFIED);
	});

	it.each([
		['an unrecognised plugin error', 'Failed to install .deb package'],
		['a near miss of a known wording', 'the format of the file is odd'],
		['an empty message', '']
	])('falls back to the generic sentence for %s', (_name, message: string) => {
		expect(describeState({ kind: 'failed', message })).toBe(GENERIC);
	});
});

describe('progressPercent', () => {
	it.each([
		['rounds a fraction down', 250, 1000, 25],
		['rounds a repeating fraction to the nearest integer', 1, 3, 33],
		['reports nothing received as zero', 0, 1000, 0],
		['clamps an overshooting byte count to 100', 150, 100, 100]
	])('%s', (_name, received: number, total: number, expected: number) => {
		expect(progressPercent({ kind: 'downloading', version: '2.4.0', received, total })).toBe(
			expected
		);
	});

	it.each([
		['the total is unknown', null],
		['the total is zero', 0],
		['the total is negative', -5]
	])('reports no percentage while downloading when %s', (_name, total: number | null) => {
		expect(progressPercent({ kind: 'downloading', version: '2.4.0', received: 37, total })).toBe(
			null
		);
	});

	it.each([
		['idle', IDLE],
		['checking', CHECKING],
		['up-to-date', UP_TO_DATE],
		['available', AVAILABLE],
		['installing', INSTALLING],
		['ready', READY],
		['failed', FAILED]
	])('reports no percentage in state %s', (_name, state: UpdateState) => {
		expect(progressPercent(state)).toBe(null);
	});
});

describe('applyProgress', () => {
	it('starts a download from the available state with a known total', () => {
		expect(applyProgress(AVAILABLE, { kind: 'started', total: 4096 })).toEqual({
			kind: 'downloading',
			version: '2.4.0',
			received: 0,
			total: 4096
		});
	});

	it('starts a download with an unknown total when the server sends no length', () => {
		expect(applyProgress(AVAILABLE, { kind: 'started', total: null })).toEqual({
			kind: 'downloading',
			version: '2.4.0',
			received: 0,
			total: null
		});
	});

	it('restarts the byte count when a second download starts', () => {
		expect(applyProgress(DOWNLOADING, { kind: 'started', total: 2048 })).toEqual({
			kind: 'downloading',
			version: '2.4.0',
			received: 0,
			total: 2048
		});
	});

	it('adds a chunk to the bytes already received and keeps the known total', () => {
		expect(applyProgress(DOWNLOADING, { kind: 'chunk', bytes: 37 })).toEqual({
			kind: 'downloading',
			version: '2.4.0',
			received: 137,
			total: 4096
		});
	});

	it.each([
		['available', AVAILABLE],
		['installing', INSTALLING],
		['ready', READY]
	])(
		'treats a chunk arriving in state %s as the first bytes of a download of unknown size',
		(_name, state: UpdateState) => {
			expect(applyProgress(state, { kind: 'chunk', bytes: 37 })).toEqual({
				kind: 'downloading',
				version: '2.4.0',
				received: 37,
				total: null
			});
		}
	);

	it.each([
		['available', AVAILABLE],
		['downloading', DOWNLOADING],
		['installing', INSTALLING],
		['ready', READY]
	])('moves on to installing when the download finishes in state %s', (_name, state) => {
		expect(applyProgress(state, { kind: 'finished' })).toEqual({
			kind: 'installing',
			version: '2.4.0'
		});
	});

	const EVENTS: ReadonlyArray<readonly [string, DownloadProgress]> = [
		['started', { kind: 'started', total: 4096 }],
		['chunk', { kind: 'chunk', bytes: 37 }],
		['finished', { kind: 'finished' }]
	];
	const STRAY = VERSIONLESS.flatMap(([stateName, state]) =>
		EVENTS.map(([eventName, event]) => [stateName, eventName, state, event] as const)
	);

	it.each(STRAY)(
		'ignores a %s event in state %s, because there is no version to download',
		(_stateName, _eventName, state: UpdateState, event: DownloadProgress) => {
			expect(applyProgress(state, event)).toBe(state);
		}
	);
});

describe('startupNotice', () => {
	it('announces the available version', () => {
		expect(startupNotice(AVAILABLE)).toBe('Version 2.4.0 is available.');
	});

	it.each([
		['idle', IDLE],
		['checking', CHECKING],
		['up-to-date', UP_TO_DATE],
		['downloading', DOWNLOADING],
		['installing', INSTALLING],
		['ready', READY],
		['failed', FAILED]
	])('stays silent in state %s so startup is never interrupted', (_name, state: UpdateState) => {
		expect(startupNotice(state)).toBe('');
	});
});

describe('runCheck', () => {
	it('announces the found version after announcing that it is looking', async () => {
		const { onState, seen } = recorder();
		const install = vi.fn();
		const deps: UpdateDeps = {
			check: vi.fn().mockResolvedValue({ version: '2.4.0', notes: 'Fixes the launcher' }),
			install,
			onState
		};

		const result = await runCheck(deps);

		expect(seen).toEqual([
			{ kind: 'checking' },
			{ kind: 'available', version: '2.4.0', notes: 'Fixes the launcher' }
		]);
		expect(result).toEqual({ kind: 'available', version: '2.4.0', notes: 'Fixes the launcher' });
		expect(install).not.toHaveBeenCalled();
	});

	it('reports the newest version when the feed offers nothing', async () => {
		const { onState, seen } = recorder();
		const deps: UpdateDeps = { check: vi.fn().mockResolvedValue(null), install: vi.fn(), onState };

		const result = await runCheck(deps);

		expect(seen).toEqual([{ kind: 'checking' }, { kind: 'up-to-date' }]);
		expect(result).toEqual({ kind: 'up-to-date' });
	});

	it('captures a failing check as state instead of throwing at the caller', async () => {
		const { onState, seen } = recorder();
		const deps: UpdateDeps = {
			check: vi.fn().mockRejectedValue(new Error('the release feed is unreachable')),
			install: vi.fn(),
			onState
		};

		const result = await runCheck(deps);

		expect(seen).toEqual([
			{ kind: 'checking' },
			{ kind: 'failed', message: 'the release feed is unreachable' }
		]);
		expect(result).toEqual({ kind: 'failed', message: 'the release feed is unreachable' });
	});

	it.each([
		['a string', 'the backend said no', 'the backend said no'],
		['a number', 404, '404']
	])(
		'renders a rejection that is not an Error (%s) as text',
		async (_name, cause: unknown, expected: string) => {
			const { onState, seen } = recorder();
			const deps: UpdateDeps = {
				check: vi.fn().mockRejectedValue(cause),
				install: vi.fn(),
				onState
			};

			const result = await runCheck(deps);

			expect(seen).toEqual([{ kind: 'checking' }, { kind: 'failed', message: expected }]);
			expect(result).toEqual({ kind: 'failed', message: expected });
		}
	);
});

describe('runInstall', () => {
	it('reports installing and then ready when nothing reports progress', async () => {
		const { onState, seen } = recorder();
		const deps: UpdateDeps = {
			check: vi.fn(),
			install: vi.fn().mockResolvedValue(undefined),
			onState
		};

		const result = await runInstall('2.4.0', deps);

		expect(seen).toEqual([
			{ kind: 'installing', version: '2.4.0' },
			{ kind: 'ready', version: '2.4.0' }
		]);
		expect(result).toEqual({ kind: 'ready', version: '2.4.0' });
	});

	it('turns the reported download events into a running byte count', async () => {
		const { onState, seen } = recorder();
		const install = vi.fn(async (onProgress: (progress: DownloadProgress) => void) => {
			onProgress({ kind: 'started', total: 4096 });
			onProgress({ kind: 'chunk', bytes: 1000 });
			onProgress({ kind: 'chunk', bytes: 37 });
			onProgress({ kind: 'finished' });
		});
		const deps: UpdateDeps = { check: vi.fn(), install, onState };

		const result = await runInstall('2.4.0', deps);

		expect(seen).toEqual([
			{ kind: 'installing', version: '2.4.0' },
			{ kind: 'downloading', version: '2.4.0', received: 0, total: 4096 },
			{ kind: 'downloading', version: '2.4.0', received: 1000, total: 4096 },
			{ kind: 'downloading', version: '2.4.0', received: 1037, total: 4096 },
			{ kind: 'installing', version: '2.4.0' },
			{ kind: 'ready', version: '2.4.0' }
		]);
		expect(result).toEqual({ kind: 'ready', version: '2.4.0' });
	});

	it('captures a failing install as state, keeping the progress already reported', async () => {
		const { onState, seen } = recorder();
		const install = vi.fn(async (onProgress: (progress: DownloadProgress) => void) => {
			onProgress({ kind: 'started', total: 4096 });
			throw new Error('invalid updater binary format');
		});
		const deps: UpdateDeps = { check: vi.fn(), install, onState };

		const result = await runInstall('2.4.0', deps);

		expect(seen).toEqual([
			{ kind: 'installing', version: '2.4.0' },
			{ kind: 'downloading', version: '2.4.0', received: 0, total: 4096 },
			{ kind: 'failed', message: 'invalid updater binary format' }
		]);
		expect(result).toEqual({ kind: 'failed', message: 'invalid updater binary format' });
	});

	it.each([
		['a string', 'the installer refused', 'the installer refused'],
		['a number', 500, '500']
	])(
		'renders an install rejection that is not an Error (%s) as text',
		async (_name, cause: unknown, expected: string) => {
			const { onState, seen } = recorder();
			const deps: UpdateDeps = {
				check: vi.fn(),
				install: vi.fn().mockRejectedValue(cause),
				onState
			};

			const result = await runInstall('2.4.0', deps);

			expect(seen).toEqual([
				{ kind: 'installing', version: '2.4.0' },
				{ kind: 'failed', message: expected }
			]);
			expect(result).toEqual({ kind: 'failed', message: expected });
		}
	);
});
