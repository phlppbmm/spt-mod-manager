import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	DISMISSED_RELEASE_KEY,
	canApply,
	compareVersions,
	confirmBlocked,
	confirmationFor,
	formatReleaseDate,
	loadDismissedRelease,
	modCheckView,
	needsModCheck,
	noticeFor,
	releaseState,
	saveDismissedRelease
} from './spt';
import type { ChangeKind } from './spt';
import type { SptCompatibilityReport, SptReleaseInfo, SptReleaseList } from './api/client';

/**
 * The release rows come from the RPC schema, but only `version` and
 * `downloadable` steer the decisions in `spt.ts`; the remaining fields are
 * filled with fixed placeholders so a test states just what it varies.
 */
function makeRelease(overrides: Partial<SptReleaseInfo> = {}): SptReleaseInfo {
	return {
		version: '4.1.3',
		client_build: '39000',
		published_at: '2026-01-02T03:04:05Z',
		url: 'https://sp-tarkov.com/releases/4.1.3',
		downloadable: true,
		content_length: 1024,
		installed: false,
		newer: true,
		...overrides
	};
}

function makeList(overrides: Partial<SptReleaseList> = {}): SptReleaseList {
	return {
		current_version: '4.1.0',
		client_build: '38000',
		releases: [],
		latest: '4.1.3',
		source_ok: true,
		notice: '',
		...overrides
	};
}

/**
 * `spt.ts` reads and writes `localStorage`, which Vitest's `node` environment
 * does not provide. A memory stand-in is the allowed system-edge stub here, as
 * in `theme.test.ts`.
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

/** A browser that refuses storage access, as private-mode browsers do. */
function createFailingStorage(): Storage {
	const storage = createMemoryStorage();
	return {
		...storage,
		getItem: () => {
			throw new Error('storage is blocked');
		},
		setItem: () => {
			throw new Error('storage is blocked');
		},
		get length() {
			return 0;
		}
	};
}

const GIT_WORDS = ['commit', 'revert', 'repository', 'branch', 'git'];

