import { describe, expect, it } from 'vitest';
import {
	CHIP_PREDICATES,
	NEW_WINDOW_DAYS,
	categoriesOf,
	chipCounts,
	filterEntries,
	isCompatible,
	isFavorite,
	matchesSearch,
	rowState,
	sortEntries
} from './modfilter';
import type { ChipId, FilterContext, ModFilter } from './modfilter';
import type { CatalogEntry, CatalogMod, CatalogVersion, ModType } from './api/client';

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
		first_seen_at: null,
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
		fika_compatibility: null,
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

const NOW = Date.parse('2026-01-15T00:00:00Z');
const DAY_MS = 24 * 60 * 60 * 1000;

function makeContext(overrides: Partial<FilterContext> = {}): FilterContext {
	return {
		favorites: new Set<number>(),
		disabled: new Set<string>(),
		modTypes: new Map<number, ModType>(),
		now: NOW,
		...overrides
	};
}

function makeFilter(overrides: Partial<ModFilter> = {}): ModFilter {
	return {
		chips: new Set<ChipId>(),
		category: null,
		search: '',
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

describe('CHIP_PREDICATES.new', () => {
	it.each([
		{
			id: 'first_seen_at within the 14-day window',
			seenAt: new Date(NOW - 5 * DAY_MS).toISOString(),
			expected: true
		},
		{
			id: 'first_seen_at exactly at the 14-day boundary',
			seenAt: new Date(NOW - NEW_WINDOW_DAYS * DAY_MS).toISOString(),
			expected: true
		},
		{
			id: 'first_seen_at one millisecond past the boundary',
			seenAt: new Date(NOW - NEW_WINDOW_DAYS * DAY_MS - 1).toISOString(),
			expected: false
		},
		{
			id: 'first_seen_at older than the window',
			seenAt: new Date(NOW - 30 * DAY_MS).toISOString(),
			expected: false
		},
		{ id: 'first_seen_at is null', seenAt: null, expected: false },
		{ id: 'first_seen_at is an unparseable string', seenAt: 'not-a-date', expected: false }
	])('is $expected when $id', ({ seenAt, expected }) => {
		const entry = makeEntry({ mod: makeMod({ first_seen_at: seenAt }) });
		expect(CHIP_PREDICATES.new(entry, makeContext())).toBe(expected);
	});
});

describe('CHIP_PREDICATES.featured', () => {
	it.each([
		{ featured: true, expected: true },
		{ featured: false, expected: false }
	])('is $expected when mod.featured is $featured', ({ featured, expected }) => {
		const entry = makeEntry({ mod: makeMod({ featured }) });
		expect(CHIP_PREDICATES.featured(entry, makeContext())).toBe(expected);
	});
});

describe('CHIP_PREDICATES.installed', () => {
	it.each([
		{
			id: 'state installed, not in the disabled set',
			state: 'installed' as const,
			disabled: [] as string[],
			expected: true
		},
		{
			id: 'state update_available, not in the disabled set',
			state: 'update_available' as const,
			disabled: [] as string[],
			expected: true
		},
		{
			id: 'state available, not in the disabled set',
			state: 'available' as const,
			disabled: [] as string[],
			expected: false
		},
		{
			id: 'state available but the name is in the disabled set (disabled mods drop out of the join)',
			state: 'available' as const,
			disabled: ['a'],
			expected: true
		}
	])('is $expected for $id', ({ state, disabled, expected }) => {
		const entry = makeEntry({ mod: makeMod({ name: 'A' }), state });
		const context = makeContext({ disabled: new Set(disabled) });
		expect(CHIP_PREDICATES.installed(entry, context)).toBe(expected);
	});
});

describe('CHIP_PREDICATES.update_available', () => {
	it.each([
		{
			id: 'update_available and not disabled',
			state: 'update_available' as const,
			disabled: [] as string[],
			expected: true
		},
		{
			id: 'update_available but disabled (disabled wins in rowState)',
			state: 'update_available' as const,
			disabled: ['a'],
			expected: false
		},
		{ id: 'installed with no update', state: 'installed' as const, disabled: [], expected: false },
		{ id: 'not installed', state: 'available' as const, disabled: [], expected: false }
	])('is $expected for $id', ({ state, disabled, expected }) => {
		const entry = makeEntry({ mod: makeMod({ name: 'A' }), state });
		const context = makeContext({ disabled: new Set(disabled) });
		expect(CHIP_PREDICATES.update_available(entry, context)).toBe(expected);
	});
});

describe('CHIP_PREDICATES.disabled', () => {
	it.each([
		{ disabled: ['a'], expected: true },
		{ disabled: ['b'], expected: false },
		{ disabled: [], expected: false }
	])('is $expected for disabled=$disabled', ({ disabled, expected }) => {
		const entry = makeEntry({ mod: makeMod({ name: 'A' }) });
		const context = makeContext({ disabled: new Set(disabled) });
		expect(CHIP_PREDICATES.disabled(entry, context)).toBe(expected);
	});
});

describe('CHIP_PREDICATES.fika', () => {
	it.each([
		{
			id: 'version compatible, mod true',
			versionFika: 'compatible' as const,
			hasVersion: true,
			modFika: true,
			expected: true
		},
		{
			id: 'version compatible, mod false',
			versionFika: 'compatible' as const,
			hasVersion: true,
			modFika: false,
			expected: true
		},
		{
			id: 'version compatible, mod null',
			versionFika: 'compatible' as const,
			hasVersion: true,
			modFika: null,
			expected: true
		},
		{
			id: 'version incompatible, mod true',
			versionFika: 'incompatible' as const,
			hasVersion: true,
			modFika: true,
			expected: false
		},
		{
			id: 'version incompatible, mod false',
			versionFika: 'incompatible' as const,
			hasVersion: true,
			modFika: false,
			expected: false
		},
		{
			id: 'version incompatible, mod null',
			versionFika: 'incompatible' as const,
			hasVersion: true,
			modFika: null,
			expected: false
		},
		{
			id: 'version unknown, mod true',
			versionFika: 'unknown' as const,
			hasVersion: true,
			modFika: true,
			expected: true
		},
		{
			id: 'version unknown, mod false',
			versionFika: 'unknown' as const,
			hasVersion: true,
			modFika: false,
			expected: false
		},
		{
			id: 'version unknown, mod null',
			versionFika: 'unknown' as const,
			hasVersion: true,
			modFika: null,
			expected: false
		},
		{
			id: 'version field explicitly null, mod true',
			versionFika: null,
			hasVersion: true,
			modFika: true,
			expected: true
		},
		{
			id: 'version field explicitly null, mod false',
			versionFika: null,
			hasVersion: true,
			modFika: false,
			expected: false
		},
		{
			id: 'no latest_version, mod true',
			versionFika: null,
			hasVersion: false,
			modFika: true,
			expected: true
		},
		{
			id: 'no latest_version, mod false',
			versionFika: null,
			hasVersion: false,
			modFika: false,
			expected: false
		},
		{
			id: 'no latest_version, mod null',
			versionFika: null,
			hasVersion: false,
			modFika: null,
			expected: false
		}
	])('is $expected when $id', ({ versionFika, hasVersion, modFika, expected }) => {
		const latestVersion = hasVersion ? makeVersion({ fika_compatibility: versionFika }) : null;
		const entry = makeEntry({
			mod: makeMod({ fika_compatibility: modFika }),
			latest_version: latestVersion
		});
		expect(CHIP_PREDICATES.fika(entry, makeContext())).toBe(expected);
	});
});

describe('CHIP_PREDICATES.client and CHIP_PREDICATES.server', () => {
	it.each([
		{ id: 'client mod type', type: 'client' as ModType | undefined, client: true, server: false },
		{ id: 'server mod type', type: 'server' as ModType | undefined, client: false, server: true },
		{ id: 'hybrid mod type matches both', type: 'hybrid' as ModType | undefined, client: true, server: true },
		{ id: 'unknown / uninstalled mod type matches neither', type: undefined, client: false, server: false }
	])('$id', ({ type, client, server }) => {
		const entry = makeEntry({ mod: makeMod({ id: 9 }) });
		const modTypes = new Map<number, ModType>();
		if (type !== undefined) {
			modTypes.set(9, type);
		}
		const context = makeContext({ modTypes });
		expect(CHIP_PREDICATES.client(entry, context)).toBe(client);
		expect(CHIP_PREDICATES.server(entry, context)).toBe(server);
	});
});

describe('filterEntries', () => {
	// alpha: featured, compatible, category Weapons
	// beta: not featured, compatible, category Weapons
	// gamma: featured, incompatible, category Armor
	// delta: not featured, incompatible, no category
	const alpha = makeEntry({
		mod: makeMod({ name: 'Alpha', author: 'Aaron', featured: true, category: 'Weapons' }),
		latest_version: makeVersion()
	});
	const beta = makeEntry({
		mod: makeMod({ name: 'Beta', author: 'Barb', featured: false, category: 'Weapons' }),
		latest_version: makeVersion()
	});
	const gamma = makeEntry({
		mod: makeMod({ name: 'Gamma', author: 'Gary', featured: true, category: 'Armor' }),
		latest_version: null
	});
	const delta = makeEntry({
		mod: makeMod({ name: 'Delta', author: 'Dana', featured: false, category: null }),
		latest_version: null
	});
	const entries = [alpha, beta, gamma, delta];

	it('with no active chips, no category and no search, returns every entry', () => {
		expect(filterEntries(entries, makeFilter(), makeContext())).toEqual(entries);
	});

	it('a single chip filters to entries matching that predicate', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['featured']) });
		expect(filterEntries(entries, filter, makeContext())).toEqual([alpha, gamma]);
	});

	it('multiple chips combine with AND', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['featured', 'compatible']) });
		expect(filterEntries(entries, filter, makeContext())).toEqual([alpha]);
	});

	it('filters by category', () => {
		const filter = makeFilter({ category: 'Armor' });
		expect(filterEntries(entries, filter, makeContext())).toEqual([gamma]);
	});

	it('category null does not filter by category', () => {
		const filter = makeFilter({ category: null });
		expect(filterEntries(entries, filter, makeContext())).toEqual(entries);
	});

	it('a category title absent from every entry yields an empty result', () => {
		const filter = makeFilter({ category: 'Nonexistent' });
		expect(filterEntries(entries, filter, makeContext())).toEqual([]);
	});

	it('combines a chip with a category filter', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['featured']), category: 'Weapons' });
		expect(filterEntries(entries, filter, makeContext())).toEqual([alpha]);
	});

	it('combines a chip with a search filter', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['featured']), search: 'gary' });
		expect(filterEntries(entries, filter, makeContext())).toEqual([gamma]);
	});

	it('returns an empty array for an empty entry list', () => {
		expect(filterEntries([], makeFilter(), makeContext())).toEqual([]);
	});
});

