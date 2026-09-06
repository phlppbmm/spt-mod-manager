export interface PopoverKeyState {
	count: number;
	index: number;
	shift: boolean;
}

export interface PopoverKeyResult {
	close: boolean;
	focusIndex: number | null;
	preventDefault: boolean;
}

/** Escape closes; Tab cycles through the focusable elements of the panel. */
export function popoverKey(key: string, state: PopoverKeyState): PopoverKeyResult {
	if (key === 'Escape') {
		return { close: true, focusIndex: null, preventDefault: true };
	}
	if (key === 'Tab' && state.count > 0) {
		const step = state.shift ? -1 : 1;
		const next = (((state.index + step) % state.count) + state.count) % state.count;
		return { close: false, focusIndex: next, preventDefault: true };
	}
	return { close: false, focusIndex: null, preventDefault: false };
}
