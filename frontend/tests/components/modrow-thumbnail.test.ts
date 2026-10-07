/**
 * Component tests for the thumbnail of a mod row, driven through the view that
 * owns the rows.
 *
 * Setup: `ModsView` is mounted with catalog entries whose `thumbnail` field is
 * either a Forge URL or null, and the `get_thumbnails` RPC is answered, delayed
 * or rejected. Assertions read the image the row and the details dialog render,
 * the letter placeholder that stands in for it, and the payloads the RPC
 * received. Reason: the webview cannot load the Forge CDN URL itself, so a row
 * shows an image only if the backend handed it the bytes as a data URL — and the
 * request de-duplication that keeps a scrolling list down to a few RPC calls is
 * only visible across several mounted rows.
 *
 * Every test uses its own mod ids: the loader keeps one session cache per module
 * instance, and that instance is shared by the tests of this file.
 */

import { render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CatalogEntry } from '$lib/api/client';
import { clearFilter } from '$lib/filterstate';
import { requestIntent, takeIntents } from '$lib/intents';
import { setSubNav } from '$lib/shellstate';
import { invalidate } from '$lib/viewcache';

import ModsView from '$lib/components/ModsView.svelte';
import { apiError } from '../support/errors';
import { catalogEntry, catalogMod } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

const GAME_DIR = '/games/SPT';

/** A catalog entry whose mod carries a Forge thumbnail URL the webview cannot use. */
function entryWithThumbnail(id: number, name: string): CatalogEntry {
	return catalogEntry({
		mod: catalogMod({
			id,
			name,
			author: 'Ada',
			thumbnail: `https://cdn.sp-mod.com/thumbs/${id}.png`
		})
	});
}

/** The data URL the backend hands out for `modId`; distinct per id. */
function dataUrlOf(modId: number): string {
	return `data:image/png;base64,thumb-${modId}`;
}

/** Answers `get_thumbnails` for every requested id, the way the backend does. */
function serveThumbnails(): void {
	rpc.handle('get_thumbnails', (payload) => {
		const { mod_ids: modIds } = payload as { mod_ids: number[] };
		return {
			thumbnails: Object.fromEntries(modIds.map((modId) => [String(modId), dataUrlOf(modId)]))
		};
	});
}

function stubView(catalog: CatalogEntry[]): void {
	rpc.returns('browse_catalog', catalog);
	rpc.returns('list_mods', []);
	rpc.returns('list_favorites', { mod_ids: [] });
}

function mount(): { unmount: () => void } {
	const { unmount } = render(ModsView, {
		props: {
			path: GAME_DIR,
			refreshKey: 0,
			locked: false,
			sptVersion: '4.1.0',
			onbusy: () => undefined,
			onupdatable: () => undefined
		}
	});
	return { unmount };
}

/** The grid row of `name`, addressed through its own select checkbox. */
function rowOf(name: string): HTMLElement {
	const row = screen
		.getAllByRole('row')
		.find((candidate) => within(candidate).queryByLabelText(`Select ${name}`) !== null);
	if (row === undefined) {
		throw new Error(`no row for ${name}`);
	}
	return row;
}

/** The row's thumbnail image, or null while the row shows the letter instead. */
function thumbnailOf(name: string): HTMLImageElement | null {
	return rowOf(name).querySelector('img');
}

/** A run of catalog entries, all with an image, rendered in exactly this order. */
function manyEntries(firstId: number, count: number): CatalogEntry[] {
	return Array.from({ length: count }, (_unused, offset) =>
		entryWithThumbnail(firstId + offset, `Mod ${firstId + offset}`)
	);
}

/** The mod ids of every `get_thumbnails` batch so far, flattened into call order. */
function requestedIds(): number[] {
	return rpc
		.payloads('get_thumbnails')
		.flatMap((payload) => (payload as { mod_ids: number[] }).mod_ids);
}

/**
 * A stand-in for the browser's `IntersectionObserver`: happy-dom defines the
 * class but never delivers a callback, so revealing a row is only possible with
 * an observer the test drives itself.
 */
