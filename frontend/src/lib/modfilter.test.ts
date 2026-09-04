import { describe, expect, it } from 'vitest';
import {
	filterEntries,
	isCompatible,
	isFavorite,
	isInstalled,
	matchesSearch,
	rowState,
	sortEntries
} from './modfilter';
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
		thumbnail: null,
		favourites_count: 0,
		endorsements_count: 0,
		fika_compatibility: null,
		featured: false,
		contains_ads: false,
		contains_ai_content: false,
		category: null,
		license_name: null,
		license_link: null,
		source_links: [],
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
	it.each([
		{ id: 'a latest_version is present', latestVersion: makeVersion(), expected: true },
		{ id: 'latest_version is null', latestVersion: null, expected: false }
	])('is $expected when $id', ({ latestVersion, expected }) => {
		const entry = makeEntry({ latest_version: latestVersion });
		expect(isCompatible(entry)).toBe(expected);
	});
});

describe('isInstalled', () => {
	it.each([
		{ state: 'installed' as const, expected: true },
		{ state: 'update_available' as const, expected: true },
		{ state: 'available' as const, expected: false }
	])('is $expected for state "$state"', ({ state, expected }) => {
		expect(isInstalled(makeEntry({ state }))).toBe(expected);
	});
});

describe('isFavorite', () => {
	it.each([
		{ id: 'the entry mod id is in the favorites set', favorites: [47], expected: true },
		{ id: 'the entry mod id is not in the favorites set', favorites: [50], expected: false },
		{ id: 'the favorites set is empty', favorites: [], expected: false }
	])('is $expected when $id', ({ favorites, expected }) => {
		const entry = makeEntry({ mod: makeMod({ id: 47 }) });
		expect(isFavorite(entry, new Set(favorites))).toBe(expected);
	});
});

