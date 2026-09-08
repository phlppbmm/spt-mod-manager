import { describe, expect, it } from 'vitest';
import { clampIndex, opensPalette, paletteKey } from './palettekeys';
import type { PaletteKeyState } from './palettekeys';

function state(overrides: Partial<PaletteKeyState> = {}): PaletteKeyState {
	return { count: 3, index: 0, ...overrides };
}

describe('opensPalette', () => {
	it.each([
		['k', true, false],
		['K', true, false],
		['k', false, true],
		['K', false, true]
	])('key %s with ctrlKey=%s metaKey=%s opens the palette', (key, ctrlKey, metaKey) => {
		expect(opensPalette({ key, ctrlKey, metaKey })).toBe(true);
	});

	it('does not open for a plain k without ctrl or meta', () => {
		expect(opensPalette({ key: 'k', ctrlKey: false, metaKey: false })).toBe(false);
	});

	it('does not open for an unrelated key even with ctrl held', () => {
		expect(opensPalette({ key: 'j', ctrlKey: true, metaKey: false })).toBe(false);
	});

	it('does not open for the modifier key itself', () => {
		expect(opensPalette({ key: 'Control', ctrlKey: true, metaKey: false })).toBe(false);
	});
});

describe('paletteKey', () => {
	it.each([
		{
			id: 'ArrowDown-empty-list',
			key: 'ArrowDown',
			count: 0,
			index: 0,
			expected: { index: null, run: false, close: false, preventDefault: true }
		},
		{
			id: 'ArrowUp-empty-list',
			key: 'ArrowUp',
			count: 0,
			index: 0,
			expected: { index: null, run: false, close: false, preventDefault: true }
		},
		{
			id: 'ArrowDown-next',
			key: 'ArrowDown',
			count: 3,
			index: 0,
			expected: { index: 1, run: false, close: false, preventDefault: true }
		},
		{
			id: 'ArrowDown-wraps',
			key: 'ArrowDown',
			count: 3,
			index: 2,
			expected: { index: 0, run: false, close: false, preventDefault: true }
		},
		{
			id: 'ArrowUp-previous',
			key: 'ArrowUp',
			count: 3,
			index: 2,
			expected: { index: 1, run: false, close: false, preventDefault: true }
		},
		{
			id: 'ArrowUp-wraps',
			key: 'ArrowUp',
			count: 3,
			index: 0,
			expected: { index: 2, run: false, close: false, preventDefault: true }
		},
		{
			id: 'Enter-runs',
			key: 'Enter',
			count: 1,
			index: 0,
			expected: { index: null, run: true, close: false, preventDefault: true }
		},
		{
			id: 'Enter-empty-list',
			key: 'Enter',
			count: 0,
			index: 0,
			expected: { index: null, run: false, close: false, preventDefault: false }
		},
		{
			id: 'Escape-closes',
			key: 'Escape',
			count: 5,
			index: 2,
			expected: { index: null, run: false, close: true, preventDefault: true }
		},
		{
			id: 'Tab-swallowed',
			key: 'Tab',
			count: 3,
			index: 0,
			expected: { index: null, run: false, close: false, preventDefault: true }
		},
		{
			id: 'other-key-inert',
			key: 'a',
			count: 3,
			index: 0,
			expected: { index: null, run: false, close: false, preventDefault: false }
		}
	])('$id', ({ key, count, index, expected }) => {
		expect(paletteKey(key, state({ count, index }))).toEqual(expected);
	});
});

describe('clampIndex', () => {
	it('returns 0 for an empty list regardless of the requested index', () => {
		expect(clampIndex(0, 5)).toBe(0);
		expect(clampIndex(0, -3)).toBe(0);
	});

	it('clamps a negative index up to 0', () => {
		expect(clampIndex(4, -1)).toBe(0);
	});

	it('clamps an index beyond the end down to the last valid index', () => {
		expect(clampIndex(4, 10)).toBe(3);
	});

	it('leaves an in-range index unchanged', () => {
		expect(clampIndex(4, 2)).toBe(2);
	});
});
