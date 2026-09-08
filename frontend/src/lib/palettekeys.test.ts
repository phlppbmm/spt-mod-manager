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
	it('ArrowDown does not move and does not run when the list is empty', () => {
		expect(paletteKey('ArrowDown', state({ count: 0, index: 0 }))).toEqual({
			index: null,
			run: false,
			close: false,
			preventDefault: true
		});
	});

	it('ArrowUp does not move and does not run when the list is empty', () => {
		expect(paletteKey('ArrowUp', state({ count: 0, index: 0 }))).toEqual({
			index: null,
			run: false,
			close: false,
			preventDefault: true
		});
	});

	it('ArrowDown moves to the next index', () => {
		expect(paletteKey('ArrowDown', state({ count: 3, index: 0 }))).toEqual({
			index: 1,
			run: false,
			close: false,
			preventDefault: true
		});
	});

	it('ArrowDown wraps from the last index back to the first', () => {
		expect(paletteKey('ArrowDown', state({ count: 3, index: 2 }))).toEqual({
			index: 0,
			run: false,
			close: false,
			preventDefault: true
		});
	});

	it('ArrowUp moves to the previous index', () => {
		expect(paletteKey('ArrowUp', state({ count: 3, index: 2 }))).toEqual({
			index: 1,
			run: false,
			close: false,
			preventDefault: true
		});
	});

	it('ArrowUp wraps from the first index back to the last', () => {
		expect(paletteKey('ArrowUp', state({ count: 3, index: 0 }))).toEqual({
			index: 2,
			run: false,
			close: false,
			preventDefault: true
		});
	});

	it('Enter runs when there is at least one entry', () => {
		expect(paletteKey('Enter', state({ count: 1, index: 0 }))).toEqual({
			index: null,
			run: true,
			close: false,
			preventDefault: true
		});
	});

	it('Enter does not run when the list is empty', () => {
		expect(paletteKey('Enter', state({ count: 0, index: 0 }))).toEqual({
			index: null,
			run: false,
			close: false,
			preventDefault: false
		});
	});

	it('Escape closes the palette', () => {
		expect(paletteKey('Escape', state({ count: 5, index: 2 }))).toEqual({
			index: null,
			run: false,
			close: true,
			preventDefault: true
		});
	});

	it('Tab is swallowed since the input is the only focusable element', () => {
		expect(paletteKey('Tab', state())).toEqual({
			index: null,
			run: false,
			close: false,
			preventDefault: true
		});
	});

	it('is inert for any other key', () => {
		expect(paletteKey('a', state())).toEqual({
			index: null,
			run: false,
			close: false,
			preventDefault: false
		});
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
