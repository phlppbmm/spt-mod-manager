import type { RowState } from './modfilter';

export function toggleSelection(selected: ReadonlySet<number>, id: number): Set<number> {
	const next = new Set(selected);
	if (!next.delete(id)) {
		next.add(id);
	}
	return next;
}

export function toggleAll(selected: ReadonlySet<number>, ids: readonly number[]): Set<number> {
	if (ids.every((id) => selected.has(id))) {
		return new Set();
	}
	return new Set(ids);
}

/**
 * A selection survives filter changes only for the rows that stay visible;
 * a bulk action must never touch a mod the user cannot see.
 */
export function pruneSelection(selected: ReadonlySet<number>, ids: readonly number[]): Set<number> {
	return new Set(ids.filter((id) => selected.has(id)));
}

export function headerCheckbox(
	selected: ReadonlySet<number>,
	ids: readonly number[]
): 'none' | 'some' | 'all' {
	const chosen = ids.filter((id) => selected.has(id)).length;
	if (chosen === 0) {
		return 'none';
	}
	return chosen === ids.length ? 'all' : 'some';
}

export type BulkActionId = 'enable' | 'disable' | 'update';

export interface BulkTarget {
	modId: number;
	name: string;
	state: RowState;
}

export interface BulkAction {
	id: BulkActionId;
	label: string;
	names: string[];
}

export function bulkActions(targets: readonly BulkTarget[]): BulkAction[] {
	const enable = namesOf(targets, ['disabled']);
	const disable = namesOf(targets, ['active', 'update_available']);
	const update = namesOf(targets, ['update_available']);
	const actions: BulkAction[] = [];
	if (enable.length > 0) {
		actions.push({ id: 'enable', label: `Enable (${enable.length})`, names: enable });
	}
	if (disable.length > 0) {
		actions.push({ id: 'disable', label: `Disable (${disable.length})`, names: disable });
	}
	if (update.length > 0) {
		actions.push({
			id: 'update',
			label: `Update selected (${update.length})`,
			names: update
		});
	}
	return actions;
}

function namesOf(targets: readonly BulkTarget[], states: readonly RowState[]): string[] {
	return targets.filter((target) => states.includes(target.state)).map((target) => target.name);
}

export interface BulkProgress {
	index: number;
	total: number;
	name: string;
}

export interface BulkFailure {
	name: string;
	message: string;
}

export interface BulkResult {
	done: string[];
	failed: BulkFailure[];
}

/**
 * One mod at a time: two mod operations at once would queue on the backend
 * lock for minutes. A failing mod is recorded and the run continues.
 */
export async function runBulk(
	names: readonly string[],
	run: (name: string) => Promise<unknown>,
	onProgress: (progress: BulkProgress) => void
): Promise<BulkResult> {
	const done: string[] = [];
	const failed: BulkFailure[] = [];
	for (let index = 0; index < names.length; index += 1) {
		const name = names[index];
		onProgress({ index: index + 1, total: names.length, name });
		try {
			await run(name);
			done.push(name);
		} catch (cause) {
			failed.push({
				name,
				message: cause instanceof Error ? cause.message : String(cause)
			});
		}
	}
	return { done, failed };
}
