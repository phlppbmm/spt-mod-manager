import { describe, expect, it } from 'vitest';
import {
	QUICK_TABS,
	activePills,
	activeQuickTab,
	applyPill,
	applyQuickTab,
	availablePills,
	pillDefinitions,
	removePill
} from './filterpills';
import type { PillDefinition, PillKey } from './filterpills';
import type { ChipId, ModFilter } from './modfilter';

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

	it('defines type, fika, state and featured pills with fixed options', () => {
		const defs = pillDefinitions([], null);
		expect(defOf(defs, 'type').options).toEqual([
			{ id: 'client', label: 'Client', chips: ['client'] },
			{ id: 'server', label: 'Server', chips: ['server'] }
		]);
		expect(defOf(defs, 'fika').options).toEqual([
			{ id: 'fika', label: 'Compatible', chips: ['fika'] }
		]);
		expect(defOf(defs, 'state').options).toEqual([
			{ id: 'installed', label: 'Installed', chips: ['installed'] },
			{ id: 'disabled', label: 'Disabled', chips: ['disabled'] }
		]);
		expect(defOf(defs, 'featured').options).toEqual([
			{ id: 'featured', label: 'Featured', chips: ['featured'] }
		]);
	});

	it('returns all six pill keys, all of radio kind', () => {
		const defs = pillDefinitions(['Weapons'], null);
		expect(defs.map((def) => def.key)).toEqual([
			'compatibility',
			'category',
			'type',
			'fika',
			'state',
			'featured'
		]);
		expect(defs.every((def) => def.kind === 'radio')).toBe(true);
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

	it('returns every definition when nothing is active', () => {
		expect(availablePills(makeFilter(), defs)).toEqual(defs);
	});

	it('excludes only the pills that are currently active', () => {
		const filter = makeFilter({ chips: new Set<ChipId>(['compatible']), category: 'Weapons' });
		const result = availablePills(filter, defs);
		expect(result.map((def) => def.key)).toEqual(['type', 'fika', 'state', 'featured']);
	});
});

describe('QUICK_TABS', () => {
	it('lists the five quick-tab ids and labels in order', () => {
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
