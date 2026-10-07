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
