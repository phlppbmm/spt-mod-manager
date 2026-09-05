import { beforeEach, describe, expect, it } from 'vitest';
import { DEFAULT_FILTER, clearFilter, getFilter, isDefaultFilter, setFilter } from './filterstate';
import type { ChipId, ModFilter } from './modfilter';

beforeEach(() => {
	clearFilter();
});

describe('DEFAULT_FILTER', () => {
	it('has the compatible chip active, no category and an empty search', () => {
		expect(DEFAULT_FILTER).toEqual({ chips: new Set(['compatible']), category: null, search: '' });
	});
});

describe('getFilter and setFilter', () => {
	it('getFilter returns the default filter before anything was set', () => {
		expect(getFilter()).toEqual({ chips: new Set(['compatible']), category: null, search: '' });
	});

	it('mutating a filter returned by getFilter does not leak into a later getFilter call', () => {
		const first = getFilter();
		(first.chips as Set<ChipId>).add('new');

		const second = getFilter();

		expect(second.chips.has('new')).toBe(false);
		expect(second).toEqual({ chips: new Set(['compatible']), category: null, search: '' });
	});

	it('mutating the object passed to setFilter afterwards does not affect the stored filter', () => {
		const custom: ModFilter = {
			chips: new Set<ChipId>(['featured']),
			category: 'Weapons',
			search: 'foo'
		};
		setFilter(custom);

		(custom.chips as Set<ChipId>).add('new');
		custom.search = 'changed';

		const result = getFilter();
		expect(result.chips.has('new')).toBe(false);
		expect([...result.chips]).toEqual(['featured']);
		expect(result.search).toBe('foo');
	});

	it('a filter passed to setFilter is returned by subsequent getFilter calls', () => {
		const custom: ModFilter = {
			chips: new Set<ChipId>(['favorited', 'disabled']),
			category: 'Armor',
			search: 'plate'
		};
		setFilter(custom);

		expect(getFilter()).toEqual({
			chips: new Set(['favorited', 'disabled']),
			category: 'Armor',
			search: 'plate'
		});
		expect(getFilter()).toEqual({
			chips: new Set(['favorited', 'disabled']),
			category: 'Armor',
			search: 'plate'
		});
	});
});

describe('clearFilter', () => {
	it('restores the default filter after a custom filter was set', () => {
		setFilter({ chips: new Set<ChipId>(['new']), category: 'Ammo', search: 'something' });

		clearFilter();

		expect(getFilter()).toEqual({ chips: new Set(['compatible']), category: null, search: '' });
	});
});

describe('isDefaultFilter', () => {
	it.each([
		{
			id: 'a filter matching the default exactly',
			filter: { chips: new Set<ChipId>(['compatible']), category: null, search: '' },
			expected: true
		},
		{
			id: 'a distinct Set instance with the same chip content',
			filter: { chips: new Set<ChipId>(['compatible']), category: null, search: '' },
			expected: true
		},
		{
			id: 'an additional chip on top of the default one',
			filter: { chips: new Set<ChipId>(['compatible', 'featured']), category: null, search: '' },
			expected: false
		},
		{
			id: 'the same chip count but a different chip',
			filter: { chips: new Set<ChipId>(['new']), category: null, search: '' },
			expected: false
		},
		{
			id: 'no active chips at all',
			filter: { chips: new Set<ChipId>(), category: null, search: '' },
			expected: false
		},
		{
			id: 'a non-null category',
			filter: { chips: new Set<ChipId>(['compatible']), category: 'Weapons', search: '' },
			expected: false
		},
		{
			id: 'a non-empty search',
			filter: { chips: new Set<ChipId>(['compatible']), category: null, search: 'ammo' },
			expected: false
		}
	])('is $expected for $id', ({ filter, expected }) => {
		expect(isDefaultFilter(filter)).toBe(expected);
	});
});
