export interface PaletteKeyState {
	count: number;
	index: number;
}

export interface PaletteKeyResult {
	index: number | null;
	run: boolean;
	close: boolean;
	preventDefault: boolean;
}

export function opensPalette(event: { key: string; ctrlKey: boolean; metaKey: boolean }): boolean {
	return (event.key === 'k' || event.key === 'K') && (event.ctrlKey || event.metaKey);
}

/** The input is the only focusable element of the palette, so Tab is swallowed. */
export function paletteKey(key: string, state: PaletteKeyState): PaletteKeyResult {
	if (key === 'ArrowDown' || key === 'ArrowUp') {
		if (state.count === 0) {
			return { index: null, run: false, close: false, preventDefault: true };
		}
		const step = key === 'ArrowDown' ? 1 : -1;
		const next = (((state.index + step) % state.count) + state.count) % state.count;
		return { index: next, run: false, close: false, preventDefault: true };
	}
	if (key === 'Enter') {
		const run = state.count > 0;
		return { index: null, run, close: false, preventDefault: run };
	}
	if (key === 'Escape') {
		return { index: null, run: false, close: true, preventDefault: true };
	}
	if (key === 'Tab') {
		return { index: null, run: false, close: false, preventDefault: true };
	}
	return { index: null, run: false, close: false, preventDefault: false };
}

export function clampIndex(count: number, index: number): number {
	if (count === 0) {
		return 0;
	}
	return Math.min(Math.max(index, 0), count - 1);
}
