import { describe, expect, it } from 'vitest';
import {
	GROWTH_CHUNK,
	OVERSCAN,
	grow,
	isComplete,
	rowWindow,
	scrollTopFor,
	union,
	type ListGeometry
} from './rowwindow';

/**
 * A 1000-row list of 40px rows whose first row sits 100px into its scroll
 * container, shown through a 400px viewport -- ten rows at a time.
 */
function geometry(overrides: Partial<ListGeometry> = {}): ListGeometry {
	return { count: 1000, rowHeight: 40, scrollTop: 0, listTop: 100, viewport: 400, ...overrides };
}

describe('rowWindow', () => {
	it('renders the rows in view plus the overscan below them at the top of the list', () => {
		expect(rowWindow(geometry())).toEqual({ start: 0, end: 8 + OVERSCAN });
	});

	it('renders the overscan on both sides in the middle of the list', () => {
		const window = rowWindow(geometry({ scrollTop: 100 + 500 * 40 }));

		expect(window).toEqual({ start: 500 - OVERSCAN, end: 510 + OVERSCAN });
	});

	it('stops at the last row at the bottom of the list', () => {
		const window = rowWindow(geometry({ scrollTop: 100 + 995 * 40 }));

		expect(window.end).toBe(1000);
	});

	it.each([
		{ id: 'no-row-height', overrides: { rowHeight: 0 } },
		{ id: 'no-viewport', overrides: { viewport: 0 } }
	])('renders every row when layout is unknown ($id)', ({ overrides }) => {
		expect(rowWindow(geometry(overrides))).toEqual({ start: 0, end: 1000 });
	});

	it('renders nothing for an empty list', () => {
		expect(rowWindow(geometry({ count: 0 }))).toEqual({ start: 0, end: 0 });
	});

	it('never ends before it starts when scrolled past the list', () => {
		const window = rowWindow(geometry({ count: 5, scrollTop: 100 + 50 * 40 }));

		expect(window).toEqual({ start: 5, end: 5 });
	});
});

describe('scrollTopFor', () => {
	it('scrolls up so a row above the view becomes the first visible one', () => {
		expect(scrollTopFor(10, geometry({ scrollTop: 100 + 50 * 40 }))).toBe(100 + 10 * 40);
	});

	it('scrolls down just far enough that a row below the view becomes the last visible one', () => {
		expect(scrollTopFor(30, geometry())).toBe(100 + 31 * 40 - 400);
	});

	it('leaves the scroll position alone for a row already fully in view', () => {
		expect(scrollTopFor(3, geometry())).toBe(0);
	});
});

describe('union', () => {
	it('spans both ranges', () => {
		expect(union({ start: 10, end: 20 }, { start: 15, end: 40 })).toEqual({ start: 10, end: 40 });
	});

	it.each([
		{ id: 'first-empty', a: { start: 5, end: 5 }, b: { start: 30, end: 40 }, expected: { start: 30, end: 40 } },
		{ id: 'second-empty', a: { start: 30, end: 40 }, b: { start: 5, end: 5 }, expected: { start: 30, end: 40 } }
	])('ignores an empty range ($id)', ({ a, b, expected }) => {
		expect(union(a, b)).toEqual(expected);
	});
});

describe('grow', () => {
	it('widens the range by one chunk on each side around the visible rows', () => {
		const grown = grow({ start: 0, end: 0 }, { start: 500, end: 530 }, 1000);

		expect(grown).toEqual({ start: 500 - GROWTH_CHUNK, end: 530 + GROWTH_CHUNK });
	});

	it('takes in visible rows the range does not hold yet', () => {
		const grown = grow({ start: 100, end: 200 }, { start: 600, end: 630 }, 1000);

		expect(grown).toEqual({ start: 100 - GROWTH_CHUNK, end: 630 + GROWTH_CHUNK });
	});

	it('stays within the list at both ends', () => {
		expect(grow({ start: 10, end: 990 }, { start: 0, end: 30 }, 1000)).toEqual({ start: 0, end: 1000 });
	});
});

describe('isComplete', () => {
	it.each([
		{ id: 'whole-list', range: { start: 0, end: 1000 }, complete: true },
		{ id: 'missing-the-top', range: { start: 1, end: 1000 }, complete: false },
		{ id: 'missing-the-bottom', range: { start: 0, end: 999 }, complete: false }
	])('says whether the range holds every row ($id)', ({ range, complete }) => {
		expect(isComplete(range, 1000)).toBe(complete);
	});
});
