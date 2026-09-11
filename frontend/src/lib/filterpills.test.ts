import { describe, expect, it } from 'vitest';
import {
	QUICK_TABS,
	activePills,
	activeQuickTab,
	applyPill,
	applyQuickTab,
	availablePills,
	installableVersions,
	pillDefinitions,
	removePill,
	showsInstalledColumns,
	sortForTab
} from './filterpills';
import type { PillDefinition, PillKey } from './filterpills';
import type { SptReleaseInfo } from './api/client';
import type { ChipId, ModFilter } from './modfilter';

/**
 * Only `version` and `downloadable` steer the compatibility options; the other
 * fields of the RPC row are fixed placeholders so a test states just what it
 * varies.
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

function makeFilter(overrides: Partial<ModFilter> = {}): ModFilter {
	return {
		chips: new Set<ChipId>(),
		category: null,
		search: '',
		...overrides
	};
}

function defOf(defs: PillDefinition[], key: PillKey): PillDefinition {
	const found = defs.find((def) => def.key === key);
	if (!found) throw new Error(`no def for ${key}`);
	return found;
}

describe('pillDefinitions', () => {
	it('labels the compatibility option "Compatible" when no SPT version is known', () => {
		const defs = pillDefinitions([], null);
		const compat = defOf(defs, 'compatibility');
		expect(compat.options[0]).toEqual({ id: 'compatible', label: 'Compatible', chips: ['compatible'] });
		expect(compat.options[1]).toEqual({ id: 'all', label: 'All versions', chips: [] });
	});

	it('labels the compatibility option with the SPT version when one is known', () => {
		const defs = pillDefinitions([], '3.9.1');
		const compat = defOf(defs, 'compatibility');
		expect(compat.options[0].label).toBe('SPT 3.9.1');
	});

	it('builds category options from the given category list, in order', () => {
		const defs = pillDefinitions(['Weapons', 'Armor'], null);
		const category = defOf(defs, 'category');
		expect(category.options).toEqual([
			{ id: 'Weapons', label: 'Weapons', chips: [], category: 'Weapons' },
			{ id: 'Armor', label: 'Armor', chips: [], category: 'Armor' }
		]);
	});

	it('produces no category options for an empty category list', () => {
		const defs = pillDefinitions([], null);
		expect(defOf(defs, 'category').options).toEqual([]);
	});

	it.each([
		{
			id: 'type',
			key: 'type' as PillKey,
			options: [
				{ id: 'client', label: 'Client', chips: ['client'] },
				{ id: 'server', label: 'Server', chips: ['server'] }
			]
		},
		{
			id: 'fika',
			key: 'fika' as PillKey,
			options: [{ id: 'fika', label: 'Compatible', chips: ['fika'] }]
		},
		{
			id: 'state',
			key: 'state' as PillKey,
			options: [
				{ id: 'installed', label: 'Installed', chips: ['installed'] },
				{ id: 'disabled', label: 'Disabled', chips: ['disabled'] }
			]
		},
		{
			id: 'featured',
			key: 'featured' as PillKey,
			options: [{ id: 'featured', label: 'Featured', chips: ['featured'] }]
		}
	])('the $id pill offers the labels and chips shown in the filter bar', ({ key, options }) => {
		expect(defOf(pillDefinitions([], null), key).options).toEqual(options);
	});
});

describe('activePills', () => {
	const defs = pillDefinitions(['Weapons', 'Armor'], '3.9.1');

	it('reports no active pills for an unfiltered ModFilter', () => {
		expect(activePills(makeFilter(), defs)).toEqual([]);
	});

	it('reports the compatibility pill active when the compatible chip is set, using the SPT-version label', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']) });
		expect(activePills(filter, defs)).toEqual([
			{ key: 'compatibility', optionIds: ['compatible'], valueLabel: 'SPT 3.9.1' }
		]);
	});

	it('never reports the compatibility pill active via the "all" option', () => {
		const filter = makeFilter();
		const compatOnly = [defOf(defs, 'compatibility')];
		expect(activePills(filter, compatOnly)).toEqual([]);
	});

	it('reports the category pill active with the matching category label', () => {
		const filter = makeFilter({ category: 'Armor' });
		expect(activePills(filter, defs)).toEqual([
			{ key: 'category', optionIds: ['Armor'], valueLabel: 'Armor' }
		]);
	});

	it('reports the type pill active for a single selected chip', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['client']) });
		expect(activePills(filter, defs)).toEqual([
			{ key: 'type', optionIds: ['client'], valueLabel: 'Client' }
		]);
	});

	it('joins multiple selected options of the same pill in definition order', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['server', 'client']) });
		expect(activePills(filter, defs)).toEqual([
			{ key: 'type', optionIds: ['client', 'server'], valueLabel: 'Client, Server' }
		]);
	});

	it('lists active pills in definition order regardless of chip insertion order', () => {
		const filter = makeFilter({
			chips: new Set<ChipId>(['featured', 'compatible']),
			category: 'Weapons'
		});
		expect(activePills(filter, defs).map((pill) => pill.key)).toEqual([
			'compatibility',
			'category',
			'featured'
		]);
	});
});

describe('applyPill', () => {
	const defs = pillDefinitions(['Weapons', 'Armor'], null);

	it('sets the chip for a selected option', () => {
		const filter = makeFilter({ search: 'scav' });
		const result = applyPill(filter, defOf(defs, 'compatibility'), ['compatible']);
		expect(result).toEqual({
			chips: new Set<ChipId>(['compatible']),
			category: null,
			search: 'scav'
		});
	});

	it('replaces a previously selected chip within the same pill', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['installed']) });
		const result = applyPill(filter, defOf(defs, 'state'), ['disabled']);
		expect([...result.chips]).toEqual(['disabled']);
	});

	it('an empty selection equals removePill', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['fika']) });
		const applied = applyPill(filter, defOf(defs, 'fika'), []);
		const removed = removePill(filter, defOf(defs, 'fika'));
		expect(applied).toEqual(removed);
		expect([...applied.chips]).toEqual([]);
	});

	it('sets the category and clears any previous category on selection', () => {
		const filter = makeFilter({ category: 'Weapons' });
		const result = applyPill(filter, defOf(defs, 'category'), ['Armor']);
		expect(result.category).toBe('Armor');
	});

	it('clears the category when the category pill selection is empty', () => {
		const filter = makeFilter({ category: 'Weapons' });
		const result = applyPill(filter, defOf(defs, 'category'), []);
		expect(result.category).toBeNull();
	});

	it('selecting the Compatibility "all" option removes the compatible chip', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']) });
		const result = applyPill(filter, defOf(defs, 'compatibility'), ['all']);
		expect([...result.chips]).toEqual([]);
	});

	it('the "all" selection leaves the compatibility pill out of activePills', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']) });
		const result = applyPill(filter, defOf(defs, 'compatibility'), ['all']);
		expect(activePills(result, defs).map((pill) => pill.key)).not.toContain('compatibility');
	});

	it('leaves chips of unrelated pills untouched', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible', 'fika']) });
		const result = applyPill(filter, defOf(defs, 'state'), ['installed']);
		expect([...result.chips].sort()).toEqual(['compatible', 'fika', 'installed']);
	});
});

describe('removePill', () => {
	const defs = pillDefinitions(['Weapons'], null);

	it('removes only the chip belonging to the given pill, keeping others and the search', () => {
		const filter = makeFilter({
			chips: new Set<ChipId>(['compatible', 'fika']),
			category: 'Weapons',
			search: 'quest'
		});
		const result = removePill(filter, defOf(defs, 'fika'));
		expect(result).toEqual({
			chips: new Set<ChipId>(['compatible']),
			category: 'Weapons',
			search: 'quest'
		});
	});

	it('clears the category when removing the category pill', () => {
		const filter = makeFilter({ category: 'Weapons', search: 'quest' });
		const result = removePill(filter, defOf(defs, 'category'));
		expect(result.category).toBeNull();
		expect(result.search).toBe('quest');
	});
});

describe('availablePills', () => {
	const defs = pillDefinitions(['Weapons'], null);

	it('offers every pill in display order when nothing is active', () => {
		const result = availablePills(makeFilter(), defs);
		expect(result).toEqual(defs);
		expect(result.map((def) => def.key)).toEqual([
			'compatibility',
			'category',
			'type',
			'fika',
			'state',
			'featured'
		]);
	});

	it('excludes only the pills that are currently active', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), category: 'Weapons' });
		const result = availablePills(filter, defs);
		expect(result.map((def) => def.key)).toEqual(['type', 'fika', 'state', 'featured']);
	});
});

describe('QUICK_TABS', () => {
	it('names the quick tabs in the order the tab strip shows them', () => {
		expect(QUICK_TABS).toEqual([
			{ id: 'all', label: 'All' },
			{ id: 'installed', label: 'Installed' },
			{ id: 'updates', label: 'Updates' },
			{ id: 'favorites', label: 'Favorites' },
			{ id: 'new', label: 'New' }
		]);
	});
});

describe('activeQuickTab', () => {
	it.each([
		['installed', 'installed'],
		['update_available', 'updates'],
		['favorited', 'favorites'],
		['new', 'new']
	] as const)('reports %s as %s', (chip, tab) => {
		const filter = makeFilter({ chips: new Set<ChipId>([chip]) });
		expect(activeQuickTab(filter)).toBe(tab);
	});

	it('reports "all" when none of the quick-tab chips are set', () => {
		expect(activeQuickTab(makeFilter({ chips: new Set<ChipId>(['compatible']) }))).toBe('all');
	});

	it('prefers the first matching chip in priority order when several are set', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['new', 'installed']) });
		expect(activeQuickTab(filter)).toBe('installed');
	});
});

describe('applyQuickTab', () => {
	it('sets the chip for the requested tab', () => {
		const result = applyQuickTab(makeFilter(), 'favorites');
		expect([...result.chips]).toEqual(['favorited']);
	});

	it('replaces only the four tab chips, keeping unrelated chips, category and search', () => {
		const filter = makeFilter({
			chips: new Set<ChipId>(['compatible', 'fika', 'installed']),
			category: 'Weapons',
			search: 'quest'
		});
		const result = applyQuickTab(filter, 'updates');
		expect(result).toEqual({
			chips: new Set<ChipId>(['compatible', 'fika', 'update_available']),
			category: 'Weapons',
			search: 'quest'
		});
	});

	it('applying "all" clears every quick-tab chip', () => {
		const filter = makeFilter({
			chips: new Set<ChipId>(['installed', 'update_available', 'favorited', 'new', 'compatible'])
		});
		const result = applyQuickTab(filter, 'all');
		expect([...result.chips]).toEqual(['compatible']);
	});
});

describe('installableVersions', () => {
	it('keeps the versions the manager can install, in the order the source lists them', () => {
		const releases = [
			makeRelease({ version: '4.1.5' }),
			makeRelease({ version: '4.1.3' }),
			makeRelease({ version: '4.1.0' })
		];

		expect(installableVersions(releases)).toEqual(['4.1.5', '4.1.3', '4.1.0']);
	});

	it('drops a release that has no download, so the pill cannot offer an unreachable version', () => {
		const releases = [
			makeRelease({ version: '4.1.5' }),
			makeRelease({ version: '4.1.4', downloadable: false }),
			makeRelease({ version: '4.1.0' })
		];

		expect(installableVersions(releases)).toEqual(['4.1.5', '4.1.0']);
	});

	it('names a version once even when the source lists it twice', () => {
		const releases = [
			makeRelease({ version: '4.1.5' }),
			makeRelease({ version: '4.1.5', client_build: '39001' })
		];

		expect(installableVersions(releases)).toEqual(['4.1.5']);
	});

	it('reports nothing when no release can be installed', () => {
		expect(installableVersions([makeRelease({ downloadable: false })])).toEqual([]);
		expect(installableVersions([])).toEqual([]);
	});
});

describe('the compatibility pill with installable versions', () => {
	const compatOf = (installable: string[], sptVersion: string | null = '4.1.0'): PillDefinition =>
		defOf(pillDefinitions([], sptVersion, installable), 'compatibility');

	it('offers the installed version first, then every other version, then "All versions"', () => {
		const options = compatOf(['4.1.5', '4.1.3', '4.1.0']).options;

		expect(options).toEqual([
			{ id: 'compatible', label: 'SPT 4.1.0', chips: ['compatible'] },
			{ id: 'spt-4.1.5', label: 'SPT 4.1.5', chips: ['compatible'], sptVersion: '4.1.5' },
			{ id: 'spt-4.1.3', label: 'SPT 4.1.3', chips: ['compatible'], sptVersion: '4.1.3' },
			{ id: 'all', label: 'All versions', chips: [] }
		]);
	});

	it('offers every installable version when no SPT version is installed yet', () => {
		const options = compatOf(['4.1.5', '4.1.0'], null).options;

		expect(options.map((option) => option.id)).toEqual([
			'compatible',
			'spt-4.1.5',
			'spt-4.1.0',
			'all'
		]);
		expect(options[0].label).toBe('Compatible');
	});

	it('leaves the pill at the installed version and "All versions" without any release', () => {
		expect(compatOf([]).options.map((option) => option.id)).toEqual(['compatible', 'all']);
	});
});

describe('the SPT version a filter is judged against', () => {
	const defs = pillDefinitions(['Weapons'], '4.1.0', ['4.1.5', '4.1.3']);
	const compat = defOf(defs, 'compatibility');

	it('reports the picked version as the active compatibility option, not the installed one', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), sptVersion: '4.1.5' });

		expect(activePills(filter, [compat])).toEqual([
			{ key: 'compatibility', optionIds: ['spt-4.1.5'], valueLabel: 'SPT 4.1.5' }
		]);
	});

	it('reports the installed version as active while the filter names no version', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']) });

		expect(activePills(filter, [compat])).toEqual([
			{ key: 'compatibility', optionIds: ['compatible'], valueLabel: 'SPT 4.1.0' }
		]);
	});

	it('reports no compatibility pill for a version the pill does not offer', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), sptVersion: '3.9.0' });

		expect(activePills(filter, [compat])).toEqual([]);
	});

	/**
	 * Every option of the pill except "All versions" means "show what runs on
	 * this version": each one narrows the list with the compatible chip and only
	 * differs in the version the backend judges the entries against.
	 */
	it.each([
		{ pick: 'the installed version', id: 'compatible', version: undefined },
		{ pick: 'a newer version', id: 'spt-4.1.5', version: '4.1.5' },
		{ pick: 'an older version of the same line', id: 'spt-4.1.3', version: '4.1.3' }
	])('picking $pick keeps the compatible chip and records the version', ({ id, version }) => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), search: 'scav' });

		const result = applyPill(filter, compat, [id]);

		expect(result).toEqual({
			chips: new Set<ChipId>(['compatible']),
			category: null,
			search: 'scav',
			sptVersion: version
		});
	});

	it('drops chip and version together when "All versions" is picked', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), sptVersion: '4.1.5' });

		const result = applyPill(filter, compat, ['all']);

		expect([...result.chips]).toEqual([]);
		expect(result.sptVersion).toBeUndefined();
	});

	it('forgets the version when the compatibility pill is removed', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), sptVersion: '4.1.5' });

		expect(removePill(filter, compat).sptVersion).toBeUndefined();
	});

	it('keeps the version when a pill that owns no version is applied', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), sptVersion: '4.1.5' });

		const result = applyPill(filter, defOf(defs, 'state'), ['installed']);

		expect(result.sptVersion).toBe('4.1.5');
	});

	it('keeps the version across a quick-tab switch', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), sptVersion: '4.1.5' });

		expect(applyQuickTab(filter, 'updates').sptVersion).toBe('4.1.5');
	});

	it('offers the compatibility pill again once its version-less state is restored', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), sptVersion: '3.9.0' });

		expect(availablePills(filter, defs).map((def) => def.key)).toContain('compatibility');
	});
});

