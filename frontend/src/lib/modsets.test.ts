import { describe, expect, it } from 'vitest';
import {
	NO_SET_LABEL,
	boundSetName,
	entriesFromMods,
	entryRows,
	exportFileName,
	findSet,
	installTargets,
	missingFromError,
	planSummary,
	setOptions,
	toggleEntry,
	type EntryStatus
} from './modsets';
import type {
	ModInfo,
	ModSet,
	ModSetChange,
	ModSetEntry,
	ModSetOverview,
	ModSetPlan,
	ProfileBinding
} from './api/client';
import { ApiError, type ErrorDetails } from './api/transport';

function makeMod(overrides: Partial<ModInfo> = {}): ModInfo {
	return {
		name: 'some-mod',
		version: '1.0.0',
		mod_type: 'client',
		author: 'Author',
		spt_range: '~3.9.0',
		state: 'installed',
		commit: 'abc1234',
		date: '2026-01-01T00:00:00Z',
		forge_id: '',
		forge_version_id: '',
		guid: '',
		source: '',
		...overrides
	};
}

function makeEntry(overrides: Partial<ModSetEntry> = {}): ModSetEntry {
	return { forge_id: '', name: 'some-mod', version: '', enabled: true, ...overrides };
}

function makeSet(name: string, entries: ModSetEntry[]): ModSet {
	return { name, created_at: '2026-01-01T00:00:00Z', entries };
}

function makeOverview(sets: ModSet[], bindings: ProfileBinding[]): ModSetOverview {
	return { sets, bindings };
}

function makeChange(name: string): ModSetChange {
	return { name, version: '1.0.0', installed_version: '1.0.0', reason: '' };
}

function makePlan(counts: { enable: number; disable: number; missing: number }): ModSetPlan {
	return {
		name: 'Set A',
		enable: Array.from({ length: counts.enable }, (_unused, index) => makeChange(`on-${index}`)),
		disable: Array.from({ length: counts.disable }, (_unused, index) =>
			makeChange(`off-${index}`)
		),
		missing: Array.from({ length: counts.missing }, (_unused, index) =>
			makeEntry({ name: `gone-${index}` })
		),
		different: [],
		unchanged: []
	};
}

const ALL_DETAILS: ErrorDetails = {
	conflicts: [],
	conflicting_paths: [],
	residues: [],
	installed: [],
	name: '',
	candidates: [],
	dependency: '',
	reason: '',
	api_code: '',
	api_status: 0,
	missing: ['SAIN', 'Looting Bots']
};

describe('entriesFromMods', () => {
	it('turns the installation into entries, keeping the enabled flag of each state', () => {
		const mods = [
			makeMod({ name: 'a-installed', version: '1.1.1', state: 'installed', forge_id: '11' }),
			makeMod({ name: 'b-updated', version: '2.2.2', state: 'updated', forge_id: '22' }),
			makeMod({ name: 'c-enabled', version: '3.3.3', state: 'enabled', forge_id: '33' }),
			makeMod({ name: 'd-disabled', version: '4.4.4', state: 'disabled', forge_id: '44' })
		];

		expect(entriesFromMods(mods)).toEqual([
			{ forge_id: '11', name: 'a-installed', version: '1.1.1', enabled: true },
			{ forge_id: '22', name: 'b-updated', version: '2.2.2', enabled: true },
			{ forge_id: '33', name: 'c-enabled', version: '3.3.3', enabled: true },
			{ forge_id: '44', name: 'd-disabled', version: '4.4.4', enabled: false }
		]);
	});

	it('leaves a removed mod out, because a set cannot turn back on what is gone', () => {
		const mods = [
			makeMod({ name: 'kept', state: 'disabled' }),
			makeMod({ name: 'gone', state: 'removed' })
		];

		expect(entriesFromMods(mods).map((entry) => entry.name)).toEqual(['kept']);
	});

	it('sorts by name ignoring case, so an upper-case name does not jump to the front', () => {
		const mods = [
			makeMod({ name: 'zeta' }),
			makeMod({ name: 'Beta' }),
			makeMod({ name: 'alpha' })
		];

		expect(entriesFromMods(mods).map((entry) => entry.name)).toEqual(['alpha', 'Beta', 'zeta']);
	});

	it('returns an empty list for an installation without mods', () => {
		expect(entriesFromMods([])).toEqual([]);
	});
});

