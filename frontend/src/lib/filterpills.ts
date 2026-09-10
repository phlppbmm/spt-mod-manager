import type { SptReleaseInfo } from './api/client';
import type { ChipId, ModFilter } from './modfilter';

export type PillKey = 'compatibility' | 'category' | 'type' | 'fika' | 'state' | 'featured';

export interface PillOption {
	id: string;
	label: string;
	chips: readonly ChipId[];
	category?: string;
	/** Set on the options that judge the list against another SPT version. */
	sptVersion?: string;
}

export interface PillDefinition {
	key: PillKey;
	label: string;
	kind: 'radio';
	options: PillOption[];
	/** Set on the pill whose options pick the SPT version the list is judged against. */
	versioned?: true;
}

/** The versions of the releases the manager can install, newest first. */
export function installableVersions(releases: readonly SptReleaseInfo[]): string[] {
	const versions: string[] = [];
	for (const release of releases) {
		if (release.downloadable && !versions.includes(release.version)) {
			versions.push(release.version);
		}
	}
	return versions;
}

function compatibilityOptions(
	sptVersion: string | null,
	installable: readonly string[]
): PillOption[] {
	const options: PillOption[] = [
		{
			id: 'compatible',
			label: sptVersion === null ? 'Compatible' : `SPT ${sptVersion}`,
			chips: ['compatible']
		}
	];
	for (const version of installable) {
		if (version !== sptVersion) {
			options.push({
				id: `spt-${version}`,
				label: `SPT ${version}`,
				chips: ['compatible'],
				sptVersion: version
			});
		}
	}
	options.push({ id: 'all', label: 'All versions', chips: [] });
	return options;
}

/**
 * Every pill is a radio: chips are ANDed, so a Type pill offering Client and
 * Server at once would only ever match hybrid mods.
 *
 * `installable` names the SPT versions the manager can install; an empty list
 * (nothing published, or the release source unreachable) leaves the
 * compatibility pill with the installed version and "All versions".
 */
export function pillDefinitions(
	categories: readonly string[],
	sptVersion: string | null,
	installable: readonly string[] = []
): PillDefinition[] {
	return [
		{
			key: 'compatibility',
			label: 'Compatibility',
			kind: 'radio',
			options: compatibilityOptions(sptVersion, installable),
			versioned: true
		},
		{
			key: 'category',
			label: 'Category',
			kind: 'radio',
			options: categories.map((title) => ({ id: title, label: title, chips: [], category: title }))
		},
		{
			key: 'type',
			label: 'Type',
			kind: 'radio',
			options: [
				{ id: 'client', label: 'Client', chips: ['client'] },
				{ id: 'server', label: 'Server', chips: ['server'] }
			]
		},
		{
			key: 'fika',
			label: 'Fika',
			kind: 'radio',
			options: [{ id: 'fika', label: 'Compatible', chips: ['fika'] }]
		},
		{
			key: 'state',
			label: 'State',
			kind: 'radio',
			options: [
				{ id: 'installed', label: 'Installed', chips: ['installed'] },
				{ id: 'disabled', label: 'Disabled', chips: ['disabled'] }
			]
		},
		{
			key: 'featured',
			label: 'Featured',
			kind: 'radio',
			options: [{ id: 'featured', label: 'Featured', chips: ['featured'] }]
		}
	];
}

export interface ActivePill {
	key: PillKey;
	optionIds: string[];
	valueLabel: string;
}

/** An option without chips and without a category narrows nothing. */
function isSelected(option: PillOption, filter: ModFilter, versioned: boolean): boolean {
	if (option.chips.length === 0 && option.category === undefined) {
		return false;
	}
	if (option.category !== undefined && filter.category !== option.category) {
		return false;
	}
	if (versioned && option.sptVersion !== filter.sptVersion) {
		return false;
	}
	return option.chips.every((chip) => filter.chips.has(chip));
}

export function activePills(filter: ModFilter, defs: PillDefinition[]): ActivePill[] {
	const pills: ActivePill[] = [];
	for (const def of defs) {
		const versioned = def.versioned === true;
		const selected = def.options.filter((option) => isSelected(option, filter, versioned));
		if (selected.length > 0) {
			pills.push({
				key: def.key,
				optionIds: selected.map((option) => option.id),
				valueLabel: selected.map((option) => option.label).join(', ')
			});
		}
	}
	return pills;
}

export function applyPill(
	filter: ModFilter,
	def: PillDefinition,
	optionIds: readonly string[]
): ModFilter {
	const chips = new Set(filter.chips);
	const ownsCategory = def.options.some((option) => option.category !== undefined);
	let category = ownsCategory ? null : filter.category;
	let sptVersion = def.versioned === true ? undefined : filter.sptVersion;
	// Siblings share chips (every SPT version option narrows to `compatible`),
	// so what the picked options ask for is collected before anything is removed.
	const picked = new Set<ChipId>();
	for (const option of def.options) {
		if (optionIds.includes(option.id)) {
			for (const chip of option.chips) {
				picked.add(chip);
			}
			if (option.category !== undefined) {
				category = option.category;
			}
			if (option.sptVersion !== undefined) {
				sptVersion = option.sptVersion;
			}
		}
	}
	for (const option of def.options) {
		for (const chip of option.chips) {
			if (picked.has(chip)) {
				chips.add(chip);
			} else {
				chips.delete(chip);
			}
		}
	}
	return { chips, category, search: filter.search, sptVersion };
}

export function removePill(filter: ModFilter, def: PillDefinition): ModFilter {
	return applyPill(filter, def, []);
}

export function availablePills(filter: ModFilter, defs: PillDefinition[]): PillDefinition[] {
	const active = new Set(activePills(filter, defs).map((pill) => pill.key));
	return defs.filter((def) => !active.has(def.key));
}

export type QuickTabId = 'all' | 'installed' | 'updates' | 'favorites' | 'new';

export const QUICK_TABS: readonly { id: QuickTabId; label: string }[] = [
	{ id: 'all', label: 'All' },
	{ id: 'installed', label: 'Installed' },
	{ id: 'updates', label: 'Updates' },
	{ id: 'favorites', label: 'Favorites' },
	{ id: 'new', label: 'New' }
];

const QUICK_TAB_CHIPS: readonly { id: QuickTabId; chip: ChipId }[] = [
	{ id: 'installed', chip: 'installed' },
	{ id: 'updates', chip: 'update_available' },
	{ id: 'favorites', chip: 'favorited' },
	{ id: 'new', chip: 'new' }
];

export function activeQuickTab(filter: ModFilter): QuickTabId {
	for (const tab of QUICK_TAB_CHIPS) {
		if (filter.chips.has(tab.chip)) {
			return tab.id;
		}
	}
	return 'all';
}

export function applyQuickTab(filter: ModFilter, tab: QuickTabId): ModFilter {
	const chips = new Set(filter.chips);
	for (const entry of QUICK_TAB_CHIPS) {
		if (entry.id === tab) {
			chips.add(entry.chip);
		} else {
			chips.delete(entry.chip);
		}
	}
	return {
		chips,
		category: filter.category,
		search: filter.search,
		sptVersion: filter.sptVersion
	};
}
