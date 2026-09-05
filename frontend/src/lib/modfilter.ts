import type { CatalogEntry, ModType } from './api/client';

export type RowState = 'active' | 'disabled' | 'update_available' | 'incompatible' | 'available';

export type ChipId =
	| 'new'
	| 'featured'
	| 'installed'
	| 'favorited'
	| 'update_available'
	| 'disabled'
	| 'compatible'
	| 'fika'
	| 'client'
	| 'server';

export const CHIP_IDS: readonly ChipId[] = [
	'new',
	'featured',
	'installed',
	'favorited',
	'update_available',
	'disabled',
	'compatible',
	'fika',
	'client',
	'server'
];

export const CHIP_LABELS: Record<ChipId, string> = {
	new: 'New',
	featured: 'Featured',
	installed: 'Installed',
	favorited: 'Favorited',
	update_available: 'Update available',
	disabled: 'Disabled',
	compatible: 'Compatible',
	fika: 'Fika',
	client: 'Client',
	server: 'Server'
};

export const NEW_WINDOW_DAYS = 14;

const NEW_WINDOW_MS = NEW_WINDOW_DAYS * 24 * 60 * 60 * 1000;

export interface FilterContext {
	favorites: ReadonlySet<number>;
	/** Names of the disabled mods, lowercased. */
	disabled: ReadonlySet<string>;
	/** Installed mod type per Forge id. */
	modTypes: ReadonlyMap<number, ModType>;
	/** Reference time for the `new` chip, in epoch milliseconds. */
	now: number;
}

export interface ModFilter {
	chips: ReadonlySet<ChipId>;
	category: string | null;
	search: string;
}

/**
 * A catalog entry counts as compatible when the catalog knows a version for it
 * under the SPT version of the game directory.
 */
export function isCompatible(entry: CatalogEntry): boolean {
	return entry.latest_version !== null;
}

export function isFavorite(entry: CatalogEntry, favorites: ReadonlySet<number>): boolean {
	return favorites.has(entry.mod.id);
}

export function matchesSearch(entry: CatalogEntry, search: string): boolean {
	const needle = search.trim().toLowerCase();
	if (needle === '') {
		return true;
	}
	return (
		entry.mod.name.toLowerCase().includes(needle) ||
		entry.mod.author.toLowerCase().includes(needle)
	);
}

function isDisabled(entry: CatalogEntry, disabled: ReadonlySet<string>): boolean {
	return disabled.has(entry.mod.name.toLowerCase());
}

export const CHIP_PREDICATES: Record<
	ChipId,
	(entry: CatalogEntry, context: FilterContext) => boolean
> = {
	new: (entry, context) => {
		const seen = entry.mod.first_seen_at;
		if (!seen) {
			return false;
		}
		const moment = Date.parse(seen);
		return !Number.isNaN(moment) && context.now - moment <= NEW_WINDOW_MS;
	},
	featured: (entry) => entry.mod.featured,
	installed: (entry, context) => entry.state !== 'available' || isDisabled(entry, context.disabled),
	favorited: (entry, context) => isFavorite(entry, context.favorites),
	update_available: (entry, context) => rowState(entry, context.disabled) === 'update_available',
	disabled: (entry, context) => isDisabled(entry, context.disabled),
	compatible: (entry) => isCompatible(entry),
	// The per-version flag is the precise answer; the mod-wide flag stands in
	// while the version says nothing.
	fika: (entry) => {
		const version = entry.latest_version?.fika_compatibility;
		if (version === 'compatible') {
			return true;
		}
		if (version === 'incompatible') {
			return false;
		}
		return entry.mod.fika_compatibility === true;
	},
	client: (entry, context) => {
		const type = context.modTypes.get(entry.mod.id);
		return type === 'client' || type === 'hybrid';
	},
	server: (entry, context) => {
		const type = context.modTypes.get(entry.mod.id);
		return type === 'server' || type === 'hybrid';
	}
};

function matches(entry: CatalogEntry, filter: ModFilter, context: FilterContext): boolean {
	for (const chip of filter.chips) {
		if (!CHIP_PREDICATES[chip](entry, context)) {
			return false;
		}
	}
	if (filter.category !== null && entry.mod.category !== filter.category) {
		return false;
	}
	return matchesSearch(entry, filter.search);
}

export function filterEntries(
	entries: readonly CatalogEntry[],
	filter: ModFilter,
	context: FilterContext
): CatalogEntry[] {
	return entries.filter((entry) => matches(entry, filter, context));
}

/**
 * How many rows each chip would leave: the other active chips, the category
 * and the search still apply, so the number is the row count the user gets
 * after switching that chip on.
 */
export function chipCounts(
	entries: readonly CatalogEntry[],
	filter: ModFilter,
	context: FilterContext
): Record<ChipId, number> {
	const counts = {} as Record<ChipId, number>;
	for (const chip of CHIP_IDS) {
		const chips = new Set(filter.chips);
		chips.add(chip);
		counts[chip] = entries.filter((entry) =>
			matches(entry, { ...filter, chips }, context)
		).length;
	}
	return counts;
}

export function categoriesOf(entries: readonly CatalogEntry[]): string[] {
	const titles = new Set<string>();
	for (const entry of entries) {
		if (entry.mod.category) {
			titles.add(entry.mod.category);
		}
	}
	return [...titles].sort();
}

/**
 * The state chip of a row. Disabled mods are not part of the catalog join, so
 * their names are matched against the lowercased names of the disabled mods.
 */
export function rowState(entry: CatalogEntry, disabled: ReadonlySet<string>): RowState {
	if (isDisabled(entry, disabled)) {
		return 'disabled';
	}
	if (entry.state === 'update_available') {
		return 'update_available';
	}
	if (entry.state === 'installed') {
		return 'active';
	}
	return isCompatible(entry) ? 'available' : 'incompatible';
}

export type SortKey = 'name' | 'installed' | 'latest' | 'spt' | 'downloads' | 'updated';

export interface ModSort {
	key: SortKey;
	descending: boolean;
}

function sortValue(entry: CatalogEntry, key: SortKey): string | number {
	switch (key) {
		case 'name':
			return entry.mod.name.toLowerCase();
		case 'installed':
			return entry.installed_version ?? '';
		case 'latest':
			return entry.latest_version?.version ?? '';
		case 'spt':
			return entry.latest_version?.spt_version_constraint ?? '';
		case 'downloads':
			return entry.mod.downloads;
		case 'updated':
			return entry.mod.updated_at ?? '';
	}
}

/** Stable sort of catalog entries by a column; ties keep the given order. */
export function sortEntries(
	entries: readonly CatalogEntry[],
	sort: ModSort | null
): CatalogEntry[] {
	if (sort === null) {
		return [...entries];
	}
	const direction = sort.descending ? -1 : 1;
	return [...entries].sort((a, b) => {
		const va = sortValue(a, sort.key);
		const vb = sortValue(b, sort.key);
		if (va < vb) return -direction;
		if (va > vb) return direction;
		return 0;
	});
}
