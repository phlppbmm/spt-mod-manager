/**
 * Unit tests for the thumbnail loader.
 *
 * Setup: the only collaborator, the `get_thumbnails` RPC wrapper, is replaced by
 * a spy; every test starts from a fresh copy of the module, because its session
 * cache, its in-flight table and its batch scheduler are module-level state.
 * Assertions read what `loadThumbnail` resolves with and which batches of mod ids
 * reached the RPC. Reason: the loader exists to turn many independent per-row
 * requests into few RPC calls without ever handing a row the wrong image, and
 * neither the batching, nor the URL-keyed session cache, nor the byte budget of
 * that cache is observable from a single call.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ThumbnailsResponse } from './api/client';

// Hoisted so the same spy survives the `vi.resetModules()` that each test uses
// to get a loader whose session cache has not seen a URL yet.
const client = vi.hoisted(() => ({ getThumbnails: vi.fn() }));

vi.mock('./api/client', () => ({ getThumbnails: client.getThumbnails }));

/** A loader module with an empty session cache. */
async function freshLoader(): Promise<typeof import('./thumbnails')> {
	vi.resetModules();
	return import('./thumbnails');
}

/** The image URL the catalog carries for `modId`; distinct per id. */
function urlOf(modId: number): string {
	return `https://cdn.sp-mod.com/thumbs/${modId}.png`;
}

/** The data URL the fake backend serves for `modId`; distinct per id. */
function dataUrlOf(modId: number): string {
	return `data:image/png;base64,thumb-${modId}`;
}

/** The mod ids of every batch that reached the RPC, in call order. */
function requestedBatches(): number[][] {
	return client.getThumbnails.mock.calls.map((call) => call[0] as number[]);
}

/** Answers with `dataUrlOf` for every requested mod id, as the backend does. */
function serveEveryId(): void {
	client.getThumbnails.mockImplementation(
		(modIds: number[]): Promise<ThumbnailsResponse> =>
			Promise.resolve({
				thumbnails: Object.fromEntries(modIds.map((modId) => [String(modId), dataUrlOf(modId)]))
			})
	);
}

/** Answers with a fixed table; ids missing from it are absent from the response. */
function serveTable(table: Record<string, string | null>): void {
	client.getThumbnails.mockResolvedValue({ thumbnails: table } satisfies ThumbnailsResponse);
}

interface Deferred {
	promise: Promise<ThumbnailsResponse>;
	settle: (modIds: number[]) => void;
}

/** A response the test settles by hand, to observe the loader mid-batch. */
function deferredResponse(): Deferred {
	let resolve: (response: ThumbnailsResponse) => void = () => undefined;
	const promise = new Promise<ThumbnailsResponse>((fulfil) => {
		resolve = fulfil;
	});
	return {
		promise,
		settle: (modIds) =>
			resolve({
				thumbnails: Object.fromEntries(modIds.map((modId) => [String(modId), dataUrlOf(modId)]))
			})
	};
}

/** Lets a pending promise chain settle without letting the coalescing window run out. */
function tick(): Promise<void> {
	return Promise.resolve();
}

/** The window in which further requests join the batch that is about to go out. */
const COALESCE_MS = 50;

/** Runs out the coalescing window on the fake clock, so the batch goes out. */
async function openBurst(): Promise<void> {
	await vi.advanceTimersByTimeAsync(COALESCE_MS);
}

/**
 * A data URL of at least `chars` characters. It is built by doubling so the
 * engine keeps it a rope: only the length matters to the cache budget.
 */
function dataUrlOfSize(modId: number, chars: number): string {
	let text = `data:image/png;base64,${modId}-`;
	while (text.length < chars) {
		text += text;
	}
	return text;
}

/** Answers with an image of a given size per mod id. */
function serveSizes(sizes: Record<number, string>): void {
	client.getThumbnails.mockImplementation(
		(modIds: number[]): Promise<ThumbnailsResponse> =>
			Promise.resolve({
				thumbnails: Object.fromEntries(
					modIds.map((modId) => [String(modId), sizes[modId] ?? null])
				)
			})
	);
}

