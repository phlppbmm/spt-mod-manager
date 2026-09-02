import { describe, expect, it } from 'vitest';
import { filterEntries, isCompatible, isInstalled, matchesSearch, rowState } from './modfilter';
import type { CatalogEntry, CatalogMod, CatalogVersion } from './api/client';

let nextId = 1;

function makeMod(overrides: Partial<CatalogMod> = {}): CatalogMod {
	const id = nextId++;
	return {
		id,
		hub_id: null,
		guid: null,
		name: `Mod ${id}`,
		slug: `mod-${id}`,
		teaser: null,
		author: `Author ${id}`,
		downloads: 0,
		detail_url: `https://example.test/mod/${id}`,
		published_at: null,
		created_at: null,
		updated_at: null,
		...overrides
	};
}

function makeVersion(overrides: Partial<CatalogVersion> = {}): CatalogVersion {
	const id = nextId++;
	return {
		id,
		mod_id: id,
		version: '1.0.0',
		link: null,
		content_length: null,
		spt_version_constraint: '~3.9.0',
		downloads: 0,
		published_at: null,
		updated_at: null,
		...overrides
	};
}

function makeEntry(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
	return {
		mod: makeMod(),
		state: 'available',
		installed_version: null,
		latest_version: makeVersion(),
		...overrides
	};
}

describe('isCompatible', () => {
	it('is true when the entry carries a latest_version', () => {
		const entry = makeEntry({ latest_version: makeVersion() });
		expect(isCompatible(entry)).toBe(true);
	});

	it('is false when latest_version is null', () => {
		const entry = makeEntry({ latest_version: null });
		expect(isCompatible(entry)).toBe(false);
	});
});

describe('isInstalled', () => {
	it('is true for state "installed"', () => {
		expect(isInstalled(makeEntry({ state: 'installed' }))).toBe(true);
	});

	it('is true for state "update_available"', () => {
		expect(isInstalled(makeEntry({ state: 'update_available' }))).toBe(true);
	});

	it('is false for state "available"', () => {
		expect(isInstalled(makeEntry({ state: 'available' }))).toBe(false);
	});
});

describe('matchesSearch', () => {
	it('matches on mod name, case-insensitively', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Realistic Sound Mod', author: 'Zed' }) });
		expect(matchesSearch(entry, 'realistic')).toBe(true);
	});

	it('matches on author, case-insensitively', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Something', author: 'Waffle.Lord' }) });
		expect(matchesSearch(entry, 'WAFFLE')).toBe(true);
	});

	it('trims surrounding whitespace from the query before matching', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Realistic Sound Mod', author: 'Zed' }) });
		expect(matchesSearch(entry, '  realistic  ')).toBe(true);
	});

	it('treats an empty query as matching everything', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Anything', author: 'Anyone' }) });
		expect(matchesSearch(entry, '')).toBe(true);
	});

	it('treats a whitespace-only query as matching everything', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Anything', author: 'Anyone' }) });
		expect(matchesSearch(entry, '   ')).toBe(true);
	});

	it('returns false when neither name nor author contains the query', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Realistic Sound Mod', author: 'Zed' }) });
		expect(matchesSearch(entry, 'graphics')).toBe(false);
	});
});

