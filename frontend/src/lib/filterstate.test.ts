import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
	DEFAULT_FILTER,
	clearFilter,
	filtersEqual,
	getFilter,
	isDefaultFilter,
	setFilter,
	subscribeFilter
} from './filterstate';
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

describe('filtersEqual', () => {
	it('is true for two independently built filters with the same category, search and chip content', () => {
		const a: ModFilter = { chips: new Set<ChipId>(['compatible', 'featured']), category: 'Weapons', search: 'ammo' };
		const b: ModFilter = { chips: new Set<ChipId>(['featured', 'compatible']), category: 'Weapons', search: 'ammo' };

		expect(filtersEqual(a, b)).toBe(true);
	});

	it.each([
		{
			id: 'a different category',
			a: { chips: new Set<ChipId>(['featured']), category: 'Weapons', search: 'ammo' },
			b: { chips: new Set<ChipId>(['featured']), category: 'Armor', search: 'ammo' }
		},
		{
			id: 'a different search',
			a: { chips: new Set<ChipId>(['featured']), category: 'Weapons', search: 'ammo' },
			b: { chips: new Set<ChipId>(['featured']), category: 'Weapons', search: 'plate' }
		},
		{
			id: 'a different chip count',
			a: { chips: new Set<ChipId>(['featured']), category: 'Weapons', search: 'ammo' },
			b: { chips: new Set<ChipId>(['featured', 'new']), category: 'Weapons', search: 'ammo' }
		},
		{
			id: 'the same chip count with a different chip',
			a: { chips: new Set<ChipId>(['featured']), category: 'Weapons', search: 'ammo' },
			b: { chips: new Set<ChipId>(['new']), category: 'Weapons', search: 'ammo' }
		}
	])('is false for $id', ({ a, b }) => {
		expect(filtersEqual(a, b)).toBe(false);
	});
});

describe('subscribeFilter', () => {
	it('calls the listener immediately with the current filter', () => {
		setFilter({ chips: new Set<ChipId>(['featured']), category: 'Ammo', search: 'x' });
		const listener = vi.fn();

		subscribeFilter(listener);

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith({
			chips: new Set(['featured']),
			category: 'Ammo',
			search: 'x'
		});
	});

	it('notifies subscribers with the new filter when setFilter changes the filter', () => {
		const listener = vi.fn();
		subscribeFilter(listener);
		listener.mockClear();

		setFilter({ chips: new Set<ChipId>(['new']), category: 'Gear', search: 'foo' });

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith({ chips: new Set(['new']), category: 'Gear', search: 'foo' });
	});

	it('does not notify subscribers when setFilter is called with an equivalent filter', () => {
		setFilter({ chips: new Set<ChipId>(['featured']), category: 'Ammo', search: 'x' });
		const listener = vi.fn();
		subscribeFilter(listener);
		listener.mockClear();

		setFilter({ chips: new Set<ChipId>(['featured']), category: 'Ammo', search: 'x' });

		expect(listener).not.toHaveBeenCalled();
	});

	it('does not notify subscribers when clearFilter is called while already at the default filter', () => {
		const listener = vi.fn();
		subscribeFilter(listener);
		listener.mockClear();

		clearFilter();

		expect(listener).not.toHaveBeenCalled();
	});

	it('notifies subscribers when clearFilter changes away from a custom filter', () => {
		setFilter({ chips: new Set<ChipId>(['new']), category: 'Gear', search: 'foo' });
		const listener = vi.fn();
		subscribeFilter(listener);
		listener.mockClear();

		clearFilter();

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith({ chips: new Set(['compatible']), category: null, search: '' });
	});

	it('stops delivering notifications to a subscriber after unsubscribe', () => {
		const listener = vi.fn();
		const unsubscribe = subscribeFilter(listener);
		listener.mockClear();

		unsubscribe();
		setFilter({ chips: new Set<ChipId>(['new']), category: 'Gear', search: 'foo' });

		expect(listener).not.toHaveBeenCalled();
	});

	it('delivers copies that do not alias the stored filter', () => {
		setFilter({ chips: new Set<ChipId>(['featured']), category: 'Ammo', search: 'x' });
		let received: ModFilter | undefined;
		subscribeFilter((filter) => {
			received = filter;
		});

		(received!.chips as Set<ChipId>).add('new');
		received!.search = 'changed';

		expect(getFilter()).toEqual({ chips: new Set(['featured']), category: 'Ammo', search: 'x' });
	});

	it('notifies every registered subscriber', () => {
		const listenerA = vi.fn();
		const listenerB = vi.fn();
		subscribeFilter(listenerA);
		subscribeFilter(listenerB);
		listenerA.mockClear();
		listenerB.mockClear();

		setFilter({ chips: new Set<ChipId>(['new']), category: 'Gear', search: 'foo' });

		expect(listenerA).toHaveBeenCalledTimes(1);
		expect(listenerB).toHaveBeenCalledTimes(1);
	});
});