describe('matchesSearch', () => {
	it.each([
		{
			id: 'the query matches the mod name case-insensitively',
			name: 'Realistic Sound Mod',
			author: 'Zed',
			query: 'realistic',
			expected: true
		},
		{
			id: 'the query matches the author case-insensitively',
			name: 'Something',
			author: 'Waffle.Lord',
			query: 'WAFFLE',
			expected: true
		},
		{
			id: 'the query has surrounding whitespace, which is trimmed',
			name: 'Realistic Sound Mod',
			author: 'Zed',
			query: '  realistic  ',
			expected: true
		},
		{
			id: 'the query is empty',
			name: 'Anything',
			author: 'Anyone',
			query: '',
			expected: true
		},
		{
			id: 'the query is whitespace only',
			name: 'Anything',
			author: 'Anyone',
			query: '   ',
			expected: true
		},
		{
			id: 'neither name nor author contains the query',
			name: 'Realistic Sound Mod',
			author: 'Zed',
			query: 'graphics',
			expected: false
		}
	])('is $expected when $id', ({ name, author, query, expected }) => {
		const entry = makeEntry({ mod: makeMod({ name, author }) });
		expect(matchesSearch(entry, query)).toBe(expected);
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

	// Favorites: A (installed, compatible) and D (not installed, incompatible).
	const favorites = new Set([entryA.mod.id, entryD.mod.id]);

	it.each([
		{ tab: 'all' as const, compatibleOnly: false, expected: [entryA, entryB, entryC, entryD] },
		{ tab: 'all' as const, compatibleOnly: true, expected: [entryA, entryC] },
		{ tab: 'installed' as const, compatibleOnly: false, expected: [entryA, entryB] },
		{ tab: 'installed' as const, compatibleOnly: true, expected: [entryA] },
		{ tab: 'favorited' as const, compatibleOnly: false, expected: [entryA, entryD] },
		{ tab: 'favorited' as const, compatibleOnly: true, expected: [entryA] }
	])('tab=$tab compatibleOnly=$compatibleOnly', ({ tab, compatibleOnly, expected }) => {
		const result = filterEntries(entries, {
			tab,
			compatibleOnly,
			favorites,
			search: ''
		});
		expect(result).toEqual(expected);
	});

	it('the favorited tab keeps favorited entries regardless of install state', () => {
		// A is installed, D is not; both are favorited.
		const result = filterEntries(entries, {
			tab: 'favorited',
			compatibleOnly: false,
			favorites,
			search: ''
		});
		expect(result).toEqual([entryA, entryD]);
	});

	it('the favorited tab hides entries that are not favorited', () => {
		const result = filterEntries(entries, {
			tab: 'favorited',
			compatibleOnly: false,
			favorites,
			search: ''
		});
		expect(result).not.toContain(entryB);
		expect(result).not.toContain(entryC);
	});

	it('the favorited tab is empty for an empty favorites set', () => {
		const result = filterEntries(entries, {
			tab: 'favorited',
			compatibleOnly: false,
			favorites: new Set<number>(),
			search: ''
		});
		expect(result).toEqual([]);
	});

	it('tabs other than favorited ignore a non-empty favorites set', () => {
		const result = filterEntries(entries, {
			tab: 'all',
			compatibleOnly: false,
			favorites,
			search: ''
		});
		expect(result).toEqual([entryA, entryB, entryC, entryD]);
	});

	it('the favorited tab combines with search', () => {
		// Both A and D are favorited, but only D's name matches "delta".
		const result = filterEntries(entries, {
			tab: 'favorited',
			compatibleOnly: false,
			favorites,
			search: 'delta'
		});
		expect(result).toEqual([entryD]);
	});

	it('sorting still applies within the favorited tab', () => {
		const result = sortEntries(
			filterEntries(entries, {
				tab: 'favorited',
				compatibleOnly: false,
				favorites,
				search: ''
			}),
			{ key: 'name', descending: true }
		);
		expect(result).toEqual([entryD, entryA]);
	});

	it('applies the search filter on top of the tab and compatibility filters', () => {
		const result = filterEntries(entries, {
			tab: 'all',
			compatibleOnly: false,
			favorites: new Set<number>(),
			search: 'AL'
		});
		// "AL" matches "Alpha" (name) and "Dana" author does not, but "Gary" doesn't either;
		// only Alpha's name contains "al" case-insensitively.
		expect(result).toEqual([entryA]);
	});

	it('matches search against the author field within the active tab', () => {
		const result = filterEntries(entries, {
			tab: 'favorited',
			compatibleOnly: false,
			favorites,
			search: 'dana'
		});
		expect(result).toEqual([entryD]);
	});

	it('trims the search query before filtering', () => {
		const result = filterEntries(entries, {
			tab: 'all',
			compatibleOnly: false,
			favorites: new Set<number>(),
			search: '  gamma  '
		});
		expect(result).toEqual([entryC]);
	});

	it('returns an empty array when nothing in the active tab matches the search', () => {
		const result = filterEntries(entries, {
			tab: 'installed',
			compatibleOnly: false,
			favorites: new Set<number>(),
			search: 'gamma'
		});
		expect(result).toEqual([]);
	});

	it('returns an empty array for an empty entry list', () => {
		const result = filterEntries([], {
			tab: 'all',
			compatibleOnly: false,
			favorites: new Set<number>(),
			search: ''
		});
		expect(result).toEqual([]);
	});
});

describe('rowState', () => {
	it.each([
		{
			id: 'an installed mod whose name is in disabledMods',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabledMods: ['Foo Bar'],
			expected: 'disabled'
		},
		{
			id: 'a disabled mod that also has an update available',
			state: 'update_available' as const,
			latestVersion: makeVersion(),
			disabledMods: ['Foo Bar'],
			expected: 'disabled'
		},
		{
			id: 'a disabledMods name differing only in case',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabledMods: ['fOO bAR'],
			expected: 'disabled'
		},
		{
			id: 'an update-available mod that is not disabled',
			state: 'update_available' as const,
			latestVersion: makeVersion(),
			disabledMods: [],
			expected: 'update_available'
		},
		{
			id: 'an installed mod that is not disabled and has no update',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabledMods: [],
			expected: 'active'
		},
		{
			id: 'an available entry with no latest_version',
			state: 'available' as const,
			latestVersion: null,
			disabledMods: [],
			expected: 'incompatible'
		},
		{
			id: 'an available entry with a latest_version',
			state: 'available' as const,
			latestVersion: makeVersion(),
			disabledMods: [],
			expected: 'available'
		},
		{
			id: 'an installed mod with only an unrelated name in disabledMods',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabledMods: ['Some Other Mod'],
			expected: 'active'
		},
		{
			id: 'an installed mod with an empty disabledMods list',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabledMods: [],
			expected: 'active'
		}
	])('is "$expected" for $id', ({ state, latestVersion, disabledMods, expected }) => {
		const entry = makeEntry({
			mod: makeMod({ name: 'Foo Bar' }),
			state,
			latest_version: latestVersion
		});
		expect(rowState(entry, disabledMods)).toBe(expected);
	});
});

describe('sortEntries', () => {
	it('returns entries in their original order, unmodified, when sort is null', () => {
		const entries = [
			makeEntry({ mod: makeMod({ name: 'Zebra' }) }),
			makeEntry({ mod: makeMod({ name: 'Alpha' }) }),
			makeEntry({ mod: makeMod({ name: 'Mango' }) })
		];

		expect(sortEntries(entries, null)).toEqual(entries);
	});

	it.each([
		{ descending: false, expected: ['Alpha', 'Mango', 'Zebra'] },
		{ descending: true, expected: ['Zebra', 'Mango', 'Alpha'] }
	])('sorts by name with descending=$descending', ({ descending, expected }) => {
		const entries = [
			makeEntry({ mod: makeMod({ name: 'Zebra' }) }),
			makeEntry({ mod: makeMod({ name: 'Alpha' }) }),
			makeEntry({ mod: makeMod({ name: 'Mango' }) })
		];

		const result = sortEntries(entries, { key: 'name', descending });

		expect(result.map((entry) => entry.mod.name)).toEqual(expected);
	});

	it('keeps entries with an equal sort value in their original relative order', () => {
		const first = makeEntry({ mod: makeMod({ name: 'First', downloads: 10 }) });
		const second = makeEntry({ mod: makeMod({ name: 'Second', downloads: 10 }) });

		const result = sortEntries([second, first], { key: 'downloads', descending: false });

		expect(result).toEqual([second, first]);
	});

	it('sorts by downloads numerically', () => {
		const low = makeEntry({ mod: makeMod({ name: 'Low', downloads: 5 }) });
		const high = makeEntry({ mod: makeMod({ name: 'High', downloads: 50 }) });

		const result = sortEntries([high, low], { key: 'downloads', descending: false });

		expect(result).toEqual([low, high]);
	});

	it.each([
		{
			id: 'installed: a missing installed_version sorts before a present one',
			key: 'installed' as const,
			withoutValue: makeEntry({ mod: makeMod({ name: 'Without' }), installed_version: null }),
			withValue: makeEntry({ mod: makeMod({ name: 'With' }), installed_version: '1.0.0' })
		},
		{
			id: 'latest: a null latest_version sorts before a present version',
			key: 'latest' as const,
			withoutValue: makeEntry({ mod: makeMod({ name: 'Without' }), latest_version: null }),
			withValue: makeEntry({
				mod: makeMod({ name: 'With' }),
				latest_version: makeVersion({ version: '2.0.0' })
			})
		},
		{
			id: 'spt: a null latest_version sorts before a present spt_version_constraint',
			key: 'spt' as const,
			withoutValue: makeEntry({ mod: makeMod({ name: 'Without' }), latest_version: null }),
			withValue: makeEntry({
				mod: makeMod({ name: 'With' }),
				latest_version: makeVersion({ spt_version_constraint: '~3.9.0' })
			})
		},
		{
			id: 'updated: a null updated_at sorts before a present timestamp',
			key: 'updated' as const,
			withoutValue: makeEntry({ mod: makeMod({ name: 'Without', updated_at: null }) }),
			withValue: makeEntry({ mod: makeMod({ name: 'With', updated_at: '2024-01-01' }) })
		}
	])('$id', ({ key, withoutValue, withValue }) => {
		const result = sortEntries([withValue, withoutValue], { key, descending: false });

		expect(result).toEqual([withoutValue, withValue]);
	});
});