describe('chipCounts', () => {
	// alpha: featured, compatible, category Weapons
	// beta: not featured, compatible, category Weapons
	// gamma: featured, incompatible, category Armor
	// delta: not featured, incompatible, no category
	const alpha = makeEntry({
		mod: makeMod({ name: 'Alpha', featured: true, category: 'Weapons' }),
		latest_version: makeVersion()
	});
	const beta = makeEntry({
		mod: makeMod({ name: 'Beta', featured: false, category: 'Weapons' }),
		latest_version: makeVersion()
	});
	const gamma = makeEntry({
		mod: makeMod({ name: 'Gamma', featured: true, category: 'Armor' }),
		latest_version: null
	});
	const delta = makeEntry({
		mod: makeMod({ name: 'Delta', featured: false, category: null }),
		latest_version: null
	});
	const entries = [alpha, beta, gamma, delta];

	it('with zero active chips, each count matches filtering by that chip alone', () => {
		const counts = chipCounts(entries, makeFilter(), makeContext());
		expect(counts.featured).toBe(2);
		expect(counts.compatible).toBe(2);
	});

	it("an active chip's count equals the current filtered row count", () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['featured']) });
		const counts = chipCounts(entries, filter, makeContext());
		const currentCount = filterEntries(entries, filter, makeContext()).length;
		expect(currentCount).toBe(2);
		expect(counts.featured).toBe(currentCount);
	});

	it('leave-one-out: a chip count equals the row count after toggling that chip on', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['featured']) });
		const counts = chipCounts(entries, filter, makeContext());
		const toggledOn = filterEntries(
			entries,
			{ ...filter, chips: new Set<ChipId>(['featured', 'compatible']) },
			makeContext()
		);
		expect(counts.compatible).toBe(toggledOn.length);
		expect(counts.compatible).toBe(1);
	});

	it('reflects several active chips at once', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['featured', 'compatible']) });
		const counts = chipCounts(entries, filter, makeContext());
		expect(counts.featured).toBe(1);
		expect(counts.compatible).toBe(1);
		expect(counts.installed).toBe(0);
	});

	it('respects category alongside the chip toggle', () => {
		const filter = makeFilter({ category: 'Weapons' });
		const counts = chipCounts(entries, filter, makeContext());
		expect(counts.featured).toBe(1);
	});
});