class FakeObserver {
	static readonly instances: FakeObserver[] = [];

	readonly targets: Element[] = [];

	constructor(
		private readonly callback: (entries: { isIntersecting: boolean }[]) => void,
		readonly options: { rootMargin: string }
	) {
		FakeObserver.instances.push(this);
	}

	observe(target: Element): void {
		this.targets.push(target);
	}

	disconnect(): void {
		this.targets.length = 0;
	}

	/** Reports the observed rows as visible, as the browser does when they scroll in. */
	reveal(): void {
		this.callback([{ isIntersecting: false }, { isIntersecting: true }]);
	}
}

/** The observer watching the row of `name`. */
function observerOf(name: string): FakeObserver {
	const observer = FakeObserver.instances.find((candidate) =>
		candidate.targets.includes(rowOf(name))
	);
	if (observer === undefined) {
		throw new Error(`no intersection observer watches the row of ${name}`);
	}
	return observer;
}

/** Waits until the row of `name` is on screen, so the loading effect has run. */
async function awaitRow(name: string): Promise<void> {
	await screen.findByLabelText(`Select ${name}`);
}

afterEach(() => {
	FakeObserver.instances.length = 0;
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setSubNav([], null);
	takeIntents(['reveal-mod']);
});

describe('what the row shows in place of the image', () => {
	it('never asks for image bytes when the mod has no thumbnail at all', async () => {
		stubView([catalogEntry({ mod: catalogMod({ id: 10, name: 'Nothing', thumbnail: null }) })]);
		mount();

		await awaitRow('Nothing');

		expect(rpc.payloads('get_thumbnails')).toEqual([]);
		expect(thumbnailOf('Nothing')).toBeNull();
		expect(within(rowOf('Nothing')).getByText('N')).toBeInTheDocument();
	});

	it('shows the initial letter while the image bytes are on their way and the image once they arrive', async () => {
		let deliver: (response: unknown) => void = () => undefined;
		rpc.handle(
			'get_thumbnails',
			() =>
				new Promise((resolve) => {
					deliver = resolve;
				})
		);
		stubView([entryWithThumbnail(20, 'Waiting')]);
		mount();

		await awaitRow('Waiting');
		await waitFor(() => {
			expect(rpc.payloads('get_thumbnails')).toEqual([{ mod_ids: [20] }]);
		});

		expect(thumbnailOf('Waiting')).toBeNull();
		expect(within(rowOf('Waiting')).getByText('W')).toBeInTheDocument();

		deliver({ thumbnails: { '20': dataUrlOf(20) } });

		await waitFor(() => {
			expect(thumbnailOf('Waiting')).toHaveAttribute('src', dataUrlOf(20));
		});
	});

	it('keeps the letter and opens no failure dialog when the image bytes cannot be fetched', async () => {
		rpc.rejects('get_thumbnails', apiError('The Forge is unreachable.', 'FORGE_DOWN'));
		stubView([entryWithThumbnail(50, 'Broken')]);
		mount();

		await awaitRow('Broken');
		await waitFor(() => {
			expect(rpc.payloads('get_thumbnails')).toEqual([{ mod_ids: [50] }]);
		});

		expect(thumbnailOf('Broken')).toBeNull();
		expect(within(rowOf('Broken')).getByText('B')).toBeInTheDocument();
		expect(screen.queryByRole('dialog')).toBeNull();
	});
});

