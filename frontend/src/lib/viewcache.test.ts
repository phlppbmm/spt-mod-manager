import { describe, expect, it } from 'vitest';
import { cached, invalidate } from './viewcache';

/**
 * `viewcache.ts` keeps its store in a module-level `Map`, so each test uses
 * a unique game-dir path to stay isolated from state left behind by other
 * tests (mirrors the isolation approach used for other module-singleton
 * caches in this codebase).
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

	it('isolates keys by call name for the same game dir and args', async () => {
		const gameDir = '/games/isolated-by-name';
		const mods = cached(gameDir, 'listMods', [], async () => 'mods-value');
		await mods.refresh();

		const profiles = cached(gameDir, 'listProfiles', [], async () => 'profiles-value');

		expect(profiles.value).toBeUndefined();
	});

	it('isolates keys by args for the same game dir and call name', async () => {
		const gameDir = '/games/isolated-by-args';
		const withArgOne = cached(gameDir, 'getMod', [1], async () => 'mod-one');
		await withArgOne.refresh();

		const withArgTwo = cached(gameDir, 'getMod', [2], async () => 'mod-two');

		expect(withArgTwo.value).toBeUndefined();
	});

	it('scopes entries by game dir so a second game dir starts as a miss', async () => {
		const gameDirA = '/games/scope-a';
		const gameDirB = '/games/scope-b';
		const viewA = cached(gameDirA, 'listMods', [], async () => 'value-a');
		await viewA.refresh();

		const viewB = cached(gameDirB, 'listMods', [], async () => 'value-b');

		expect(viewB.value).toBeUndefined();
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

	it('is a no-op when invalidating a game dir that has no entries', () => {
		expect(() => invalidate('/games/never-cached')).not.toThrow();
	});
});
