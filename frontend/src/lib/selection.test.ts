import { describe, expect, it, vi } from 'vitest';
import {
	bulkActions,
	headerCheckbox,
	pruneSelection,
	runBulk,
	toggleAll,
	toggleSelection
} from './selection';
import type { BulkTarget } from './selection';

function makeTarget(overrides: Partial<BulkTarget> = {}): BulkTarget {
	return { modId: 1, name: 'Mod A', state: 'active', ...overrides };
}

describe('toggleSelection', () => {
	it('adds an id that is not selected', () => {
		const result = toggleSelection(new Set(), 1);
		expect(result).toEqual(new Set([1]));
	});

	it('removes an id that is already selected', () => {
		const result = toggleSelection(new Set([1, 2]), 1);
		expect(result).toEqual(new Set([2]));
	});

	it('does not mutate the input set', () => {
		const input = new Set([1]);
		toggleSelection(input, 2);
		expect(input).toEqual(new Set([1]));
	});
});

describe('toggleAll', () => {
	it.each([
		{ id: 'none-selected-selects-all', selected: [], ids: [1, 2, 3], expected: [1, 2, 3] },
		{ id: 'some-selected-selects-all', selected: [1], ids: [1, 2, 3], expected: [1, 2, 3] },
		{ id: 'all-selected-clears', selected: [1, 2, 3], ids: [1, 2, 3], expected: [] },
		{ id: 'superset-selected-clears', selected: [1, 2, 3, 99], ids: [1, 2, 3], expected: [] },
		{ id: 'empty-id-list-nothing-selected', selected: [], ids: [], expected: [] },
		{ id: 'empty-id-list-with-a-selection', selected: [1], ids: [], expected: [] }
	])('$id', ({ selected, ids, expected }) => {
		expect(toggleAll(new Set(selected), ids)).toEqual(new Set(expected));
	});

	it('does not mutate the input set', () => {
		const input = new Set([1]);
		toggleAll(input, [1, 2]);
		expect(input).toEqual(new Set([1]));
	});
});

describe('pruneSelection', () => {
	it('keeps only ids that are both selected and still present', () => {
		const result = pruneSelection(new Set([1, 2, 3]), [2, 3, 4]);
		expect(result).toEqual(new Set([2, 3]));
	});

	it('drops selected ids that are no longer in the visible id list', () => {
		const result = pruneSelection(new Set([1, 2]), [2]);
		expect(result).toEqual(new Set([2]));
	});

	it('returns an empty set when the id list is empty', () => {
		expect(pruneSelection(new Set([1, 2]), [])).toEqual(new Set());
	});

	it('returns an empty set when the selection is empty', () => {
		expect(pruneSelection(new Set(), [1, 2])).toEqual(new Set());
	});

	it('does not mutate the input set', () => {
		const input = new Set([1, 2]);
		pruneSelection(input, [1]);
		expect(input).toEqual(new Set([1, 2]));
	});
});

describe('headerCheckbox', () => {
	it.each([
		{ id: 'nothing-selected', selected: [], ids: [1, 2], expected: 'none' },
		{ id: 'empty-id-list', selected: [1], ids: [], expected: 'none' },
		{ id: 'subset-selected', selected: [1], ids: [1, 2], expected: 'some' },
		{ id: 'all-selected', selected: [1, 2], ids: [1, 2], expected: 'all' },
		{ id: 'selection-outside-the-list', selected: [1, 99], ids: [1, 2], expected: 'some' }
	])('$id yields $expected', ({ selected, ids, expected }) => {
		expect(headerCheckbox(new Set(selected), ids)).toBe(expected);
	});
});

