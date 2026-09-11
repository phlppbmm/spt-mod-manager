import { describe, expect, it } from 'vitest';
import { cached, invalidate } from './viewcache';

/**
 * `viewcache.ts` keeps its store in a module-level `Map`, so each test uses
 * a unique game-dir path to stay isolated from state left behind by other
 * tests (mirrors the isolation approach used for other module-singleton
 * caches in this codebase). Loads that are still on their way are driven by
 * promises the test resolves itself, so the sharing of one request between
 * two views is observed rather than timed.
 */

describe('cached', () => {
	it('is a miss on first call and refresh() stores and returns the loaded value', async () => {
		const gameDir = '/games/miss-then-refresh';
		const view = cached(gameDir, 'listMods', [], async () => 'loaded-value');

		expect(view.value).toBeUndefined();

		const result = await view.refresh();

		expect(result).toBe('loaded-value');
	});

	it('is a hit on a later call with the same key and does not call the loader', async () => {
		const gameDir = '/games/hit-no-loader-call';
		const first = cached(gameDir, 'listMods', [], async () => 'stored-value');
		await first.refresh();

		let loaderCalls = 0;
		const second = cached(gameDir, 'listMods', [], async () => {
			loaderCalls += 1;
			return 'unused-value';
		});

		expect(second.value).toBe('stored-value');
		expect(loaderCalls).toBe(0);
	});

	it('background refresh via a fresh loader updates what the next cached() call sees', async () => {
		const gameDir = '/games/background-refresh-2';
		const initial = cached(gameDir, 'listMods', [], async () => 'first-value');
		await initial.refresh();

		const refreshing = cached(gameDir, 'listMods', [], async () => 'second-value');
		await refreshing.refresh();

		const after = cached(gameDir, 'listMods', [], async () => 'unused-value');
		expect(after.value).toBe('second-value');
	});

	it('a second refresh while the first is still on its way shares that one load', async () => {
		const gameDir = '/games/shared-inflight';
		let loaderCalls = 0;
		let release: (value: string) => void = () => {};
		const loader = async (): Promise<string> => {
			loaderCalls += 1;
			return new Promise<string>((resolve) => {
				release = resolve;
			});
		};

		const first = cached(gameDir, 'listMods', [], loader).refresh();
		const second = cached(gameDir, 'listMods', [], loader).refresh();
		release('shared-value');

		expect(await first).toBe('shared-value');
		expect(await second).toBe('shared-value');
		expect(loaderCalls).toBe(1);
	});

	it('a load that failed is not shared, so the next refresh tries again', async () => {
		const gameDir = '/games/failed-inflight';
		let loaderCalls = 0;
		const loader = async (): Promise<string> => {
			loaderCalls += 1;
			if (loaderCalls === 1) {
				throw new Error('source down');
			}
			return 'second-value';
		};

		await expect(cached(gameDir, 'listMods', [], loader).refresh()).rejects.toThrow(
			'source down'
		);

		expect(await cached(gameDir, 'listMods', [], loader).refresh()).toBe('second-value');
		expect(loaderCalls).toBe(2);
	});

	it.each([
		{
			id: 'differing-call-name',
			primed: { gameDir: '/games/isolated-by-name', call: 'listMods', args: [] as unknown[] },
			neighbour: { gameDir: '/games/isolated-by-name', call: 'listProfiles', args: [] as unknown[] }
		},
		{
			id: 'differing-args',
			primed: { gameDir: '/games/isolated-by-args', call: 'getMod', args: [1] as unknown[] },
			neighbour: { gameDir: '/games/isolated-by-args', call: 'getMod', args: [2] as unknown[] }
		},
		{
			id: 'differing-game-dir',
			primed: { gameDir: '/games/scope-a', call: 'listMods', args: [] as unknown[] },
			neighbour: { gameDir: '/games/scope-b', call: 'listMods', args: [] as unknown[] }
		}
	])('a neighbouring key with $id is a miss', async ({ primed, neighbour }) => {
		const view = cached(primed.gameDir, primed.call, primed.args, async () => 'primed-value');
		await view.refresh();

		const other = cached(neighbour.gameDir, neighbour.call, neighbour.args, async () => 'other');

		expect(other.value).toBeUndefined();
	});
});

describe('invalidate', () => {
	it('drops every entry for the given game dir', async () => {
		const gameDir = '/games/invalidate-target';
		const view = cached(gameDir, 'listMods', [], async () => 'value');
		await view.refresh();

		invalidate(gameDir);

		const after = cached(gameDir, 'listMods', [], async () => 'unused');
		expect(after.value).toBeUndefined();
	});

	it('leaves entries for other game dirs intact', async () => {
		const kept = '/games/invalidate-keep';
		const dropped = '/games/invalidate-drop';
		const keptView = cached(kept, 'listMods', [], async () => 'kept-value');
		await keptView.refresh();
		const droppedView = cached(dropped, 'listMods', [], async () => 'dropped-value');
		await droppedView.refresh();

		invalidate(dropped);

		const afterKept = cached(kept, 'listMods', [], async () => 'unused');
		expect(afterKept.value).toBe('kept-value');
	});

	it('drops a load that is still on its way, so the next refresh starts its own', async () => {
		const gameDir = '/games/invalidate-inflight';
		let loaderCalls = 0;
		const releases: ((value: string) => void)[] = [];
		const loader = async (): Promise<string> => {
			loaderCalls += 1;
			return new Promise<string>((resolve) => {
				releases.push(resolve);
			});
		};

		const first = cached(gameDir, 'listMods', [], loader).refresh();
		invalidate(gameDir);
		const second = cached(gameDir, 'listMods', [], loader).refresh();
		for (const resolve of releases) {
			resolve('value');
		}
		await Promise.all([first, second]);

		expect(loaderCalls).toBe(2);
	});

	it('is a no-op when invalidating a game dir that has no entries', () => {
		expect(() => invalidate('/games/never-cached')).not.toThrow();
	});
});