describe('entryRows', () => {
	it('returns no rows when no set is selected', () => {
		expect(entryRows(null, [makeMod()])).toEqual([]);
	});

	it('returns no rows for a set without entries', () => {
		expect(entryRows(makeSet('Empty', []), [makeMod()])).toEqual([]);
	});

	it('matches by Forge id first, even when another mod carries the entry name', () => {
		const entry = makeEntry({ forge_id: '47', name: 'trailer-name' });
		const mods = [
			makeMod({ forge_id: '99', name: 'trailer-name', version: '9.9.9', state: 'disabled' }),
			makeMod({ forge_id: '47', name: 'forge-display', version: '2.0.0', state: 'enabled' })
		];

		expect(entryRows(makeSet('Set A', [entry]), mods)).toEqual([
			{ entry, installed: true, installedVersion: '2.0.0', status: 'on' }
		]);
	});

	it('falls back to the name when no installed mod carries the entry Forge id', () => {
		const entry = makeEntry({ forge_id: '47', name: 'trailer-name' });
		const mods = [makeMod({ forge_id: '99', name: 'TRAILER-NAME', version: '9.9.9' })];

		expect(entryRows(makeSet('Set A', [entry]), mods)).toEqual([
			{ entry, installed: true, installedVersion: '9.9.9', status: 'on' }
		]);
	});

	it('matches an entry without a Forge id by name, not by the empty Forge id', () => {
		const entry = makeEntry({ forge_id: '', name: 'wanted' });
		const mods = [
			makeMod({ forge_id: '', name: 'other-mod', version: '1.1.1' }),
			makeMod({ forge_id: '5', name: 'wanted', version: '3.3.3' })
		];

		expect(entryRows(makeSet('Set A', [entry]), mods)).toEqual([
			{ entry, installed: true, installedVersion: '3.3.3', status: 'on' }
		]);
	});

	it('reports a mod that is not installed at all as missing', () => {
		const entry = makeEntry({ forge_id: '47', name: 'wanted', version: '1.0.0' });
		const mods = [makeMod({ forge_id: '99', name: 'something-else', version: '9.9.9' })];

		expect(entryRows(makeSet('Set A', [entry]), mods)).toEqual([
			{ entry, installed: false, installedVersion: '', status: 'missing' }
		]);
	});

	it('reports a removed mod as missing, hiding its leftover version', () => {
		const entry = makeEntry({ forge_id: '47', name: 'wanted' });
		const mods = [
			makeMod({ forge_id: '47', name: 'wanted', version: '9.9.9', state: 'removed' })
		];

		expect(entryRows(makeSet('Set A', [entry]), mods)).toEqual([
			{ entry, installed: false, installedVersion: '', status: 'missing' }
		]);
	});

	it('keeps one row per entry, in the order the set lists them', () => {
		const entries = [
			makeEntry({ name: 'second' }),
			makeEntry({ name: 'first' }),
			makeEntry({ name: 'third' })
		];
		const mods = [makeMod({ name: 'first' })];

		expect(entryRows(makeSet('Set A', entries), mods).map((row) => row.entry.name)).toEqual([
			'second',
			'first',
			'third'
		]);
	});

	it.each<{ id: string; entryVersion: string; state: ModInfo['state']; status: EntryStatus }>([
		{ id: 'same version, installed', entryVersion: '2.0.0', state: 'installed', status: 'on' },
		{ id: 'same version, updated', entryVersion: '2.0.0', state: 'updated', status: 'on' },
		{ id: 'same version, enabled', entryVersion: '2.0.0', state: 'enabled', status: 'on' },
		{ id: 'same version, disabled', entryVersion: '2.0.0', state: 'disabled', status: 'off' },
		{ id: 'no recorded version, enabled', entryVersion: '', state: 'enabled', status: 'on' },
		{ id: 'no recorded version, disabled', entryVersion: '', state: 'disabled', status: 'off' },
		{
			id: 'other version, enabled',
			entryVersion: '1.0.0',
			state: 'enabled',
			status: 'version'
		},
		{
			id: 'other version, disabled',
			entryVersion: '1.0.0',
			state: 'disabled',
			status: 'off'
		}
	])('reports status $status for an entry with $id', ({ entryVersion, state, status }) => {
		const entry = makeEntry({ forge_id: '47', name: 'wanted', version: entryVersion });
		const mods = [makeMod({ forge_id: '47', name: 'wanted', version: '2.0.0', state })];

		expect(entryRows(makeSet('Set A', [entry]), mods)).toEqual([
			{ entry, installed: true, installedVersion: '2.0.0', status }
		]);
	});

	it.each<{ id: string; entryVersion: string; state: ModInfo['state'] }>([
		{ id: 'runs in another version', entryVersion: '1.0.0', state: 'enabled' },
		{ id: 'runs in the named version', entryVersion: '2.0.0', state: 'enabled' },
		{ id: 'carries no recorded version', entryVersion: '', state: 'installed' }
	])('reports an entry the set turns off as off, even though the mod $id', ({ entryVersion, state }) => {
		const entry = makeEntry({
			forge_id: '47',
			name: 'wanted',
			version: entryVersion,
			enabled: false
		});
		const mods = [makeMod({ forge_id: '47', name: 'wanted', version: '2.0.0', state })];

		expect(entryRows(makeSet('Set A', [entry]), mods)).toEqual([
			{ entry, installed: true, installedVersion: '2.0.0', status: 'off' }
		]);
	});
});

