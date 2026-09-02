import type { CatalogEntry } from './api/client';

export type ModTab = 'all' | 'installed' | 'available';

export type RowState = 'active' | 'disabled' | 'update_available' | 'incompatible' | 'available';

export interface ModFilter {
	tab: ModTab;
	compatibleOnly: boolean;
	search: string;
}

/**
 * A catalog entry counts as compatible when the catalog knows a version for it
 * under the compatibility filter the entries were fetched with: a fetch with
 * `compatible_only` drops every mod without a compatible version, so the
 * remaining entries always carry a `latest_version`.
 */
export function isCompatible(entry: CatalogEntry): boolean {
	return entry.latest_version !== null;
}

export function isInstalled(entry: CatalogEntry): boolean {
	return entry.state !== 'available';
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

export function filterEntries(entries: readonly CatalogEntry[], filter: ModFilter): CatalogEntry[] {
	return entries.filter((entry) => {
		if (filter.tab === 'installed' && !isInstalled(entry)) {
			return false;
		}
		if (filter.tab === 'available' && isInstalled(entry)) {
			return false;
		}
		if (filter.compatibleOnly && !isCompatible(entry)) {
			return false;
		}
		return matchesSearch(entry, filter.search);
	});
}

/**
 * The state chip of a row. Disabled mods are not part of the catalog join, so
 * the names of the disabled mods are matched case-insensitively here.
 */
export function rowState(entry: CatalogEntry, disabledMods: readonly string[]): RowState {
	const name = entry.mod.name.toLowerCase();
	if (disabledMods.some((disabled) => disabled.toLowerCase() === name)) {
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