describe('bulkActions', () => {
	it('returns an empty list for no targets', () => {
		expect(bulkActions([])).toEqual([]);
	});

	it('produces an enable action for disabled rows only', () => {
		const targets = [makeTarget({ name: 'Alpha', state: 'disabled' })];
		expect(bulkActions(targets)).toEqual([
			{ id: 'enable', label: 'Enable (1)', names: ['Alpha'] }
		]);
	});

	it('produces a disable action for active and update_available rows', () => {
		const targets = [
			makeTarget({ name: 'Alpha', state: 'active' }),
			makeTarget({ name: 'Beta', state: 'update_available' })
		];
		expect(bulkActions(targets)).toEqual([
			{ id: 'disable', label: 'Disable (2)', names: ['Alpha', 'Beta'] },
			{ id: 'update', label: 'Update selected (1)', names: ['Beta'] }
		]);
	});

	it('omits actions whose name list would be empty', () => {
		const targets = [makeTarget({ name: 'Incompat', state: 'incompatible' })];
		expect(bulkActions(targets)).toEqual([]);
	});

	it('excludes available rows from every action', () => {
		const targets = [makeTarget({ name: 'Avail', state: 'available' })];
		expect(bulkActions(targets)).toEqual([]);
	});

	it('returns actions in the fixed order enable, disable, update', () => {
		const targets = [
			makeTarget({ name: 'Disabled', state: 'disabled' }),
			makeTarget({ name: 'Active', state: 'active' }),
			makeTarget({ name: 'Updatable', state: 'update_available' })
		];
		expect(bulkActions(targets).map((a) => a.id)).toEqual(['enable', 'disable', 'update']);
	});

	it('does not mutate the input array', () => {
		const targets = [makeTarget({ name: 'Alpha', state: 'disabled' })];
		const copy = [...targets];
		bulkActions(targets);
		expect(targets).toEqual(copy);
	});
});

describe('runBulk', () => {
	it('runs names strictly sequentially, never starting the next before the previous ends', async () => {
		const log: string[] = [];
		const gates = new Map<string, () => void>();
		const run = vi.fn((name: string) => {
			log.push(`start:${name}`);
			return new Promise<void>((resolve) => {
				gates.set(name, () => {
					log.push(`end:${name}`);
					resolve();
				});
			});
		});
		const onProgress = vi.fn();

		const resultPromise = runBulk(['Alpha', 'Beta'], run, onProgress);

		await vi.waitFor(() => expect(log).toEqual(['start:Alpha']));
		expect(gates.has('Beta')).toBe(false);

		gates.get('Alpha')!();
		await vi.waitFor(() => expect(log).toEqual(['start:Alpha', 'end:Alpha', 'start:Beta']));

		gates.get('Beta')!();
		await resultPromise;

		expect(log).toEqual(['start:Alpha', 'end:Alpha', 'start:Beta', 'end:Beta']);
	});

	it('reports 1-based index, total and name to onProgress before each run starts', async () => {
		const calls: Array<[number, number, string]> = [];
		const onProgress = vi.fn((progress: { index: number; total: number; name: string }) => {
			calls.push([progress.index, progress.total, progress.name]);
		});
		const run = vi.fn().mockResolvedValue(undefined);

		await runBulk(['Alpha', 'Beta', 'Gamma'], run, onProgress);

		expect(calls).toEqual([
			[1, 3, 'Alpha'],
			[2, 3, 'Beta'],
			[3, 3, 'Gamma']
		]);
	});

	it('resolves with done listing successes in order and empty failures', async () => {
		const run = vi.fn().mockResolvedValue(undefined);

		const result = await runBulk(['Alpha', 'Beta'], run, vi.fn());

		expect(result).toEqual({ done: ['Alpha', 'Beta'], failed: [] });
	});

	it('records a rejection with an Error cause under name and message', async () => {
		const run = vi.fn().mockRejectedValue(new Error('disk full'));

		const result = await runBulk(['Alpha'], run, vi.fn());

		expect(result).toEqual({ done: [], failed: [{ name: 'Alpha', message: 'disk full' }] });
	});

	it('records a rejection with a non-Error cause by stringifying it', async () => {
		const run = vi.fn().mockRejectedValue('backend exploded');

		const result = await runBulk(['Alpha'], run, vi.fn());

		expect(result).toEqual({
			done: [],
			failed: [{ name: 'Alpha', message: 'backend exploded' }]
		});
	});

	it('continues running later names after an earlier one fails', async () => {
		const run = vi
			.fn()
			.mockRejectedValueOnce(new Error('boom'))
			.mockResolvedValueOnce(undefined)
			.mockRejectedValueOnce(new Error('boom again'));

		const result = await runBulk(['Alpha', 'Beta', 'Gamma'], run, vi.fn());

		expect(result).toEqual({
			done: ['Beta'],
			failed: [
				{ name: 'Alpha', message: 'boom' },
				{ name: 'Gamma', message: 'boom again' }
			]
		});
	});

	it('resolves with empty done and failed and calls neither run nor onProgress for an empty name list', async () => {
		const run = vi.fn();
		const onProgress = vi.fn();

		const result = await runBulk([], run, onProgress);

		expect(result).toEqual({ done: [], failed: [] });
		expect(run).not.toHaveBeenCalled();
		expect(onProgress).not.toHaveBeenCalled();
	});
});
