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

	it('ArrowDown is a no-op when there are no rows', () => {
		expect(rowKey('ArrowDown', state({ ids: [], focusId: null }))).toEqual(IGNORED);
	});

	it('ArrowUp is a no-op when there are no rows', () => {
		expect(rowKey('ArrowUp', state({ ids: [], focusId: null }))).toEqual(IGNORED);
	});

	it('ArrowDown from no focus moves to the first row', () => {
		expect(rowKey('ArrowDown', state({ focusId: null }))).toEqual({
			focusId: 10,
			action: null,
			preventDefault: true,
			handled: true
		});
	});

	it('ArrowDown moves from the middle row to the next one', () => {
		expect(rowKey('ArrowDown', state({ focusId: 20 }))).toEqual({
			focusId: 30,
			action: null,
			preventDefault: true,
			handled: true
		});
	});

	it('ArrowDown stays on the last row', () => {
		expect(rowKey('ArrowDown', state({ focusId: 30 }))).toEqual({
			focusId: 30,
			action: null,
			preventDefault: true,
			handled: true
		});
	});

	it('ArrowUp from no focus moves to the last row', () => {
		expect(rowKey('ArrowUp', state({ focusId: null }))).toEqual({
			focusId: 30,
			action: null,
			preventDefault: true,
			handled: true
		});
	});

	it('ArrowUp moves from the middle row to the previous one', () => {
		expect(rowKey('ArrowUp', state({ focusId: 20 }))).toEqual({
			focusId: 10,
			action: null,
			preventDefault: true,
			handled: true
		});
	});

	it('ArrowUp stays on the first row', () => {
		expect(rowKey('ArrowUp', state({ focusId: 10 }))).toEqual({
			focusId: 10,
			action: null,
			preventDefault: true,
			handled: true
		});
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