describe('the loaded image', () => {
	it('puts the data URL the backend sent into the row image', async () => {
		serveThumbnails();
		stubView([entryWithThumbnail(30, 'Loaded')]);
		mount();

		await awaitRow('Loaded');

		await waitFor(() => {
			expect(thumbnailOf('Loaded')).toHaveAttribute('src', dataUrlOf(30));
		});
		expect(rpc.payloads('get_thumbnails')).toEqual([{ mod_ids: [30] }]);
	});

	it('shows the same image in the details dialog of the mod', async () => {
		const user = userEvent.setup();
		serveThumbnails();
		rpc.returns('get_mod_details', { mod: null, versions: [], description: 'A rifle pack.' });
		stubView([entryWithThumbnail(40, 'Detailed')]);
		mount();

		await user.click(await screen.findByRole('button', { name: 'Detailed' }));
		const dialog = await screen.findByRole('dialog', { name: 'Detailed' });

		await waitFor(() => {
			expect(dialog.querySelector('img')).toHaveAttribute('src', dataUrlOf(40));
		});
	});

	it('gives two rows that ask at the same time one RPC call and their own image each', async () => {
		serveThumbnails();
		stubView([entryWithThumbnail(60, 'First'), entryWithThumbnail(61, 'Second')]);
		mount();

		await awaitRow('Second');

		await waitFor(() => {
			expect(thumbnailOf('Second')).toHaveAttribute('src', dataUrlOf(61));
		});
		expect(thumbnailOf('First')).toHaveAttribute('src', dataUrlOf(60));
		expect(rpc.payloads('get_thumbnails')).toEqual([{ mod_ids: [60, 61] }]);
	});

	it('reuses the session cache when a row is left and mounted again, without a second call', async () => {
		serveThumbnails();
		stubView([entryWithThumbnail(70, 'Revisited')]);
		const first = mount();
		await awaitRow('Revisited');
		await waitFor(() => {
			expect(thumbnailOf('Revisited')).toHaveAttribute('src', dataUrlOf(70));
		});

		first.unmount();
		invalidate(GAME_DIR);
		mount();
		await awaitRow('Revisited');

		await waitFor(() => {
			expect(thumbnailOf('Revisited')).toHaveAttribute('src', dataUrlOf(70));
		});
		expect(rpc.payloads('get_thumbnails')).toEqual([{ mod_ids: [70] }]);
	});
});

/**
 * The row index decides whether a row loads its image right away. happy-dom
 * defines `IntersectionObserver` but never delivers a callback, so a gated row
 * stays unloaded here for the same reason an off-screen row does in the browser.
 */
describe('which rows fetch their image', () => {
	it('loads the first 60 rows at once and leaves the 61st to the intersection observer', async () => {
		serveThumbnails();
		const entries = manyEntries(200, 61);
		stubView(entries);
		mount();

		await screen.findByText('61 results');
		await waitFor(() => {
			expect(requestedIds()).toHaveLength(60);
		});

		expect(requestedIds()).toEqual(entries.slice(0, 60).map((entry) => entry.mod.id));
		expect(thumbnailOf('Mod 260')).toBeNull();
		expect(within(rowOf('Mod 260')).getByText('M')).toBeInTheDocument();
		await waitFor(() => {
			expect(thumbnailOf('Mod 259')).toHaveAttribute('src', dataUrlOf(259));
		});
	});

	it('loads every row at once where the browser has no intersection observer', async () => {
		vi.stubGlobal('IntersectionObserver', undefined);
		serveThumbnails();
		const entries = manyEntries(300, 61);
		stubView(entries);
		mount();

		await screen.findByText('61 results');
		await waitFor(() => {
			expect(requestedIds()).toHaveLength(61);
		});

		expect(requestedIds()).toEqual(entries.map((entry) => entry.mod.id));
		await waitFor(() => {
			expect(thumbnailOf('Mod 360')).toHaveAttribute('src', dataUrlOf(360));
		});
	});

	it('keeps the observers it has when a sort moves every row', async () => {
		vi.stubGlobal('IntersectionObserver', FakeObserver);
		serveThumbnails();
		const entries = manyEntries(2000, 120).map((entry, position) => ({
			...entry,
			mod: { ...entry.mod, downloads: position }
		}));
		stubView(entries);
		mount();

		await screen.findByText('120 results');
		const watching = FakeObserver.instances.length;
		expect(watching).toBe(60);

		await userEvent.click(screen.getByRole('button', { name: /Downloads/ }));
		await userEvent.click(screen.getByRole('button', { name: /Downloads/ }));

		expect(FakeObserver.instances).toHaveLength(watching);
	});

	it('fetches the image of a gated row as soon as the observer reports it visible', async () => {
		vi.stubGlobal('IntersectionObserver', FakeObserver);
		serveThumbnails();
		const entries = manyEntries(400, 61);
		stubView(entries);
		mount();

		await screen.findByText('61 results');
		await waitFor(() => {
			expect(requestedIds()).toHaveLength(60);
		});

		expect(FakeObserver.instances).toHaveLength(1);
		expect(observerOf('Mod 460').options.rootMargin).toBe('200px');
		expect(thumbnailOf('Mod 460')).toBeNull();

		observerOf('Mod 460').reveal();

		await waitFor(() => {
			expect(thumbnailOf('Mod 460')).toHaveAttribute('src', dataUrlOf(460));
		});
		expect(requestedIds().slice(60)).toEqual([460]);
	});
});

