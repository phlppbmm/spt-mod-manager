import { describe, expect, it } from 'vitest';
import { popoverKey } from './popover';
import type { PopoverKeyState } from './popover';

function state(overrides: Partial<PopoverKeyState> = {}): PopoverKeyState {
	return { count: 3, index: 0, shift: false, ...overrides };
}

describe('popoverKey', () => {
	it.each([
		{
			id: 'Escape-closes',
			key: 'Escape',
			count: 0,
			index: 5,
			shift: false,
			expected: { close: true, focusIndex: null, preventDefault: true }
		},
		{
			id: 'Tab-next',
			key: 'Tab',
			count: 3,
			index: 0,
			shift: false,
			expected: { close: false, focusIndex: 1, preventDefault: true }
		},
		{
			id: 'Tab-wraps-to-first',
			key: 'Tab',
			count: 3,
			index: 2,
			shift: false,
			expected: { close: false, focusIndex: 0, preventDefault: true }
		},
		{
			id: 'ShiftTab-previous',
			key: 'Tab',
			count: 3,
			index: 2,
			shift: true,
			expected: { close: false, focusIndex: 1, preventDefault: true }
		},
		{
			id: 'ShiftTab-wraps-to-last',
			key: 'Tab',
			count: 3,
			index: 0,
			shift: true,
			expected: { close: false, focusIndex: 2, preventDefault: true }
		},
		{
			id: 'Tab-no-focusables',
			key: 'Tab',
			count: 0,
			index: 0,
			shift: false,
			expected: { close: false, focusIndex: null, preventDefault: false }
		},
		{
			id: 'other-key-noop',
			key: 'Enter',
			count: 3,
			index: 0,
			shift: false,
			expected: { close: false, focusIndex: null, preventDefault: false }
		}
	])('$id', ({ key, count, index, shift, expected }) => {
		expect(popoverKey(key, state({ count, index, shift }))).toEqual(expected);
	});
});
