import type { CatalogEntry, ModInfo, UpdatesReport } from './api/client';
import { rowState } from './modfilter';

export interface UpdateTarget {
	name: string;
	installedVersion: string;
	newVersion: string;
}

export interface UpdateFailure {
	name: string;
	message: string;
}

export interface UpdateAllResult {
	total: number;
	done: string[];
	failed: UpdateFailure[];
}

export interface UpdateAllDeps {
	update: (name: string, operationId: string) => Promise<unknown>;
	onProgress: (index: number, total: number, name: string) => void;
	newOperationId?: () => string;
}

/**
 * Only the `available` candidates can actually be updated: a blocked update
 * has no downloadable version, and the other buckets carry nothing to do.
 */
export function updatableFrom(report: UpdatesReport): UpdateTarget[] {
	return report.available.map((candidate) => ({
		name: candidate.name,
		installedVersion: candidate.installed_version,
		newVersion: candidate.new_version
	}));
}

/**
 * The same targets the rows show as updatable: the row state decides, so a
 * disabled mod is not counted even when a newer version exists. `updateMod`
 * addresses a mod by its install trailer name, which differs from the Forge
 * display name for many mods.
 */
export function updatableFromCatalog(
	entries: readonly CatalogEntry[],
	installed: readonly ModInfo[]
): UpdateTarget[] {
	const disabledMods = installed
		.filter((mod) => mod.state === 'disabled')
		.map((mod) => mod.name);
	const byForgeId = new Map(
		installed.filter((mod) => mod.forge_id !== '').map((mod) => [mod.forge_id, mod] as const)
	);
	const byName = new Map(installed.map((mod) => [mod.name.toLowerCase(), mod] as const));
	return entries
		.filter((entry) => rowState(entry, disabledMods) === 'update_available')
		.map((entry) => {
			const mod =
				byForgeId.get(String(entry.mod.id)) ?? byName.get(entry.mod.name.toLowerCase());
			return {
				name: mod?.name ?? entry.mod.name,
				installedVersion: entry.installed_version ?? '',
				newVersion: entry.latest_version?.version ?? ''
			};
		});
}

/**
 * Update the targets one after another. Two mod operations at once would
 * queue on the backend lock for minutes, so the next update only starts once
 * the previous one finished; a failing mod is recorded and the run continues.
 */
export async function runUpdates(
	targets: UpdateTarget[],
	deps: UpdateAllDeps
): Promise<UpdateAllResult> {
	const newOperationId = deps.newOperationId ?? (() => crypto.randomUUID());
	const done: string[] = [];
	const failed: UpdateFailure[] = [];
	for (let index = 0; index < targets.length; index += 1) {
		const target = targets[index];
		deps.onProgress(index + 1, targets.length, target.name);
		try {
			await deps.update(target.name, newOperationId());
			done.push(target.name);
		} catch (cause) {
			failed.push({
				name: target.name,
				message: cause instanceof Error ? cause.message : String(cause)
			});
		}
	}
	return { total: targets.length, done, failed };
}

export function summarize(result: UpdateAllResult): string {
	if (result.total === 0) {
		return 'Everything is up to date.';
	}
	const updated = `Updated ${count(result.done.length)}`;
	if (result.failed.length === 0) {
		return `${updated}.`;
	}
	const reasons = result.failed
		.map((failure) => `${failure.name}: ${failure.message}`)
		.join(' — ');
	return `${updated}, ${result.failed.length} failed. ${reasons}`;
}

function count(mods: number): string {
	return mods === 1 ? '1 mod' : `${mods} mods`;
}