beforeEach(() => {
	vi.clearAllMocks();
	client.getThumbnails.mockReset();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('loadThumbnail', () => {
	it('resolves with the data URL the RPC returned for exactly the requested mod id', async () => {
		const { loadThumbnail } = await freshLoader();
		serveTable({ '47': 'data:image/png;base64,forty-seven' });

		await expect(loadThumbnail(47, urlOf(47))).resolves.toBe('data:image/png;base64,forty-seven');
		expect(requestedBatches()).toEqual([[47]]);
	});

	it('resolves with null for a mod id the response leaves out, so the row falls back', async () => {
		const { loadThumbnail } = await freshLoader();
		serveTable({ '11': dataUrlOf(11) });

		await expect(loadThumbnail(23, urlOf(23))).resolves.toBeNull();
	});

	it('resolves with null for a mod id the response maps to null', async () => {
		const { loadThumbnail } = await freshLoader();
		serveTable({ '23': null });

		await expect(loadThumbnail(23, urlOf(23))).resolves.toBeNull();
	});

	it('gives each mod of one batch its own data URL and never another mod’s', async () => {
		const { loadThumbnail } = await freshLoader();
		serveEveryId();

		const first = loadThumbnail(47, urlOf(47));
		const second = loadThumbnail(63, urlOf(63));

		await expect(first).resolves.toBe(dataUrlOf(47));
		await expect(second).resolves.toBe(dataUrlOf(63));
	});
});

describe('coalescing requests of the same tick', () => {
	it('turns two mods asked for in one tick into a single RPC call carrying both ids', async () => {
		const { loadThumbnail } = await freshLoader();
		serveEveryId();

		await Promise.all([loadThumbnail(47, urlOf(47)), loadThumbnail(63, urlOf(63))]);

		expect(requestedBatches()).toEqual([[47, 63]]);
	});

	it('asks once for an image two rows request before the first answer arrives', async () => {
		const { loadThumbnail } = await freshLoader();
		const first = deferredResponse();
		client.getThumbnails.mockReturnValueOnce(first.promise);

		const rowA = loadThumbnail(47, urlOf(47));
		await tick();
		const rowB = loadThumbnail(47, urlOf(47));
		first.settle([47]);

		await expect(rowA).resolves.toBe(dataUrlOf(47));
		await expect(rowB).resolves.toBe(dataUrlOf(47));
		expect(requestedBatches()).toEqual([[47]]);
	});
});

/**
 * Rows become visible over several frames while the list scrolls, and each RPC
 * call is a process spawn, so the loader waits a short moment before it sends
 * the first batch — but never once a batch is already on its way.
 */
describe('the window that opens a burst', () => {
	it('sends one call for ids that trickle in across separate turns of the window', async () => {
		vi.useFakeTimers();
		const { loadThumbnail } = await freshLoader();
		serveEveryId();

		const first = loadThumbnail(47, urlOf(47));
		await vi.advanceTimersByTimeAsync(20);
		const second = loadThumbnail(63, urlOf(63));
		await vi.advanceTimersByTimeAsync(20);
		const third = loadThumbnail(88, urlOf(88));
		await openBurst();

		await expect(Promise.all([first, second, third])).resolves.toEqual([
			dataUrlOf(47),
			dataUrlOf(63),
			dataUrlOf(88)
		]);
		expect(requestedBatches()).toEqual([[47, 63, 88]]);
	});

	it('sends an id that arrived during a running call without waiting the window again', async () => {
		vi.useFakeTimers();
		const { loadThumbnail } = await freshLoader();
		const inflight = deferredResponse();
		serveEveryId();
		client.getThumbnails.mockReturnValueOnce(inflight.promise);

		const first = loadThumbnail(47, urlOf(47));
		await openBurst();
		const late = loadThumbnail(63, urlOf(63));
		inflight.settle([47]);
		await first;
		await tick();

		expect(requestedBatches()).toEqual([[47], [63]]);
		await expect(late).resolves.toBe(dataUrlOf(63));
	});
});

describe('the session cache, keyed by image URL', () => {
	it('serves a second request for a known URL from the cache, without a further call', async () => {
		const { loadThumbnail } = await freshLoader();
		serveEveryId();
		await loadThumbnail(47, urlOf(47));

		await expect(loadThumbnail(47, urlOf(47))).resolves.toBe(dataUrlOf(47));
		expect(requestedBatches()).toEqual([[47]]);
	});

	it('fetches again when a catalog sync gave the same mod a new image URL', async () => {
		const { loadThumbnail } = await freshLoader();
		serveTable({ '47': 'data:image/png;base64,the-old-image' });
		await loadThumbnail(47, 'https://cdn.sp-mod.com/thumbs/47-old.png');
		serveTable({ '47': 'data:image/png;base64,the-new-image' });

		const reloaded = await loadThumbnail(47, 'https://cdn.sp-mod.com/thumbs/47-new.png');

		expect(reloaded).toBe('data:image/png;base64,the-new-image');
		expect(requestedBatches()).toEqual([[47], [47]]);
	});

	it('remembers "this mod has no image" and does not ask for it again', async () => {
		const { loadThumbnail } = await freshLoader();
		serveTable({});
		await loadThumbnail(47, urlOf(47));

		await expect(loadThumbnail(47, urlOf(47))).resolves.toBeNull();
		expect(requestedBatches()).toEqual([[47]]);
	});

	it('asks only for the images it has not seen when a scrolled-back row returns', async () => {
		const { loadThumbnail } = await freshLoader();
		serveEveryId();
		await Promise.all([loadThumbnail(47, urlOf(47)), loadThumbnail(63, urlOf(63))]);

		await Promise.all([loadThumbnail(47, urlOf(47)), loadThumbnail(88, urlOf(88))]);

		expect(requestedBatches()).toEqual([[47, 63], [88]]);
	});
});

describe('batching beyond the batch size', () => {
	it('splits 40 mods into a batch of 32 and a batch of 8, each answered correctly', async () => {
		const { loadThumbnail } = await freshLoader();
		serveEveryId();
		const modIds = Array.from({ length: 40 }, (_unused, index) => 100 + index);

		const resolved = await Promise.all(modIds.map((modId) => loadThumbnail(modId, urlOf(modId))));

		expect(requestedBatches()).toEqual([modIds.slice(0, 32), modIds.slice(32)]);
		expect(resolved).toEqual(modIds.map(dataUrlOf));
	});

	it('holds the second batch back until the first has answered, then sends it right away', async () => {
		vi.useFakeTimers();
		const { loadThumbnail } = await freshLoader();
		const modIds = Array.from({ length: 40 }, (_unused, index) => 100 + index);
		const first = deferredResponse();
		serveEveryId();
		client.getThumbnails.mockReturnValueOnce(first.promise);

		const all = Promise.all(modIds.map((modId) => loadThumbnail(modId, urlOf(modId))));
		await openBurst();

		expect(requestedBatches()).toEqual([modIds.slice(0, 32)]);

		first.settle(modIds.slice(0, 32));
		await all;

		expect(requestedBatches()).toEqual([modIds.slice(0, 32), modIds.slice(32)]);
	});
});

/**
 * The cache is bounded by the size of the images it holds, not by their number:
 * 64 MB, which an ordinary catalog never reaches. The tests below therefore use
 * a handful of images that are large enough to cross the budget together.
 */
describe('the memory budget of the session cache', () => {
	const HUGE = 40 * 1024 * 1024;
	const HALF = 24 * 1024 * 1024;

	it('drops the image read longest ago once the next one no longer fits', async () => {
		const { loadThumbnail } = await freshLoader();
		serveSizes({ 1: dataUrlOfSize(1, HUGE), 2: dataUrlOfSize(2, HUGE) });
		await loadThumbnail(1, urlOf(1));
		await loadThumbnail(2, urlOf(2));
		client.getThumbnails.mockClear();

		await Promise.all([loadThumbnail(1, urlOf(1)), loadThumbnail(2, urlOf(2))]);

		expect(requestedBatches()).toEqual([[1]]);
	});

	it('keeps an image that was read again, dropping the next-oldest one instead', async () => {
		const { loadThumbnail } = await freshLoader();
		serveSizes({
			1: dataUrlOfSize(1, HALF),
			2: dataUrlOfSize(2, HALF),
			3: dataUrlOfSize(3, HALF)
		});
		await loadThumbnail(1, urlOf(1));
		await loadThumbnail(2, urlOf(2));
		await loadThumbnail(1, urlOf(1));
		await loadThumbnail(3, urlOf(3));
		client.getThumbnails.mockClear();

		await Promise.all([loadThumbnail(1, urlOf(1)), loadThumbnail(2, urlOf(2))]);

		expect(requestedBatches()).toEqual([[2]]);
	});

	it('never evicts an image because mods without one were remembered', async () => {
		const { loadThumbnail } = await freshLoader();
		const withoutImage = Array.from({ length: 100 }, (_unused, index) => 500 + index);
		serveSizes({ 1: dataUrlOfSize(1, HUGE) });
		await loadThumbnail(1, urlOf(1));
		await Promise.all(withoutImage.map((modId) => loadThumbnail(modId, urlOf(modId))));
		client.getThumbnails.mockClear();

		await expect(loadThumbnail(1, urlOf(1))).resolves.toBe(dataUrlOfSize(1, HUGE));
		expect(requestedBatches()).toEqual([]);
	});

	it('does not keep an image that would fill the whole budget on its own', async () => {
		const { loadThumbnail } = await freshLoader();
		serveSizes({ 1: dataUrlOfSize(1, 80 * 1024 * 1024) });
		await loadThumbnail(1, urlOf(1));
		client.getThumbnails.mockClear();

		await loadThumbnail(1, urlOf(1));

		expect(requestedBatches()).toEqual([[1]]);
	});
});

describe('a failing RPC', () => {
	it('resolves every mod of the failed batch with null instead of rejecting the rows', async () => {
		const { loadThumbnail } = await freshLoader();
		client.getThumbnails.mockRejectedValue(new Error('the backend is down'));

		const resolved = await Promise.all([
			loadThumbnail(47, urlOf(47)),
			loadThumbnail(63, urlOf(63))
		]);

		expect(resolved).toEqual([null, null]);
	});

	it('keeps loading the following batch after one batch failed', async () => {
		const { loadThumbnail } = await freshLoader();
		const modIds = Array.from({ length: 40 }, (_unused, index) => 100 + index);
		client.getThumbnails.mockRejectedValueOnce(new Error('the backend is down'));
		serveEveryId();

		const resolved = await Promise.all(modIds.map((modId) => loadThumbnail(modId, urlOf(modId))));

		expect(resolved.slice(0, 32)).toEqual(modIds.slice(0, 32).map(() => null));
		expect(resolved.slice(32)).toEqual(modIds.slice(32).map(dataUrlOf));
	});

	it('does not remember a failure: the next request for that image is tried again', async () => {
		const { loadThumbnail } = await freshLoader();
		client.getThumbnails.mockRejectedValueOnce(new Error('the backend is down'));
		await expect(loadThumbnail(47, urlOf(47))).resolves.toBeNull();
		serveEveryId();

		await expect(loadThumbnail(47, urlOf(47))).resolves.toBe(dataUrlOf(47));
		expect(requestedBatches()).toEqual([[47], [47]]);
	});
});
