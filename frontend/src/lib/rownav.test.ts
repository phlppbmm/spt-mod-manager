import { describe, expect, it } from 'vitest';
import { focusAfterRows, rowKey } from './rownav';
import type { RowNavState } from './rownav';

function state(overrides: Partial<RowNavState> = {}): RowNavState {
	return { ids: [10, 20, 30], focusId: null, ...overrides };
}

const IGNORED = { focusId: null, action: null, preventDefault: false, handled: false };

describe('rowKey', () => {
	it('Escape blurs and clears focus regardless of the current state', () => {
		expect(rowKey('Escape', state({ ids: [10, 20], focusId: 20 }))).toEqual({
			focusId: null,
			action: 'blur',
			preventDefault: true,
			handled: true
		});
	});

	it.each([
		{
			id: 'ArrowDown-no-rows',
			key: 'ArrowDown',
			ids: [],
			focusId: null,
			expected: IGNORED
		},
		{
			id: 'ArrowUp-no-rows',
			key: 'ArrowUp',
			ids: [],
			focusId: null,
			expected: IGNORED
		},
		{
			id: 'ArrowDown-from-none',
			key: 'ArrowDown',
			ids: [10, 20, 30],
			focusId: null,
			expected: { focusId: 10, action: null, preventDefault: true, handled: true }
		},
		{
			id: 'ArrowDown-middle',
			key: 'ArrowDown',
			ids: [10, 20, 30],
			focusId: 20,
			expected: { focusId: 30, action: null, preventDefault: true, handled: true }
		},
		{
			id: 'ArrowDown-clamps-at-last',
			key: 'ArrowDown',
			ids: [10, 20, 30],
			focusId: 30,
			expected: { focusId: 30, action: null, preventDefault: true, handled: true }
		},
		{
			id: 'ArrowUp-from-none',
			key: 'ArrowUp',
			ids: [10, 20, 30],
			focusId: null,
			expected: { focusId: 30, action: null, preventDefault: true, handled: true }
		},
		{
			id: 'ArrowUp-middle',
			key: 'ArrowUp',
			ids: [10, 20, 30],
			focusId: 20,
			expected: { focusId: 10, action: null, preventDefault: true, handled: true }
		},
		{
			id: 'ArrowUp-clamps-at-first',
			key: 'ArrowUp',
			ids: [10, 20, 30],
			focusId: 10,
			expected: { focusId: 10, action: null, preventDefault: true, handled: true }
		}
	])('$id', ({ key, ids, focusId, expected }) => {
		expect(rowKey(key, state({ ids, focusId }))).toEqual(expected);
	});

	it.each<[string, string]>([
		['Enter', 'open'],
		[' ', 'toggle-select'],
		['f', 'toggle-favorite'],
		['F', 'toggle-favorite']
	])('%s maps to the %s action on the focused row without moving focus', (key, action) => {
		expect(rowKey(key, state({ focusId: 20 }))).toEqual({
			focusId: 20,
			action,
			preventDefault: true,
			handled: true
		});
	});

	it.each(['Enter', ' ', 'f', 'F'])(
		'%s is a no-op when no row has focus',
		(key) => {
			expect(rowKey(key, state({ focusId: null }))).toEqual(IGNORED);
		}
	);

	it('is a no-op for an unmapped key, even with a focused row', () => {
		expect(rowKey('x', state({ focusId: 20 }))).toEqual(IGNORED);
	});
});

describe('focusAfterRows', () => {
	it('returns null when there was no focus to begin with', () => {
		expect(focusAfterRows([10, 20], null)).toBeNull();
	});

	it('keeps the focused id when it is still present', () => {
		expect(focusAfterRows([10, 20, 30], 20)).toBe(20);
	});

	it('returns null when the focused row no longer exists', () => {
		expect(focusAfterRows([10, 30], 20)).toBeNull();
	});

	it('returns null when the row list becomes empty', () => {
		expect(focusAfterRows([], 20)).toBeNull();
	});
});
