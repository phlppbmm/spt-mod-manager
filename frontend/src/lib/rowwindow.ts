/** The slice of a list that is rendered: rows `start` up to, not including, `end`. */
export interface RowWindow {
	start: number;
	end: number;
}

/** Where the list sits in its scroll container and how much of it is visible. */
export interface ListGeometry {
	/** Rows in the list. */
	count: number;
	/** Height of one row; every row has the same height. */
	rowHeight: number;
	/** How far the container is scrolled. */
	scrollTop: number;
	/** Offset of the first row from the top of the container's content. */
	listTop: number;
	/** Visible height of the container. */
	viewport: number;
}

/** Rows rendered beyond each edge of the visible part, so a scroll does not show a gap. */
export const OVERSCAN = 10;

/**
 * Return the rows to render for a list scrolled to `geometry`.
 *
 * Without layout -- no row height or no visible height measured yet -- the
 * visible part cannot be known, so every row is rendered.
 */
export function rowWindow(geometry: ListGeometry): RowWindow {
	const { count, rowHeight, scrollTop, listTop, viewport } = geometry;
	if (rowHeight <= 0 || viewport <= 0) {
		return { start: 0, end: count };
	}
	const offset = scrollTop - listTop;
	const start = clamp(Math.floor(offset / rowHeight) - OVERSCAN, count);
	const end = clamp(Math.ceil((offset + viewport) / rowHeight) + OVERSCAN, count);
	return { start, end: Math.max(start, end) };
}

/**
 * Return the scroll position that brings row `index` into view, moving as
 * little as possible: unchanged when the row is already fully visible.
 */
export function scrollTopFor(index: number, geometry: ListGeometry): number {
	const { rowHeight, scrollTop, listTop, viewport } = geometry;
	const top = listTop + index * rowHeight;
	if (top < scrollTop) {
		return top;
	}
	if (top + rowHeight > scrollTop + viewport) {
		return top + rowHeight - viewport;
	}
	return scrollTop;
}

function clamp(value: number, count: number): number {
	return Math.min(Math.max(value, 0), count);
}

/** Rows added on each side of the rendered range per step while it grows to the whole list. */
export const GROWTH_CHUNK = 50;

/** The smallest range holding both `a` and `b`; ranges here are always contiguous. */
export function union(a: RowWindow, b: RowWindow): RowWindow {
	if (a.end <= a.start) {
		return b;
	}
	if (b.end <= b.start) {
		return a;
	}
	return { start: Math.min(a.start, b.start), end: Math.max(a.end, b.end) };
}

/**
 * Widen the rendered `range` by `GROWTH_CHUNK` rows on each side, keeping the
 * visible `window` inside it and staying within a list of `count` rows.
 *
 * Rendering only the rows in view keeps a sort or a filter fast; growing the
 * rendered range a little per frame afterwards ends with every row rendered,
 * so scrolling no longer mounts and destroys rows as it goes.
 */
export function grow(range: RowWindow, window: RowWindow, count: number): RowWindow {
	const held = union(range, window);
	return {
		start: Math.max(0, held.start - GROWTH_CHUNK),
		end: Math.min(count, held.end + GROWTH_CHUNK)
	};
}

/** Whether `range` already holds every row of a list of `count` rows. */
export function isComplete(range: RowWindow, count: number): boolean {
	return range.start <= 0 && range.end >= count;
}