describe('toggleEntry', () => {
	it.each([
		{ id: 'an enabled entry off', enabled: true, expected: false },
		{ id: 'a disabled entry on', enabled: false, expected: true }
	])('flips $id', ({ enabled, expected }) => {
		const entries = [makeEntry({ name: 'target', enabled })];

		expect(toggleEntry(entries, 'target')).toEqual([
			{ forge_id: '', name: 'target', version: '', enabled: expected }
		]);
	});

	it('leaves every other entry as it is', () => {
		const entries = [
			makeEntry({ name: 'before', enabled: true }),
			makeEntry({ name: 'target', enabled: true }),
			makeEntry({ name: 'after', enabled: false })
		];

		expect(toggleEntry(entries, 'target').map((entry) => entry.enabled)).toEqual([
			true,
			false,
			false
		]);
	});

	it('changes nothing when no entry carries the given name', () => {
		const entries = [
			makeEntry({ name: 'alpha', enabled: true }),
			makeEntry({ name: 'beta', enabled: false })
		];

		expect(toggleEntry(entries, 'gamma')).toEqual(entries);
	});
});

describe('setOptions', () => {
	it('offers "No set" as the selected option when the profile has no binding', () => {
		const overview = makeOverview(
			[makeSet('Hardcore', []), makeSet('Vanilla', [])],
			[{ profile_id: 'other-profile', set_name: 'Hardcore' }]
		);

		expect(setOptions(overview, 'profile-1')).toEqual([
			{ value: '', label: NO_SET_LABEL, selected: true },
			{ value: 'Hardcore', label: 'Hardcore', selected: false },
			{ value: 'Vanilla', label: 'Vanilla', selected: false }
		]);
	});

	it('selects the bound set and ignores case in the stored binding', () => {
		const overview = makeOverview(
			[makeSet('Hardcore', []), makeSet('Vanilla', [])],
			[{ profile_id: 'profile-1', set_name: 'HARDCORE' }]
		);

		expect(setOptions(overview, 'profile-1')).toEqual([
			{ value: '', label: NO_SET_LABEL, selected: false },
			{ value: 'Hardcore', label: 'Hardcore', selected: true },
			{ value: 'Vanilla', label: 'Vanilla', selected: false }
		]);
	});

	it('offers only "No set" when no set exists yet', () => {
		expect(setOptions(makeOverview([], []), 'profile-1')).toEqual([
			{ value: '', label: NO_SET_LABEL, selected: true }
		]);
	});
});

describe('boundSetName', () => {
	it('returns the set bound to the given profile', () => {
		const overview = makeOverview(
			[],
			[
				{ profile_id: 'profile-1', set_name: 'Hardcore' },
				{ profile_id: 'profile-2', set_name: 'Vanilla' }
			]
		);

		expect(boundSetName(overview, 'profile-2')).toBe('Vanilla');
	});

	it('returns null when other profiles are bound but this one is not', () => {
		const overview = makeOverview([], [{ profile_id: 'profile-1', set_name: 'Hardcore' }]);

		expect(boundSetName(overview, 'profile-2')).toBeNull();
	});

	it('returns null when nothing is bound at all', () => {
		expect(boundSetName(makeOverview([], []), 'profile-1')).toBeNull();
	});
});

describe('planSummary', () => {
	it.each([
		{ counts: { enable: 0, disable: 0, missing: 0 }, expected: 'Nothing to change.' },
		{ counts: { enable: 1, disable: 0, missing: 0 }, expected: '1 mod on.' },
		{ counts: { enable: 3, disable: 0, missing: 0 }, expected: '3 mods on.' },
		{ counts: { enable: 0, disable: 1, missing: 0 }, expected: '1 mod off.' },
		{ counts: { enable: 0, disable: 0, missing: 2 }, expected: '2 mods missing.' },
		{ counts: { enable: 0, disable: 2, missing: 3 }, expected: '2 mods off, 3 missing.' },
		{ counts: { enable: 2, disable: 1, missing: 4 }, expected: '2 mods on, 1 off, 4 missing.' }
	])('summarises $counts as "$expected"', ({ counts, expected }) => {
		expect(planSummary(makePlan(counts))).toBe(expected);
	});
});

