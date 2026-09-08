import type { ModInfo, ModSet, ModSetEntry, ModSetOverview, ModSetPlan } from './api/client';
import { ApiError } from './api/transport';

type ModState = ModInfo['state'];

const ACTIVE_STATES: ReadonlySet<ModState> = new Set<ModState>([
	'installed',
	'updated',
	'enabled'
]);

export const NO_SET_LABEL = 'No set';

export type EntryStatus = 'on' | 'off' | 'missing' | 'version';

export interface EntryRow {
	entry: ModSetEntry;
	installed: boolean;
	installedVersion: string;
	status: EntryStatus;
}

export interface SetOption {
	value: string;
	label: string;
	selected: boolean;
}

export interface InstallTarget {
	name: string;
	target: number | string;
}

/**
 * The installation as a set: every mod that is still there, with the state it
 * has right now. A removed mod is nothing the set could turn on again.
 */
export function entriesFromMods(mods: readonly ModInfo[]): ModSetEntry[] {
	return mods
		.filter((mod) => mod.state !== 'removed')
		.map((mod) => ({
			forge_id: mod.forge_id,
			name: mod.name,
			version: mod.version,
			enabled: ACTIVE_STATES.has(mod.state)
		}))
		.sort((left, right) => left.name.toLowerCase().localeCompare(right.name.toLowerCase()));
}

/**
 * One row per mod of the set, with what the installation holds for it. The
 * trailer name of a mod differs from its Forge display name, so the Forge id
 * links the two sides wherever both carry one.
 */
export function entryRows(set: ModSet | null, mods: readonly ModInfo[]): EntryRow[] {
	return (set?.entries ?? []).map((entry): EntryRow => {
		const mod = installedFor(entry, mods);
		if (mod === null || mod.state === 'removed') {
			return { entry, installed: false, installedVersion: '', status: 'missing' };
		}
		return {
			entry,
			installed: true,
			installedVersion: mod.version,
			status: entryStatus(entry, mod)
		};
	});
}

export function toggleEntry(entries: readonly ModSetEntry[], name: string): ModSetEntry[] {
	return entries.map((entry) =>
		entry.name === name ? { ...entry, enabled: !entry.enabled } : entry
	);
}

export function setOptions(overview: ModSetOverview, profileId: string): SetOption[] {
	const bound = boundSetName(overview, profileId);
	return [
		{ value: '', label: NO_SET_LABEL, selected: bound === null },
		...overview.sets.map((set) => ({
			value: set.name,
			label: set.name,
			selected: bound !== null && bound.toLowerCase() === set.name.toLowerCase()
		}))
	];
}

export function boundSetName(overview: ModSetOverview, profileId: string): string | null {
	return overview.bindings.find((binding) => binding.profile_id === profileId)?.set_name ?? null;
}

/** The set called `name`, ignoring case, or null when no set carries that name. */
export function findSet(overview: ModSetOverview, name: string): ModSet | null {
	const wanted = name.trim().toLowerCase();
	return wanted === ''
		? null
		: (overview.sets.find((set) => set.name.toLowerCase() === wanted) ?? null);
}

/**
 * What to install for the mods a set reported as missing. A missing mod is
 * named by its trailer name, which is not the name the Forge shows, so the
 * Forge id of the matching entry is used wherever the set recorded one.
 */
export function installTargets(set: ModSet | null, missing: readonly string[]): InstallTarget[] {
	const entries = set?.entries ?? [];
	return missing.map((name) => {
		const entry = entries.find((candidate) => candidate.name.toLowerCase() === name.toLowerCase());
		const forgeId = entry === undefined ? '' : entry.forge_id;
		return { name, target: forgeId === '' ? name : Number(forgeId) };
	});
}

/** `name` as the file name an export is offered under, safe on every file system. */
export function exportFileName(name: string): string {
	const cleaned = name.replace(/[^A-Za-z0-9._ -]/g, '_').trim();
	return `${cleaned === '' ? 'mod-set' : cleaned}.json`;
}

export function planSummary(plan: ModSetPlan): string {
	const counts: [number, string][] = [
		[plan.enable.length, 'on'],
		[plan.disable.length, 'off'],
		[plan.missing.length, 'missing']
	];
	const parts = counts
		.filter(([count]) => count > 0)
		.map(([count, label], index) =>
			index === 0 ? `${count} ${count === 1 ? 'mod' : 'mods'} ${label}` : `${count} ${label}`
		);
	return parts.length === 0 ? 'Nothing to change.' : `${parts.join(', ')}.`;
}

/** The mods a refused launch or apply named as missing, empty for any other failure. */
export function missingFromError(error: unknown): string[] {
	if (error instanceof ApiError && error.code === 'MOD_SET_INCOMPLETE') {
		return error.details?.missing ?? [];
	}
	return [];
}

function installedFor(entry: ModSetEntry, mods: readonly ModInfo[]): ModInfo | null {
	const byForgeId =
		entry.forge_id === ''
			? undefined
			: mods.find((mod) => mod.forge_id === entry.forge_id);
	return byForgeId ?? mods.find((mod) => matchesName(mod, entry)) ?? null;
}

function matchesName(mod: ModInfo, entry: ModSetEntry): boolean {
	return mod.name.toLowerCase() === entry.name.toLowerCase();
}

/**
 * The state a row of the set shows, decided in this order: `off` when the set
 * turns the mod off or the installation has it turned off, `version` when the
 * set names another version than the installed one, `on` otherwise. A mod that
 * does not run is off no matter which version it has. Rows for mods that are
 * not installed never reach here; they are `missing`.
 */
function entryStatus(entry: ModSetEntry, mod: ModInfo): EntryStatus {
	if (!entry.enabled || !ACTIVE_STATES.has(mod.state)) {
		return 'off';
	}
	return entry.version !== '' && entry.version !== mod.version ? 'version' : 'on';
}
