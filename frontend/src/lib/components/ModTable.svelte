<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import ModRow from './ModRow.svelte';
	import type { CatalogEntry, ModInfo } from '$lib/api/client';
	import { onIntents, takeIntents } from '$lib/intents';
	import { rowState, type ModSort, type SortKey } from '$lib/modfilter';
	import { focusAfterRows } from '$lib/rownav';
	import {
		grow,
		isComplete,
		rowWindow,
		scrollTopFor,
		union,
		type ListGeometry,
		type RowWindow
	} from '$lib/rowwindow';
	import { headerCheckbox, toggleAll, toggleSelection } from '$lib/selection';

	interface Props {
		path: string;
		rows: CatalogEntry[];
		disabledNames: ReadonlySet<string>;
		installedByForgeId: ReadonlyMap<string, ModInfo>;
		installedByName: ReadonlyMap<string, ModInfo>;
		locked: boolean;
		favoriteIds: ReadonlySet<number>;
		selected: ReadonlySet<number>;
		sort: ModSort | null;
		onsort: (key: SortKey) => void;
		onselect: (next: Set<number>) => void;
		onfavorite: (ids: number[]) => void;
		onchanged: () => void;
		onbusy: (modId: number, busy: boolean) => void;
		loading: boolean;
		showInstalledColumns: boolean;
	}

	let {
		path,
		rows,
		disabledNames,
		installedByForgeId,
		installedByName,
		locked,
		favoriteIds,
		selected,
		sort,
		onsort,
		onselect,
		onfavorite,
		onchanged,
		onbusy,
		loading,
		showInstalledColumns
	}: Props = $props();

	// Before a row has been measured, the stylesheet's minimum row height stands in for it.
	const ESTIMATED_ROW_HEIGHT = 44;

	interface RenderedRow {
		entry: CatalogEntry;
		index: number;
		offscreen: boolean;
	}

	let ids = $derived(rows.map((entry) => entry.mod.id));
	let header = $derived(headerCheckbox(selected, ids));
	let focusedId: number | null = $state(null);

	// A list change renders only the rows in view, so a sort or a filter stays fast on a catalog of
	// close to two thousand rows; the rendered range then grows a chunk per frame until it holds
	// every row, so scrolling afterwards is plain scrolling instead of mounting rows as it goes.
	let listElement: HTMLElement | null = $state(null);
	let topSpacer: HTMLElement | null = $state(null);
	let scroller: HTMLElement | null = null;
	let scrollTop = $state(0);
	let viewport = $state(0);
	let listTop = $state(0);
	let measuredRowHeight = $state(0);
	let busyIds = $state(new Set<number>());
	let grown: RowWindow = $state({ start: 0, end: 0 });
	let growthFrame = 0;
	let scrollFrame = 0;

	let rowHeight = $derived(
		measuredRowHeight > 0 ? measuredRowHeight : viewport > 0 ? ESTIMATED_ROW_HEIGHT : 0
	);
	let geometry = $derived<ListGeometry>({
		count: rows.length,
		rowHeight,
		scrollTop,
		listTop,
		viewport
	});
	let span = $derived(rowWindow(geometry));
	let range = $derived(union(grown, span));
	let rendered = $derived(renderedRows(rows, range, busyIds));

	// One row is always the tab stop, so Tab reaches the table even before a
	// row was focused; the ring follows the real DOM focus. A focused row that
	// scrolled out of the rendered window hands the tab stop to the first row
	// still rendered.
	let tabStopId = $derived.by(() => {
		const candidate = focusedId ?? (ids.length > 0 ? ids[0] : null);
		if (candidate === null) {
			return null;
		}
		const index = ids.indexOf(candidate);
		if (index >= range.start && index < range.end) {
			return candidate;
		}
		return range.start < ids.length ? ids[range.start] : null;
	});

	onMount(() => {
		const list = listElement;
		if (list !== null) {
			scroller = scrollParent(list);
		}
		const target: HTMLElement | Window | null =
			scroller === document.scrollingElement ? window : scroller;
		target?.addEventListener('scroll', onScroll, { passive: true });
		const resize =
			scroller !== null && typeof ResizeObserver !== 'undefined'
				? new ResizeObserver(measure)
				: null;
		if (scroller !== null) {
			resize?.observe(scroller);
		}
		measure();
		reveal();
		const stopIntents = onIntents(reveal);
		return () => {
			target?.removeEventListener('scroll', onScroll);
			resize?.disconnect();
			stopIntents();
			cancelAnimationFrame(growthFrame);
			cancelAnimationFrame(scrollFrame);
		};
	});

	$effect(() => {
		void rows;
		let superseded = false;
		untrack(() => {
			grown = { start: 0, end: 0 };
		});
		void tick().then(() => {
			if (superseded) {
				return;
			}
			measure();
			growthFrame = requestAnimationFrame(growStep);
		});
		// A newer list, or the table going away, ends this growth before it can touch either.
		return () => {
			superseded = true;
			cancelAnimationFrame(growthFrame);
		};
	});

	function growStep(): void {
		if (rowHeight <= 0 || viewport <= 0 || isComplete(range, rows.length)) {
			return;
		}
		grown = grow(grown, span, rows.length);
		growthFrame = requestAnimationFrame(growStep);
	}

	/** Measure at most once a frame, and not at all once every row is rendered. */
	function onScroll(): void {
		if (scrollFrame !== 0 || isComplete(range, rows.length)) {
			return;
		}
		scrollFrame = requestAnimationFrame(() => {
			scrollFrame = 0;
			measure();
		});
	}

	$effect(() => {
		focusedId = focusAfterRows(ids, untrack(() => focusedId));
	});

	$effect(() => {
		const id = focusedId;
		if (id === null) {
			return;
		}
		untrack(() => void bringIntoView(id));
	});

	/** Scroll a row that is not rendered into the window first, then focus it. */
	async function bringIntoView(id: number): Promise<void> {
		const index = ids.indexOf(id);
		if (scroller !== null && index >= 0 && (index < range.start || index >= range.end)) {
			scroller.scrollTop = scrollTopFor(index, geometry);
			measure();
			await tick();
		}
		const row = document.getElementById(`modrow-${id}`);
		row?.focus();
		row?.scrollIntoView({ block: 'nearest' });
	}

	function measure(): void {
		const container = scroller;
		const spacer = topSpacer;
		if (container === null || spacer === null) {
			return;
		}
		scrollTop = container.scrollTop;
		viewport = container.clientHeight;
		const spacerTop = spacer.getBoundingClientRect().top;
		const containerTop =
			container === document.scrollingElement ? 0 : container.getBoundingClientRect().top;
		listTop = spacerTop - containerTop + container.scrollTop;
		const row = listElement?.querySelector<HTMLElement>('.slot:not(.offscreen) > [role="row"]');
		if (row !== null && row !== undefined && row.offsetHeight > 0) {
			measuredRowHeight = row.offsetHeight;
		}
	}

	/** The closest ancestor that scrolls, or the document when none does. */
	function scrollParent(node: HTMLElement): HTMLElement | null {
		for (let element = node.parentElement; element !== null; element = element.parentElement) {
			const { overflowY } = getComputedStyle(element);
			if (overflowY === 'auto' || overflowY === 'scroll') {
				return element;
			}
		}
		return document.scrollingElement instanceof HTMLElement ? document.scrollingElement : null;
	}

	/**
	 * The rows of the window, plus every busy row outside it: a row holds its own
	 * install progress, and unmounting it while it works would show it idle when
	 * it scrolls back.
	 */
	function renderedRows(
		all: readonly CatalogEntry[],
		window: RowWindow,
		busy: ReadonlySet<number>
	): RenderedRow[] {
		const items: RenderedRow[] = [];
		for (let index = window.start; index < window.end; index += 1) {
			items.push({ entry: all[index], index, offscreen: false });
		}
		if (busy.size === 0) {
			return items;
		}
		all.forEach((entry, index) => {
			if ((index < window.start || index >= window.end) && busy.has(entry.mod.id)) {
				items.push({ entry, index, offscreen: true });
			}
		});
		return items.sort((a, b) => a.index - b.index);
	}

	function trackBusy(modId: number, isBusy: boolean): void {
		const next = new Set(busyIds);
		if (isBusy) {
			next.add(modId);
		} else {
			next.delete(modId);
		}
		busyIds = next;
		onbusy(modId, isBusy);
	}

	function reveal(): void {
		for (const intent of takeIntents(['reveal-mod'])) {
			focusedId = intent.modId;
		}
	}

	function sortMark(key: SortKey): string {
		if (sort?.key !== key) {
			return '';
		}
		return sort.descending ? ' ▼' : ' ▲';
	}
