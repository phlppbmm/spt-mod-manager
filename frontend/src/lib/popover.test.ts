import { describe, expect, it } from 'vitest';
import { popoverKey } from './popover';
import type { PopoverKeyState } from './popover';

function state(overrides: Partial<PopoverKeyState> = {}): PopoverKeyState {
	return { count: 3, index: 0, shift: false, ...overrides };
}

describe('popoverKey', () => {
	it('Escape closes the popover regardless of index or count', () => {
		expect(popoverKey('Escape', state({ count: 0, index: 5 }))).toEqual({
			close: true,
			focusIndex: null,
			preventDefault: true
		});
	});

	it('Tab moves focus to the next index', () => {
		expect(popoverKey('Tab', state({ count: 3, index: 0, shift: false }))).toEqual({
			close: false,
			focusIndex: 1,
			preventDefault: true
		});
	});

	it('Tab wraps from the last index back to the first', () => {
		expect(popoverKey('Tab', state({ count: 3, index: 2, shift: false }))).toEqual({
			close: false,
			focusIndex: 0,
			preventDefault: true
		});
	});

	it('Shift+Tab moves focus to the previous index', () => {
		expect(popoverKey('Tab', state({ count: 3, index: 2, shift: true }))).toEqual({
			close: false,
			focusIndex: 1,
			preventDefault: true
		});
	});

	it('Shift+Tab wraps from the first index back to the last', () => {
		expect(popoverKey('Tab', state({ count: 3, index: 0, shift: true }))).toEqual({
			close: false,
			focusIndex: 2,
			preventDefault: true
		});
	});

	it('Tab is a no-op when there are no focusable elements', () => {
		expect(popoverKey('Tab', state({ count: 0, index: 0, shift: false }))).toEqual({
			close: false,
			focusIndex: null,
			preventDefault: false
		});
	});

	it('other keys are a no-op', () => {
		expect(popoverKey('Enter', state())).toEqual({
			close: false,
			focusIndex: null,
			preventDefault: false
		});
	});
});