/**
 * The pill picks the judged version even when the release source offered
 * nothing: a version chosen for another game directory is still in the shared
 * filter, and the pill has to be able to show and clear it.
 */
describe('the compatibility pill without any installable release', () => {
	const compat = defOf(pillDefinitions([], '4.1.0', []), 'compatibility');
	const stale = makeFilter({ chips: new Set<ChipId>(['compatible']), sptVersion: '4.1.5' });

	it('does not let a version it cannot offer pass as the installed version', () => {
		expect(activePills(stale, [compat])).toEqual([]);
	});

	it('clears the version it cannot offer when "All versions" is picked', () => {
		const result = applyPill(stale, compat, ['all']);

		expect(result.sptVersion).toBeUndefined();
		expect([...result.chips]).toEqual([]);
	});

	it('clears the version it cannot offer when the pill is removed', () => {
		expect(removePill(stale, compat).sptVersion).toBeUndefined();
	});

	it('returns to the installed version when its only narrowing option is picked', () => {
		const result = applyPill(stale, compat, ['compatible']);

		expect(result.sptVersion).toBeUndefined();
		expect([...result.chips]).toEqual(['compatible']);
	});
});


/**
 * Setup: `showsInstalledColumns` is asked about every quick tab. Assertion: the
 * two tabs that list installed mods answer true, the other three false. Reason:
 * the installed version and the mod type come from the installation, so the
 * columns that show them are empty on a tab that lists catalog entries.
 */
