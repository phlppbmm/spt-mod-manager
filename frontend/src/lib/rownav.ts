export type RowAction = 'open' | 'toggle-select' | 'toggle-favorite' | 'blur';

export interface RowNavState {
	ids: readonly number[];
	focusId: number | null;
}

export interface RowKeyResult {
	focusId: number | null;
	action: RowAction | null;
	preventDefault: boolean;
	handled: boolean;
}

const IGNORED: RowKeyResult = {
	focusId: null,
	action: null,
	preventDefault: false,
	handled: false
};

const ROW_ACTIONS: Record<string, RowAction | undefined> = {
	Enter: 'open',
	' ': 'toggle-select',
	f: 'toggle-favorite',
	F: 'toggle-favorite'
};

/** The focused row is remembered by mod id, so filtering and sorting keep it. */
export function rowKey(key: string, state: RowNavState): RowKeyResult {
	if (key === 'Escape') {
		return { focusId: null, action: 'blur', preventDefault: true, handled: true };
	}
	if (key === 'ArrowDown' || key === 'ArrowUp') {
		if (state.ids.length === 0) {
			return IGNORED;
		}
		const at = state.focusId === null ? -1 : state.ids.indexOf(state.focusId);
		const next =
			key === 'ArrowDown'
				? Math.min(at + 1, state.ids.length - 1)
				: at < 0
					? state.ids.length - 1
					: Math.max(at - 1, 0);
		return { focusId: state.ids[next], action: null, preventDefault: true, handled: true };
	}
	const action = ROW_ACTIONS[key];
	if (action === undefined || state.focusId === null) {
		return IGNORED;
	}
	return { focusId: state.focusId, action, preventDefault: true, handled: true };
}

export function focusAfterRows(ids: readonly number[], focusId: number | null): number | null {
	if (focusId === null || !ids.includes(focusId)) {
		return null;
	}
	return focusId;
}