describe('missingFromError', () => {
	it('returns the mods an incomplete set named as missing', () => {
		const error = new ApiError('Set is incomplete.', 'MOD_SET_INCOMPLETE', ALL_DETAILS);

		expect(missingFromError(error)).toEqual(['SAIN', 'Looting Bots']);
	});

	it('returns an empty list when the incomplete-set error carries no details', () => {
		const error = new ApiError('Set is incomplete.', 'MOD_SET_INCOMPLETE');

		expect(missingFromError(error)).toEqual([]);
	});

	it('returns an empty list for an API error with a different code', () => {
		const error = new ApiError('Mod conflicts.', 'conflict', ALL_DETAILS);

		expect(missingFromError(error)).toEqual([]);
	});

	it.each([
		{ id: 'a plain Error', error: new Error('network down') },
		{ id: 'a string', error: 'offline' },
		{ id: 'null', error: null }
	])('returns an empty list for $id', ({ error }) => {
		expect(missingFromError(error)).toEqual([]);
	});
});

describe('findSet', () => {
	it('returns the set carrying exactly that name', () => {
		const wanted = makeSet('Hardcore', [makeEntry({ name: 'SAIN' })]);
		const overview = makeOverview([makeSet('Vanilla', []), wanted], []);

		expect(findSet(overview, 'Hardcore')).toBe(wanted);
	});

	it.each([
		{ id: 'another spelling', name: 'hArDcOrE' },
		{ id: 'surrounding whitespace', name: '  Hardcore  ' }
	])('returns the set for a name given with $id', ({ name }) => {
		const wanted = makeSet('Hardcore', []);
		const overview = makeOverview([makeSet('Vanilla', []), wanted], []);

		expect(findSet(overview, name)).toBe(wanted);
	});

	it('returns null when no set carries the name', () => {
		const overview = makeOverview([makeSet('Hardcore', []), makeSet('Vanilla', [])], []);

		expect(findSet(overview, 'Fika night')).toBeNull();
	});

	it('returns null when no set exists at all', () => {
		expect(findSet(makeOverview([], []), 'Hardcore')).toBeNull();
	});

	it.each([
		{ id: 'an empty name', name: '' },
		{ id: 'a name of only whitespace', name: '   ' }
	])('returns null for $id, never a nameless set', ({ name }) => {
		const overview = makeOverview([makeSet('', []), makeSet('Hardcore', [])], []);

		expect(findSet(overview, name)).toBeNull();
	});
});

describe('installTargets', () => {
	it('installs a missing mod by its Forge id where the set recorded one', () => {
		const set = makeSet('Hardcore', [makeEntry({ forge_id: '47', name: 'trailer-name' })]);

		expect(installTargets(set, ['trailer-name'])).toEqual([
			{ name: 'trailer-name', target: 47 }
		]);
	});

	it('matches the entry of a missing mod ignoring case', () => {
		const set = makeSet('Hardcore', [makeEntry({ forge_id: '47', name: 'sain' })]);

		expect(installTargets(set, ['SAIN'])).toEqual([{ name: 'SAIN', target: 47 }]);
	});

	it.each([
		{ id: 'the matching entry carries no Forge id', entryName: 'local-mod' },
		{ id: 'the set does not name the missing mod at all', entryName: 'something-else' }
	])('falls back to the name when $id', ({ entryName }) => {
		const set = makeSet('Hardcore', [makeEntry({ forge_id: '', name: entryName })]);

		expect(installTargets(set, ['local-mod'])).toEqual([
			{ name: 'local-mod', target: 'local-mod' }
		]);
	});

	it('falls back to the names when no set is selected', () => {
		expect(installTargets(null, ['SAIN', 'Looting Bots'])).toEqual([
			{ name: 'SAIN', target: 'SAIN' },
			{ name: 'Looting Bots', target: 'Looting Bots' }
		]);
	});

	it('returns nothing when no mod is missing', () => {
		expect(installTargets(makeSet('Hardcore', [makeEntry({ forge_id: '47' })]), [])).toEqual([]);
	});
});

describe('exportFileName', () => {
	it.each([
		{ id: 'a plain name', name: 'Fika night', expected: 'Fika night.json' },
		{ id: 'a name with kept punctuation', name: 'v1.2_final-set', expected: 'v1.2_final-set.json' },
		{ id: 'a name with path and shell characters', name: 'Fika/night: 2?', expected: 'Fika_night_ 2_.json' },
		{ id: 'a name with surrounding whitespace', name: '  Fika night  ', expected: 'Fika night.json' },
		{ id: 'an empty name', name: '', expected: 'mod-set.json' },
		{ id: 'a name of only whitespace', name: '   ', expected: 'mod-set.json' }
	])('offers $expected for $id', ({ name, expected }) => {
		expect(exportFileName(name)).toBe(expected);
	});
});