describe('filterEntries', () => {
	// A: installed, compatible.
	// B: update_available (installed), incompatible.
	// C: available (not installed), compatible.
	// D: available (not installed), incompatible.
	const entryA = makeEntry({
		mod: makeMod({ name: 'Alpha', author: 'Aaron' }),
		state: 'installed',
		latest_version: makeVersion()
	});
	const entryB = makeEntry({
		mod: makeMod({ name: 'Beta', author: 'Barb' }),
		state: 'update_available',
		latest_version: null
	});
	const entryC = makeEntry({
		mod: makeMod({ name: 'Gamma', author: 'Gary' }),
		state: 'available',
		latest_version: makeVersion()
	});
	const entryD = makeEntry({
		mod: makeMod({ name: 'Delta', author: 'Dana' }),
		state: 'available',
		latest_version: null
	});
	const entries = [entryA, entryB, entryC, entryD];

	it.each([
		{ tab: 'all' as const, compatibleOnly: false, expected: [entryA, entryB, entryC, entryD] },
		{ tab: 'all' as const, compatibleOnly: true, expected: [entryA, entryC] },
		{ tab: 'installed' as const, compatibleOnly: false, expected: [entryA, entryB] },
		{ tab: 'installed' as const, compatibleOnly: true, expected: [entryA] },
		{ tab: 'available' as const, compatibleOnly: false, expected: [entryC, entryD] },
		{ tab: 'available' as const, compatibleOnly: true, expected: [entryC] }
	])('tab=$tab compatibleOnly=$compatibleOnly', ({ tab, compatibleOnly, expected }) => {
		const result = filterEntries(entries, { tab, compatibleOnly, search: '' });
		expect(result).toEqual(expected);
	});

	it('applies the search filter on top of the tab and compatibility filters', () => {
		const result = filterEntries(entries, { tab: 'all', compatibleOnly: false, search: 'AL' });
		// "AL" matches "Alpha" (name) and "Dana" author does not, but "Gary" doesn't either;
		// only Alpha's name contains "al" case-insensitively.
		expect(result).toEqual([entryA]);
	});

	it('matches search against the author field within the active tab', () => {
		const result = filterEntries(entries, {
			tab: 'available',
			compatibleOnly: false,
			search: 'dana'
		});
		expect(result).toEqual([entryD]);
	});

	it('trims the search query before filtering', () => {
		const result = filterEntries(entries, {
			tab: 'all',
			compatibleOnly: false,
			search: '  gamma  '
		});
		expect(result).toEqual([entryC]);
	});

	it('returns an empty array when nothing in the active tab matches the search', () => {
		const result = filterEntries(entries, {
			tab: 'installed',
			compatibleOnly: false,
			search: 'gamma'
		});
		expect(result).toEqual([]);
	});

	it('returns an empty array for an empty entry list', () => {
		expect(filterEntries([], { tab: 'all', compatibleOnly: false, search: '' })).toEqual([]);
	});
});

describe('rowState', () => {
	it('is "disabled" for an installed mod whose name is in disabledMods', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Foo Bar' }), state: 'installed' });
		expect(rowState(entry, ['Foo Bar'])).toBe('disabled');
	});

	it('"disabled" takes precedence over "update_available"', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Foo Bar' }), state: 'update_available' });
		expect(rowState(entry, ['Foo Bar'])).toBe('disabled');
	});

	it('"disabled" name matching is case-insensitive', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Foo Bar' }), state: 'installed' });
		expect(rowState(entry, ['fOO bAR'])).toBe('disabled');
	});

	it('"update_available" takes precedence over "active" when not disabled', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Not Disabled' }), state: 'update_available' });
		expect(rowState(entry, [])).toBe('update_available');
	});

	it('is "active" for an installed entry that is not disabled and has no update', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Not Disabled' }), state: 'installed' });
		expect(rowState(entry, [])).toBe('active');
	});

	it('is "incompatible" for an available entry with no latest_version', () => {
		const entry = makeEntry({
			mod: makeMod({ name: 'Not Disabled' }),
			state: 'available',
			latest_version: null
		});
		expect(rowState(entry, [])).toBe('incompatible');
	});

	it('is "available" for an available entry with a latest_version', () => {
		const entry = makeEntry({
			mod: makeMod({ name: 'Not Disabled' }),
			state: 'available',
			latest_version: makeVersion()
		});
		expect(rowState(entry, [])).toBe('available');
	});

	it('an unrelated disabled-mods entry does not mark the row disabled', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Foo Bar' }), state: 'installed' });
		expect(rowState(entry, ['Some Other Mod'])).toBe('active');
	});

	it('an empty disabledMods list never marks a row disabled', () => {
		const entry = makeEntry({ mod: makeMod({ name: 'Foo Bar' }), state: 'installed' });
		expect(rowState(entry, [])).toBe('active');
	});
});