beforeEach(() => {
	vi.stubGlobal('localStorage', createMemoryStorage());
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('compareVersions', () => {
	it.each([
		{ id: 'a smaller patch', a: '4.1.2', b: '4.1.3', expected: -1 },
		{ id: 'a larger patch', a: '4.1.4', b: '4.1.3', expected: 1 },
		{ id: 'a smaller minor beating a larger patch', a: '4.0.9', b: '4.1.0', expected: -1 },
		{ id: 'a larger major beating a smaller minor', a: '5.0.0', b: '4.9.9', expected: 1 },
		{ id: 'two identical versions', a: '4.1.3', b: '4.1.3', expected: 0 }
	])('orders $id', ({ a, b, expected }) => {
		expect(compareVersions(a, b)).toBe(expected);
	});

	it.each([
		{ id: 'a missing trailing part as zero, so the shorter one sorts first', a: '4.1', b: '4.1.2', expected: -1 },
		{ id: 'the longer version as larger', a: '4.1.2', b: '4.1', expected: 1 },
		{ id: 'a trailing zero as equal to no part at all', a: '4.1.0', b: '4.1', expected: 0 }
	])('treats $id', ({ a, b, expected }) => {
		expect(compareVersions(a, b)).toBe(expected);
	});

	it.each([
		{ id: 'a pre-release suffix', a: '4.1.x', b: '4.1.0', expected: 0 },
		{ id: 'a wholly unreadable version', a: 'unknown', b: '0.0.0', expected: 0 },
		{ id: 'text losing against a real number', a: 'nightly', b: '1', expected: -1 },
		{ id: 'a real number beating text', a: '1', b: 'nightly', expected: 1 }
	])('counts $id as zero', ({ a, b, expected }) => {
		expect(compareVersions(a, b)).toBe(expected);
	});

	it('reads a leading number out of a mixed part', () => {
		expect(compareVersions('4.1.3-BE', '4.1.2')).toBe(1);
	});
});

describe('releaseState', () => {
	it.each([
		{ id: 'installed', version: '4.1.0', expected: 'installed' },
		{ id: 'newer', version: '4.1.3', expected: 'newer' },
		{ id: 'older', version: '4.0.1', expected: 'older' }
	])('reports $id for version $version against 4.1.0', ({ version, expected }) => {
		expect(releaseState(makeRelease({ version }), '4.1.0')).toBe(expected);
	});

	it('ignores the row\'s own installed flag and compares the versions', () => {
		const release = makeRelease({ version: '4.1.0', installed: false, newer: true });

		expect(releaseState(release, '4.1.0')).toBe('installed');
	});
});

describe('canApply', () => {
	it('refuses the release that is already installed and names its version', () => {
		const check = canApply(makeRelease({ version: '4.1.0' }), '4.1.0');

		expect(check).toEqual({ ok: false, reason: 'SPT 4.1.0 is already installed.' });
	});

	it('reports the installed version even when that release lost its download', () => {
		const check = canApply(makeRelease({ version: '4.1.0', downloadable: false }), '4.1.0');

		expect(check).toEqual({ ok: false, reason: 'SPT 4.1.0 is already installed.' });
	});

	it.each([
		{ id: 'an older', version: '4.0.1' },
		{ id: 'a newer', version: '4.2.0' }
	])('points $id release without a download at the official installer', ({ version }) => {
		const check = canApply(makeRelease({ version, downloadable: false }), '4.1.0');

		expect(check).toEqual({
			ok: false,
			reason:
				`SPT no longer offers a download for ${version}. The official installer at ` +
				'https://sp-mod.com/installer can still change the game to it.'
		});
	});

	it.each([
		{ id: 'an update', version: '4.2.0' },
		{ id: 'a downgrade', version: '4.0.1' }
	])('allows $id to a downloadable release without a reason', ({ version }) => {
		expect(canApply(makeRelease({ version, downloadable: true }), '4.1.0')).toEqual({
			ok: true,
			reason: ''
		});
	});
});

describe('noticeFor', () => {
	it('announces the newer release and the version in use', () => {
		const list = makeList({ current_version: '4.1.0', latest: '4.1.3' });

		expect(noticeFor(list, null)).toBe('SPT 4.1.3 is out. You are on 4.1.0.');
	});

	it('still announces a release the user dismissed earlier under another version', () => {
		const list = makeList({ current_version: '4.1.0', latest: '4.1.3' });

		expect(noticeFor(list, '4.1.2')).toBe('SPT 4.1.3 is out. You are on 4.1.0.');
	});

	it.each([
		{
			id: 'the latest release is unknown',
			list: makeList({ latest: '' }),
			dismissed: null
		},
		{
			id: 'the installed version is unknown',
			list: makeList({ current_version: '' }),
			dismissed: null
		},
		{
			id: 'the latest release is the installed one',
			list: makeList({ current_version: '4.1.3', latest: '4.1.3' }),
			dismissed: null
		},
		{
			id: 'the installed version is ahead of the latest release',
			list: makeList({ current_version: '4.2.0', latest: '4.1.3' }),
			dismissed: null
		},
		{
			id: 'the user dismissed exactly this release',
			list: makeList({ current_version: '4.1.0', latest: '4.1.3' }),
			dismissed: '4.1.3'
		}
	])('stays silent when $id', ({ list, dismissed }) => {
		expect(noticeFor(list, dismissed)).toBeNull();
	});
});

describe('confirmationFor', () => {
	it('asks about the update target by version', () => {
		const confirmation = confirmationFor(makeRelease({ version: '4.1.3' }), '4.1.0', 'update');

		expect(confirmation.title).toBe('Update to SPT 4.1.3?');
	});

	it('asks about a downgrade as going back to that version', () => {
		const confirmation = confirmationFor(makeRelease({ version: '4.0.1' }), '4.1.0', 'downgrade');

		expect(confirmation.title).toBe('Go back to SPT 4.0.1?');
	});

	it('asks about an undo without naming any version', () => {
		const confirmation = confirmationFor(makeRelease({ version: '4.1.3' }), '4.1.0', 'undo');

		expect(confirmation.title).toBe('Undo the last SPT change?');
	});

	it.each([
		{ id: 'an update', kind: 'update' as ChangeKind, expected: 'Update to another SPT version?' },
		{
			id: 'a downgrade',
			kind: 'downgrade' as ChangeKind,
			expected: 'Go back to another SPT version?'
		}
	])('falls back to a nameless target for $id without a release', ({ kind, expected }) => {
		expect(confirmationFor(null, '4.1.0', kind).title).toBe(expected);
	});

	it('promises the way back to the installed version and warns about mods', () => {
		const confirmation = confirmationFor(makeRelease({ version: '4.1.3' }), '4.1.0', 'update');

		expect(confirmation.body).toBe(
			'Your profiles, settings and installed mods stay where they are. ' +
				'sptmod secures the current state of your game folder first, so you can go back to ' +
				'SPT 4.1.0 at any time from this page. ' +
				'Mods built for SPT 4.1.0 may stop working — check the Updates tab afterwards.'
		);
	});

	it('describes the version in use in plain words when it is unknown', () => {
		const confirmation = confirmationFor(makeRelease({ version: '4.1.3' }), '', 'update');

		expect(confirmation.body).toBe(
			'Your profiles, settings and installed mods stay where they are. ' +
				'sptmod secures the current state of your game folder first, so you can go back to ' +
				'the version you have now at any time from this page. ' +
				'Mods built for the version you have now may stop working — check the Updates tab afterwards.'
		);
	});

	it('explains an undo as the game folder returning to its previous state', () => {
		const confirmation = confirmationFor(null, '4.1.3', 'undo');

		expect(confirmation.body).toBe(
			'Your game folder goes back to how it was before the last SPT change. ' +
				'Your profiles, settings and installed mods stay where they are. ' +
				'sptmod secures the current state of your game folder first, so you can go back to ' +
				'SPT 4.1.3 at any time from this page. ' +
				'Mods built for SPT 4.1.3 may stop working — check the Updates tab afterwards.'
		);
	});

	it('gives the downgrade the same explanation as the update', () => {
		const release = makeRelease({ version: '4.0.1' });

		expect(confirmationFor(release, '4.1.0', 'downgrade').body).toBe(
			confirmationFor(release, '4.1.0', 'update').body
		);
	});

	it.each([
		{ id: 'an update', release: makeRelease(), current: '4.1.0', kind: 'update' as ChangeKind },
		{
			id: 'a downgrade',
			release: makeRelease({ version: '4.0.1' }),
			current: '4.1.0',
			kind: 'downgrade' as ChangeKind
		},
		{ id: 'an undo', release: null, current: '', kind: 'undo' as ChangeKind }
	])('speaks about $id without version-control words', ({ release, current, kind }) => {
		const confirmation = confirmationFor(release, current, kind);
		const text = `${confirmation.title} ${confirmation.body}`.toLowerCase();

		expect(GIT_WORDS.filter((word) => text.includes(word))).toEqual([]);
	});
});

/**
 * The backend sends `datetime.min` for a release whose publication date it
 * could not read; that value arrives as year 1 in the ISO string.
 */
const UNKNOWN_DATE = '0001-01-01T00:00:00Z';

describe('formatReleaseDate', () => {
	it('shows a dash instead of the placeholder date for an unknown publication date', () => {
		expect(formatReleaseDate(UNKNOWN_DATE)).toBe('—');
	});

	it('shows a known date the way the running locale writes dates', () => {
		const published = '2026-01-02T03:04:05Z';

		expect(formatReleaseDate(published)).toBe(new Date(published).toLocaleDateString());
	});

	it('writes two different publication dates differently', () => {
		expect(formatReleaseDate('2026-01-02T12:00:00Z')).not.toBe(
			formatReleaseDate('2025-11-27T12:00:00Z')
		);
	});

	it.each([
		{ id: 'the year before the first date the backend can mean', published: '1999-06-15T12:00:00Z' },
		{ id: 'a date long before SPT existed', published: '1970-06-15T12:00:00Z' }
	])('treats $id as unknown', ({ published }) => {
		expect(formatReleaseDate(published)).toBe('—');
	});

	it('formats the first year the backend can mean instead of hiding it', () => {
		const published = '2000-06-15T12:00:00Z';

		expect(formatReleaseDate(published)).toBe(new Date(published).toLocaleDateString());
	});
});

describe('loadDismissedRelease', () => {
	it('returns the release the user dismissed', () => {
		localStorage.setItem(DISMISSED_RELEASE_KEY, '4.1.3');

		expect(loadDismissedRelease()).toBe('4.1.3');
	});

	it('returns nothing while no release has been dismissed', () => {
		expect(loadDismissedRelease()).toBeNull();
	});

	it('returns nothing when the browser denies storage access', () => {
		vi.stubGlobal('localStorage', createFailingStorage());

		expect(loadDismissedRelease()).toBeNull();
	});
});

describe('saveDismissedRelease', () => {
	it('remembers a release that a later notice then suppresses', () => {
		saveDismissedRelease('4.1.3');

		expect(noticeFor(makeList({ latest: '4.1.3' }), loadDismissedRelease())).toBeNull();
	});

	it('replaces an earlier dismissal so the older release is announced again', () => {
		saveDismissedRelease('4.1.2');
		saveDismissedRelease('4.1.3');

		expect(noticeFor(makeList({ latest: '4.1.2' }), loadDismissedRelease())).toBe(
			'SPT 4.1.2 is out. You are on 4.1.0.'
		);
	});

	it('gives up quietly when the browser denies storage access', () => {
		vi.stubGlobal('localStorage', createFailingStorage());

		expect(() => saveDismissedRelease('4.1.3')).not.toThrow();
	});
});

/**
 * The compatibility report comes from the RPC schema; a test states only the
 * fields it varies, so the builder fills a report that judged nothing.
 */
function makeReport(overrides: Partial<SptCompatibilityReport> = {}): SptCompatibilityReport {
	return {
		spt_version: '4.2.0',
		compatible: [],
		incompatible: [],
		skipped: [],
		checked: true,
		notice: '',
		...overrides
	};
}

describe('needsModCheck', () => {
	it.each([
		{ id: 'an update to a known release', kind: 'update' as ChangeKind },
		{ id: 'a downgrade to a known release', kind: 'downgrade' as ChangeKind }
	])('asks for the installed mods before $id', ({ kind }) => {
		expect(needsModCheck(makeRelease({ version: '4.2.0' }), kind)).toBe(true);
	});

	it.each([
		{ id: 'an undo, which has no target version to judge', release: makeRelease(), kind: 'undo' as ChangeKind },
		{ id: 'an undo without a release at all', release: null, kind: 'undo' as ChangeKind },
		{ id: 'an update without a release to check against', release: null, kind: 'update' as ChangeKind },
		{
			id: 'a downgrade without a release to check against',
			release: null,
			kind: 'downgrade' as ChangeKind
		}
	])('skips the check for $id', ({ release, kind }) => {
		expect(needsModCheck(release, kind)).toBe(false);
	});
});

describe('modCheckView', () => {
	it('says the check is running and marks the verdict as not yet arrived', () => {
		expect(modCheckView({ kind: 'pending' })).toEqual({
			tone: 'note',
			message: 'Checking your installed mods against this version…',
			incompatible: [],
			skippedNote: '',
			needsOverride: false,
			pending: true
		});
	});

	it('lets the change go ahead when the check itself failed, and says so', () => {
		expect(modCheckView({ kind: 'failed' })).toEqual({
			tone: 'note',
			message:
				'Your installed mods could not be checked against this version. ' +
				'The change can still go ahead.',
			incompatible: [],
			skippedNote: '',
			needsOverride: false,
			pending: false
		});
	});

	it('repeats the reason the backend gave for not checking and still allows the change', () => {
		const report = makeReport({ checked: false, notice: 'The mod list was never downloaded.' });

		expect(modCheckView({ kind: 'ready', report })).toEqual({
			tone: 'note',
			message: 'The mod list was never downloaded. The change can still go ahead.',
			incompatible: [],
			skippedNote: '',
			needsOverride: false,
			pending: false
		});
	});

	it('falls back to its own wording when the backend gave no reason', () => {
		const report = makeReport({ checked: false, notice: '' });

		expect(modCheckView({ kind: 'ready', report })).toEqual({
			tone: 'note',
			message:
				'Your installed mods could not be checked against this version. ' +
				'The change can still go ahead.',
			incompatible: [],
			skippedNote: '',
			needsOverride: false,
			pending: false
		});
	});

	it('ignores mods it could not judge while the check itself did not run', () => {
		const report = makeReport({ checked: false, skipped: ['SAIN'], incompatible: ['Looting Bots'] });

		expect(modCheckView({ kind: 'ready', report })).toEqual({
			tone: 'note',
			message:
				'Your installed mods could not be checked against this version. ' +
				'The change can still go ahead.',
			incompatible: [],
			skippedNote: '',
			needsOverride: false,
			pending: false
		});
	});

	it('warns about the one mod without a release and names it', () => {
		const report = makeReport({ spt_version: '4.2.0', compatible: ['SAIN'], incompatible: ['SVM'] });

		expect(modCheckView({ kind: 'ready', report })).toEqual({
			tone: 'warn',
			message: '1 installed mod has no release for SPT 4.2.0:',
			incompatible: ['SVM'],
			skippedNote: '',
			needsOverride: true,
			pending: false
		});
	});

	it('warns about several mods without a release and names every one of them', () => {
		const report = makeReport({ spt_version: '4.0.1', incompatible: ['SVM', 'Fika', 'Looting Bots'] });

		expect(modCheckView({ kind: 'ready', report })).toEqual({
			tone: 'warn',
			message: '3 installed mods have no release for SPT 4.0.1:',
			incompatible: ['SVM', 'Fika', 'Looting Bots'],
			skippedNote: '',
			needsOverride: true,
			pending: false
		});
	});

	it('reports that every installed mod has a release, without holding the change back', () => {
		const report = makeReport({ spt_version: '4.2.0', compatible: ['SAIN', 'SVM'] });

		expect(modCheckView({ kind: 'ready', report })).toEqual({
			tone: 'note',
			message: '2 installed mods have a release for SPT 4.2.0.',
			incompatible: [],
			skippedNote: '',
			needsOverride: false,
			pending: false
		});
	});

	it('reports a single compatible mod in the singular', () => {
		const report = makeReport({ spt_version: '4.2.0', compatible: ['SAIN'] });

		expect(modCheckView({ kind: 'ready', report }).message).toBe(
			'1 installed mod has a release for SPT 4.2.0.'
		);
	});

	it('says there was nothing to judge when no mod fell into either outcome', () => {
		const report = makeReport({ spt_version: '4.2.0' });

		expect(modCheckView({ kind: 'ready', report })).toEqual({
			tone: 'note',
			message: 'No installed mods to check against SPT 4.2.0.',
			incompatible: [],
			skippedNote: '',
			needsOverride: false,
			pending: false
		});
	});

	it.each([
		{
			id: 'alongside mods without a release',
			report: makeReport({ incompatible: ['SVM'], skipped: ['Fika'] }),
			expectedNote: '1 installed mod could not be checked: Fika.'
		},
		{
			id: 'alongside mods with a release',
			report: makeReport({ compatible: ['SAIN'], skipped: ['Fika', 'Waypoints'] }),
			expectedNote: '2 installed mods could not be checked: Fika, Waypoints.'
		},
		{
			id: 'as the only mods in the report',
			report: makeReport({ skipped: ['Fika'] }),
			expectedNote: '1 installed mod could not be checked: Fika.'
		}
	])('names the mods it could not judge $id', ({ report, expectedNote }) => {
		expect(modCheckView({ kind: 'ready', report }).skippedNote).toBe(expectedNote);
	});

	it.each([
		{ id: 'mods without a release', report: makeReport({ incompatible: ['SVM'] }) },
		{ id: 'mods with a release', report: makeReport({ compatible: ['SAIN'] }) },
		{ id: 'no mods at all', report: makeReport() }
	])('leaves the unjudged sentence empty for a report with $id', ({ report }) => {
		expect(modCheckView({ kind: 'ready', report }).skippedNote).toBe('');
	});

	it('never holds the change back for mods it could not judge', () => {
		const report = makeReport({ compatible: ['SAIN'], skipped: ['Fika', 'Waypoints'] });

		expect(modCheckView({ kind: 'ready', report }).needsOverride).toBe(false);
	});
});

describe('confirmBlocked', () => {
	it('lets the change through while no verdict has arrived yet', () => {
		expect(confirmBlocked(null, false)).toBe(false);
	});

	it('holds the change back while mods without a release are unacknowledged', () => {
		const view = modCheckView({ kind: 'ready', report: makeReport({ incompatible: ['SVM'] }) });

		expect(confirmBlocked(view, false)).toBe(true);
	});

	it('releases the change once the player says to go ahead anyway', () => {
		const view = modCheckView({ kind: 'ready', report: makeReport({ incompatible: ['SVM'] }) });

		expect(confirmBlocked(view, true)).toBe(false);
	});

	it.each([
		{ id: 'every mod has a release', report: makeReport({ compatible: ['SAIN'] }) },
		{ id: 'the only finding is mods it could not judge', report: makeReport({ skipped: ['Fika'] }) },
		{ id: 'the check could not run', report: makeReport({ checked: false }) }
	])('never blocks when $id', ({ report }) => {
		expect(confirmBlocked(modCheckView({ kind: 'ready', report }), false)).toBe(false);
	});

	it('never blocks once the check failed', () => {
		expect(confirmBlocked(modCheckView({ kind: 'failed' }), false)).toBe(false);
	});

	it.each([
		{ id: 'nothing has been acknowledged', allowed: false },
		{ id: 'the player already acknowledged incompatible mods', allowed: true }
	])('holds the change back while the check is still running and $id', ({ allowed }) => {
		expect(confirmBlocked(modCheckView({ kind: 'pending' }), allowed)).toBe(true);
	});

	it('stops blocking as soon as a clean verdict replaces the running check', () => {
		const running = modCheckView({ kind: 'pending' });
		const arrived = modCheckView({ kind: 'ready', report: makeReport({ compatible: ['SAIN'] }) });

		expect([confirmBlocked(running, false), confirmBlocked(arrived, false)]).toEqual([true, false]);
	});
});
