import type { ChipId, ModFilter } from './modfilter';

export const DEFAULT_FILTER: ModFilter = {
	chips: new Set<ChipId>(['compatible']),
	category: null,
	search: ''
};

function copy(filter: ModFilter): ModFilter {
	return { chips: new Set(filter.chips), category: filter.category, search: filter.search };
}

let current: ModFilter = copy(DEFAULT_FILTER);

const listeners = new Set<(filter: ModFilter) => void>();

export function getFilter(): ModFilter {
	return copy(current);
}

function replace(filter: ModFilter): void {
	if (filtersEqual(current, filter)) {
		return;
	}
	current = copy(filter);
	for (const listener of [...listeners]) {
		listener(copy(current));
	}
}

export function setFilter(filter: ModFilter): void {
	replace(filter);
}

export function clearFilter(): void {
	replace(DEFAULT_FILTER);
}

export function filtersEqual(a: ModFilter, b: ModFilter): boolean {
	return (
		a.category === b.category &&
		a.search === b.search &&
		a.chips.size === b.chips.size &&
		[...a.chips].every((chip) => b.chips.has(chip))
	);
}

export function isDefaultFilter(filter: ModFilter): boolean {
	return filtersEqual(filter, DEFAULT_FILTER);
}

export function subscribeFilter(fn: (filter: ModFilter) => void): () => void {
	listeners.add(fn);
	fn(getFilter());
	return () => {
		listeners.delete(fn);
	};
}
