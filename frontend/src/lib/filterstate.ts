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

export function getFilter(): ModFilter {
	return copy(current);
}

export function setFilter(filter: ModFilter): void {
	current = copy(filter);
}

export function clearFilter(): void {
	current = copy(DEFAULT_FILTER);
}

export function isDefaultFilter(filter: ModFilter): boolean {
	return (
		filter.category === DEFAULT_FILTER.category &&
		filter.search === DEFAULT_FILTER.search &&
		filter.chips.size === DEFAULT_FILTER.chips.size &&
		[...DEFAULT_FILTER.chips].every((chip) => filter.chips.has(chip))
	);
}