/**
 * Gives every element a 440px viewport and 44px rows, a scroll position that
 * sticks, and a position on screen that moves as the page scrolls -- happy-dom
 * lays nothing out, forgets a scrollTop as soon as it is set and reports every
 * element at the top of the screen. Returns a way to scroll as a user would.
 */
function fakeLayout(): { scrollTo: (top: number) => void } {
	let scrolled = 0;
	vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(440);
	vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(44);
	vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(
		() => new DOMRect(0, -scrolled, 0, 0)
	);
	vi.spyOn(Element.prototype, 'scrollTop', 'get').mockImplementation(() => scrolled);
	vi.spyOn(Element.prototype, 'scrollTop', 'set').mockImplementation((top: number) => {
		scrolled = top;
	});
	return {
		scrollTo: (top: number) => {
			scrolled = top;
			window.dispatchEvent(new Event('scroll'));
		}
	};
}

/**
 * happy-dom lays nothing out, so the table renders every row there. Giving the
 * document a viewport and the rows a height is what a real window has, and
 * then only the rows in view and the overscan around them may be rendered.
 */
describe('which rows are rendered', () => {
	it('renders only the rows in view plus the overscan, while counting every result', async () => {
		fakeLayout();
		serveThumbnails();
		stubView(manyEntries(3000, 400));
		mount();

		await screen.findByText('400 results');

		await waitFor(() => {
			expect(document.querySelectorAll('[id^="modrow-"]')).toHaveLength(20);
		});
		expect(document.getElementById('modrow-3000')).not.toBeNull();
		expect(document.getElementById('modrow-3399')).toBeNull();
		vi.restoreAllMocks();
	});

	it('scrolls a revealed mod outside the rendered rows into them and focuses it', async () => {
		fakeLayout();
		serveThumbnails();
		stubView(manyEntries(4000, 400));
		mount();
		await screen.findByText('400 results');
		await waitFor(() => {
			expect(document.getElementById('modrow-4300')).toBeNull();
		});

		requestIntent({ kind: 'reveal-mod', modId: 4300 });

		await waitFor(() => {
			expect(document.activeElement?.id).toBe('modrow-4300');
		});
		vi.restoreAllMocks();
	});

	it('hands the tab stop to a rendered row once the focused row scrolls out', async () => {
		const layout = fakeLayout();
		serveThumbnails();
		stubView(manyEntries(5000, 400));
		mount();
		await screen.findByText('400 results');
		await waitFor(() => {
			expect(document.querySelectorAll('[id^="modrow-"]')).toHaveLength(20);
		});
		document.getElementById('modrow-5000')?.focus();
		await waitFor(() => {
			expect(document.activeElement?.id).toBe('modrow-5000');
		});
		await new Promise((settled) => setTimeout(settled, 0));

		layout.scrollTo(200 * 44);

		await waitFor(() => {
			expect(document.getElementById('modrow-5000')).toBeNull();
		});
		const tabStops = document.querySelectorAll('[id^="modrow-"][tabindex="0"]');
		expect(tabStops).toHaveLength(1);
		vi.restoreAllMocks();
	});
});