describe('showsInstalledColumns', () => {
	it.each([
		['installed', true],
		['updates', true],
		['all', false],
		['favorites', false],
		['new', false]
	] as const)('answers %s with %s', (tab, shown) => {
		expect(showsInstalledColumns(tab)).toBe(shown);
	});
});

/**
 * Setup: a sort is carried over to another quick tab. Assertion: only a sort by
 * installed version is dropped, and only for a tab that hides that column.
 * Reason: the column header is the only control for the `installed` sort, so a
 * sort that survives the column would leave the list ordered by a rule the
 * player can no longer see or undo.
 */
describe('sortForTab', () => {
	it('drops a sort by installed version on a tab that hides the column', () => {
		expect(sortForTab({ key: 'installed', descending: true }, 'favorites')).toBeNull();
	});

	it('keeps a sort by installed version on a tab that shows the column', () => {
		expect(sortForTab({ key: 'installed', descending: true }, 'updates')).toEqual({
			key: 'installed',
			descending: true
		});
	});

	it.each(['name', 'latest', 'spt', 'downloads', 'updated'] as const)(
		'keeps a sort by %s on a tab that hides the installed column',
		(key) => {
			expect(sortForTab({ key, descending: false }, 'all')).toEqual({ key, descending: false });
		}
	);

	it('leaves an unsorted list unsorted', () => {
		expect(sortForTab(null, 'all')).toBeNull();
	});
});