</script>

<div class="table">
	<div
		bind:this={listElement}
		class="panel list"
		class:lean={!showInstalledColumns}
		role="grid"
		aria-label="Mods"
		aria-rowcount={rows.length + 1}
	>
		<div class="panel-head head" role="row">
			<div class="pick" role="columnheader">
				<input
					type="checkbox"
					aria-label="Select all mods"
					checked={header === 'all'}
					indeterminate={header === 'some'}
					onchange={() => onselect(toggleAll(selected, ids))}
				/>
			</div>
			<div class="index" role="columnheader">#</div>
			<div role="columnheader">
				<button class="sort" onclick={() => onsort('name')}>Mod{sortMark('name')}</button>
			</div>
			{#if showInstalledColumns}
				<div role="columnheader">
					<button class="sort" onclick={() => onsort('installed')}>Installed{sortMark('installed')}</button>
				</div>
			{/if}
			<div role="columnheader">
				<button class="sort" onclick={() => onsort('latest')}>Latest{sortMark('latest')}</button>
			</div>
			<div role="columnheader">
				<button class="sort" onclick={() => onsort('spt')}>SPT{sortMark('spt')}</button>
			</div>
			<div class="downloads" role="columnheader">
				<button class="sort" onclick={() => onsort('downloads')}>Downloads{sortMark('downloads')}</button>
			</div>
			<div class="updated" role="columnheader">
				<button class="sort" onclick={() => onsort('updated')}>Updated{sortMark('updated')}</button>
			</div>
			{#if showInstalledColumns}
				<div class="type" role="columnheader">Type</div>
			{/if}
			<div role="columnheader">State</div>
			<div role="columnheader"></div>
		</div>
		<div bind:this={topSpacer} class="spacer" style:height="{range.start * rowHeight}px"></div>
		{#each rendered as item (item.entry.mod.id)}
			<div class="slot" class:offscreen={item.offscreen} role="presentation">
				<ModRow
					{path}
					index={item.index + 1}
					entry={item.entry}
					modState={rowState(item.entry, disabledNames)}
					installed={installedByForgeId.get(String(item.entry.mod.id)) ??
						installedByName.get(item.entry.mod.name.toLowerCase()) ??
						null}
					{locked}
					favorite={favoriteIds.has(item.entry.mod.id)}
					selected={selected.has(item.entry.mod.id)}
					{ids}
					{focusedId}
					{tabStopId}
					onfocusrow={(id) => (focusedId = id)}
					onselect={() => onselect(toggleSelection(selected, item.entry.mod.id))}
					{onfavorite}
					{onchanged}
					onbusy={trackBusy}
					{showInstalledColumns}
				/>
			</div>
		{/each}
		<div class="spacer" style:height="{(rows.length - range.end) * rowHeight}px"></div>
		{#if rows.length === 0}
			<p class="empty">
				{loading ? 'Loading…' : 'No mods match the current filters.'}
			</p>
		{/if}
	</div>
</div>
<p class="result-count">{rows.length === 1 ? '1 result' : `${rows.length} results`}</p>

<style>
	.table {
		container: mods / inline-size;
	}

	.list {
		position: relative;
	}

	.slot {
		display: contents;
	}

	/* A busy row outside the window stays mounted so its progress survives, but takes no space. */
	.slot.offscreen {
		display: block;
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		visibility: hidden;
	}

	/* `lean` is the layout without the Installed and Type columns. */
	.list {
		--mods-grid: 1.75rem 2rem minmax(12rem, 1fr) 5rem 5rem 5rem 5.5rem 5.5rem 4.5rem 7rem 8rem;
	}

	.list.lean {
		--mods-grid: 1.75rem 2rem minmax(12rem, 1fr) 5rem 5rem 5.5rem 5.5rem 7rem 8rem;
	}

	@container mods (max-width: 1090px) {
		.list {
			--mods-grid: 1.75rem 2rem minmax(10rem, 1fr) 5rem 5rem 5rem 5.5rem 5.5rem 4.5rem 7rem 8rem;
		}

		.list.lean {
			--mods-grid: 1.75rem 2rem minmax(10rem, 1fr) 5rem 5rem 5.5rem 5.5rem 7rem 8rem;
		}
	}

	@container mods (max-width: 1060px) {
		.list {
			--mods-grid: 1.75rem 2rem minmax(10rem, 1fr) 5rem 5rem 5rem 7rem 8rem;
		}

		.list.lean {
			--mods-grid: 1.75rem 2rem minmax(10rem, 1fr) 5rem 5rem 7rem 8rem;
		}

		.head > .downloads,
		.head > .updated,
		.head > .type {
			display: none;
		}
	}

	.head {
		display: grid;
		grid-template-columns: var(--mods-grid);
		align-items: center;
		gap: var(--space-2);
		padding: var(--space-2) var(--space-3);
	}

	.head .pick {
		display: flex;
		align-items: center;
	}

	.head .index {
		text-align: right;
	}

	.head .sort {
		display: block;
		height: auto;
		padding: 0;
		background: none;
		border: none;
		font: inherit;
		color: inherit;
		letter-spacing: inherit;
		text-transform: inherit;
		text-align: left;
		cursor: pointer;
	}

	.head .sort:hover {
		background: none;
		color: var(--color-text);
	}

	.empty {
		margin: 0;
		padding: var(--space-4);
		color: var(--color-text-muted);
	}

	.result-count {
		margin: var(--space-3) 0 0;
	}
</style>
