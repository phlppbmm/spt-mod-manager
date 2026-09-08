export type DownloadProgress =
	| { kind: 'started'; total: number | null }
	| { kind: 'chunk'; bytes: number }
	| { kind: 'finished' };

export type UpdateState =
	| { kind: 'idle' }
	| { kind: 'checking' }
	| { kind: 'up-to-date' }
	| { kind: 'available'; version: string; notes: string }
	| { kind: 'downloading'; version: string; received: number; total: number | null }
	| { kind: 'installing'; version: string }
	| { kind: 'ready'; version: string }
	| { kind: 'failed'; message: string };

const DEB_HINT =
	'Automatic updates are only available in the AppImage build — download the new version from the releases page.';

const GENERIC_FAILURE = 'The update could not be downloaded. Try again later.';

/**
 * The updater plugin reports raw Rust error text. Each known pattern gets a
 * plain sentence; anything unrecognised falls back to the generic message so
 * that internal detail never reaches the window.
 *
 * The package mismatch covers a .deb install, which downloads the AppImage
 * artifact and then refuses it, because the bundler only signs AppImage, msi
 * and nsis packages.
 */
const FAILURE_MESSAGES: ReadonlyArray<readonly [string, string]> = [
	['invalid updater binary format', DEB_HINT],
	[
		'could not fetch a valid release json from the remote',
		'The update server could not be reached, so there is no release information available.'
	],
	[
		'found in the response `platforms` object',
		"No update is available for this computer's system."
	],
	['signature', 'The update could not be verified.'],
	['could not be decoded', 'The update could not be verified.'],
	['must use a secure protocol', 'The update could not be verified.']
];

function explainFailure(message: string): string {
	const text = message.toLowerCase();
	const match = FAILURE_MESSAGES.find(([pattern]) => text.includes(pattern));
	return match === undefined ? GENERIC_FAILURE : match[1];
}

export function describe(state: UpdateState): string {
	switch (state.kind) {
		case 'idle':
			return '';
		case 'checking':
			return 'Looking for a new version…';
		case 'up-to-date':
			return 'This is the newest version.';
		case 'available':
			return `Version ${state.version} is available.`;
		case 'downloading':
			return `Downloading version ${state.version}…`;
		case 'installing':
			return `Installing version ${state.version}…`;
		case 'ready':
			return `Version ${state.version} is installed. Restart sptmod to use it.`;
		case 'failed':
			return explainFailure(state.message);
	}
}

export function progressPercent(state: UpdateState): number | null {
	if (state.kind !== 'downloading' || state.total === null || state.total <= 0) {
		return null;
	}
	return Math.min(100, Math.round((state.received / state.total) * 100));
}

export function applyProgress(state: UpdateState, event: DownloadProgress): UpdateState {
	const version = versionOf(state);
	if (version === null) {
		return state;
	}
	if (event.kind === 'started') {
		return { kind: 'downloading', version, received: 0, total: event.total };
	}
	if (event.kind === 'finished') {
		return { kind: 'installing', version };
	}
	const received = state.kind === 'downloading' ? state.received + event.bytes : event.bytes;
	const total = state.kind === 'downloading' ? state.total : null;
	return { kind: 'downloading', version, received, total };
}

function versionOf(state: UpdateState): string | null {
	switch (state.kind) {
		case 'available':
		case 'downloading':
		case 'installing':
		case 'ready':
			return state.version;
		default:
			return null;
	}
}

export function startupNotice(state: UpdateState): string {
	if (state.kind !== 'available') {
		return '';
	}
	return `Version ${state.version} is available.`;
}

export interface UpdateDeps {
	check: () => Promise<{ version: string; notes: string } | null>;
	install: (onProgress: (progress: DownloadProgress) => void) => Promise<void>;
	onState: (state: UpdateState) => void;
}

/**
 * Every failure is reported as state, never thrown: a check that cannot reach
 * the release feed must not break the page that started it.
 */
export async function runCheck(deps: UpdateDeps): Promise<UpdateState> {
	deps.onState({ kind: 'checking' });
	let state: UpdateState;
	try {
		const found = await deps.check();
		state =
			found === null
				? { kind: 'up-to-date' }
				: { kind: 'available', version: found.version, notes: found.notes };
	} catch (cause) {
		state = { kind: 'failed', message: messageOf(cause) };
	}
	deps.onState(state);
	return state;
}

export async function runInstall(version: string, deps: UpdateDeps): Promise<UpdateState> {
	let state: UpdateState = { kind: 'installing', version };
	deps.onState(state);
	try {
		await deps.install((progress) => {
			state = applyProgress(state, progress);
			deps.onState(state);
		});
		state = { kind: 'ready', version };
	} catch (cause) {
		state = { kind: 'failed', message: messageOf(cause) };
	}
	deps.onState(state);
	return state;
}

function messageOf(cause: unknown): string {
	return cause instanceof Error ? cause.message : String(cause);
}