describe('categoriesOf', () => {
	it('returns the sorted, deduplicated list of non-null categories', () => {
		const entries = [
			makeEntry({ mod: makeMod({ category: 'Zebra' }) }),
			makeEntry({ mod: makeMod({ category: 'Alpha' }) }),
			makeEntry({ mod: makeMod({ category: 'Alpha' }) }),
			makeEntry({ mod: makeMod({ category: null }) })
		];
		expect(categoriesOf(entries)).toEqual(['Alpha', 'Zebra']);
	});

	it('returns an empty array when every entry has no category', () => {
		const entries = [makeEntry({ mod: makeMod({ category: null }) })];
		expect(categoriesOf(entries)).toEqual([]);
	});

	it('returns an empty array for an empty entry list', () => {
		expect(categoriesOf([])).toEqual([]);
	});
});

describe('rowState', () => {
	it.each([
		{
			id: 'an installed mod whose name is in the disabled set',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabled: ['foo bar'],
			name: 'Foo Bar',
			expected: 'disabled'
		},
		{
			id: 'a disabled mod that also has an update available (disabled wins)',
			state: 'update_available' as const,
			latestVersion: makeVersion(),
			disabled: ['foo bar'],
			name: 'Foo Bar',
			expected: 'disabled'
		},
		{
			id: 'an entry whose casing differs from the (already-lowercased) disabled set entry',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabled: ['foo bar'],
			name: 'FOO BAR',
			expected: 'disabled'
		},
		{
			id: 'an update-available mod that is not disabled',
			state: 'update_available' as const,
			latestVersion: makeVersion(),
			disabled: [],
			name: 'Foo Bar',
			expected: 'update_available'
		},
		{
			id: 'an installed mod that is not disabled and has no update',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabled: [],
			name: 'Foo Bar',
			expected: 'active'
		},
		{
			id: 'an available entry with no latest_version',
			state: 'available' as const,
			latestVersion: null,
			disabled: [],
			name: 'Foo Bar',
			expected: 'incompatible'
		},
		{
			id: 'an available entry with a latest_version',
			state: 'available' as const,
			latestVersion: makeVersion(),
			disabled: [],
			name: 'Foo Bar',
			expected: 'available'
		},
		{
			id: 'an installed mod with only an unrelated name in the disabled set',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabled: ['some other mod'],
			name: 'Foo Bar',
			expected: 'active'
		},
		{
			id: 'an installed mod with an empty disabled set',
			state: 'installed' as const,
			latestVersion: makeVersion(),
			disabled: [],
			name: 'Foo Bar',
			expected: 'active'
		}
	])('is "$expected" for $id', ({ state, latestVersion, disabled, name, expected }) => {
		const entry = makeEntry({ mod: makeMod({ name }), state, latest_version: latestVersion });
		expect(rowState(entry, new Set(disabled))).toBe(expected);
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

	it.each([
		{ descending: false, expected: ['Low', 'High'] },
		{ descending: true, expected: ['High', 'Low'] }
	])('sorts by downloads numerically with descending=$descending', ({ descending, expected }) => {
		const low = makeEntry({ mod: makeMod({ name: 'Low', downloads: 5 }) });
		const high = makeEntry({ mod: makeMod({ name: 'High', downloads: 50 }) });

		const result = sortEntries([high, low], { key: 'downloads', descending });

		expect(result.map((entry) => entry.mod.name)).toEqual(expected);
	});

	const missingValueCases = [
		{
			id: 'installed',
			key: 'installed' as const,
			withoutValue: makeEntry({ mod: makeMod({ name: 'Without' }), installed_version: null }),
			withValue: makeEntry({ mod: makeMod({ name: 'With' }), installed_version: '1.0.0' })
		},
		{
			id: 'latest',
			key: 'latest' as const,
			withoutValue: makeEntry({ mod: makeMod({ name: 'Without' }), latest_version: null }),
			withValue: makeEntry({
				mod: makeMod({ name: 'With' }),
				latest_version: makeVersion({ version: '2.0.0' })
			})
		},
		{
			id: 'spt',
			key: 'spt' as const,
			withoutValue: makeEntry({ mod: makeMod({ name: 'Without' }), latest_version: null }),
			withValue: makeEntry({
				mod: makeMod({ name: 'With' }),
				latest_version: makeVersion({ spt_version_constraint: '~3.9.0' })
			})
		},
		{
			id: 'updated',
			key: 'updated' as const,
			withoutValue: makeEntry({ mod: makeMod({ name: 'Without', updated_at: null }) }),
			withValue: makeEntry({ mod: makeMod({ name: 'With', updated_at: '2024-01-01' }) })
		}
	];

	it.each(
		missingValueCases.flatMap((base) => [
			{ ...base, id: `${base.id}-ascending`, descending: false },
			{ ...base, id: `${base.id}-descending`, descending: true }
		])
	)('$id: an entry without a value ranks below one with a value', ({
		key,
		descending,
		withoutValue,
		withValue
	}) => {
		const expected = descending ? [withValue, withoutValue] : [withoutValue, withValue];

		const result = sortEntries([...expected].reverse(), { key, descending });

		expect(result).toEqual(expected);
	});
});
