import { describe, expect, it, vi } from 'vitest';
import { runUpdates, summarize, updatableFrom, updatableFromCatalog } from './updateall';
import type { UpdateAllDeps, UpdateAllResult, UpdateTarget } from './updateall';
import type {
	BlockedUpdate,
	CatalogEntry,
	CatalogMod,
	CatalogVersion,
	ModInfo,
	UpdateCandidate,
	UpdatesReport
} from './api/client';
import type { components } from './api/types';

type IncompatibleMod = components['schemas']['IncompatibleMod'];

let nextCatalogId = 1;

function makeCatalogMod(overrides: Partial<CatalogMod> = {}): CatalogMod {
	const id = nextCatalogId++;
	return {
		id,
		hub_id: null,
		guid: null,
		name: `Mod ${id}`,
		slug: `mod-${id}`,
		teaser: null,
		thumbnail: null,
		favourites_count: 0,
		endorsements_count: 0,
		fika_compatibility: null,
		featured: false,
		contains_ads: false,
		contains_ai_content: false,
		category: null,
		license_name: null,
		license_link: null,
		source_links: [],
		author: `Author ${id}`,
		downloads: 0,
		detail_url: `https://example.test/mod/${id}`,
		published_at: null,
		created_at: null,
		updated_at: null,
		...overrides
	};
}

function makeCatalogVersion(overrides: Partial<CatalogVersion> = {}): CatalogVersion {
	const id = nextCatalogId++;
	return {
		id,
		mod_id: id,
		version: '1.0.0',
		link: null,
		content_length: null,
		spt_version_constraint: '~3.9.0',
		downloads: 0,
		published_at: null,
		updated_at: null,
		...overrides
	};
}

function makeCatalogEntry(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
	return {
		mod: makeCatalogMod(),
		state: 'available',
		installed_version: null,
		latest_version: makeCatalogVersion(),
		...overrides
	};
}

function makeModInfo(overrides: Partial<ModInfo> = {}): ModInfo {
	return {
		name: 'Some Mod',
		version: '1.0.0',
		mod_type: 'client',
		author: 'Some Author',
		spt_range: '~3.9.0',
		state: 'installed',
		commit: 'abc123',
		date: '2026-01-01T00:00:00Z',
		forge_id: '',
		forge_version_id: '',
		guid: 'com.example.somemod',
		source: '',
		...overrides
	};
}

function makeCandidate(overrides: Partial<UpdateCandidate> = {}): UpdateCandidate {
	return {
		name: 'Mod A',
		installed_version: '1.0.0',
		new_version: '1.1.0',
		version_id: 1,
		link: '',
		reason: '',
		...overrides
	};
}

function makeBlocked(overrides: Partial<BlockedUpdate> = {}): BlockedUpdate {
	return {
		name: 'Blocked Mod',
		installed_version: '1.0.0',
		new_version: '1.1.0',
		reason: '',
		blocking_mods: [],
		...overrides
	};
}

function makeIncompatible(overrides: Partial<IncompatibleMod> = {}): IncompatibleMod {
	return {
		name: 'Incompatible Mod',
		installed_version: '1.0.0',
		reason: '',
		latest_compatible_version: '',
		...overrides
	};
}

function makeReport(overrides: Partial<UpdatesReport> = {}): UpdatesReport {
	return {
		spt_version: '3.9.0',
		available: [],
		blocked: [],
		up_to_date: [],
		incompatible: [],
		skipped: [],
		...overrides
	};
}

function makeTarget(overrides: Partial<UpdateTarget> = {}): UpdateTarget {
	return {
		name: 'Mod A',
		installedVersion: '1.0.0',
		newVersion: '1.1.0',
		...overrides
	};
}

describe('updatableFrom', () => {
	it('maps each available candidate to an UpdateTarget with the same field values', () => {
		const report = makeReport({
			available: [
				makeCandidate({ name: 'Alpha', installed_version: '1.0.0', new_version: '2.0.0' })
			]
		});
		expect(updatableFrom(report)).toEqual([
			{ name: 'Alpha', installedVersion: '1.0.0', newVersion: '2.0.0' }
		]);
	});

	it('preserves the order of the available list', () => {
		const report = makeReport({
			available: [
				makeCandidate({ name: 'Alpha' }),
				makeCandidate({ name: 'Beta' }),
				makeCandidate({ name: 'Gamma' })
			]
		});
		expect(updatableFrom(report).map((t) => t.name)).toEqual(['Alpha', 'Beta', 'Gamma']);
	});

	it('excludes blocked, up_to_date, incompatible and skipped entries', () => {
		const report = makeReport({
			available: [makeCandidate({ name: 'Available Mod' })],
			blocked: [makeBlocked({ name: 'Blocked Mod' })],
			up_to_date: ['Up To Date Mod'],
			incompatible: [makeIncompatible({ name: 'Incompatible Mod' })],
			skipped: ['Skipped Mod']
		});
		expect(updatableFrom(report).map((t) => t.name)).toEqual(['Available Mod']);
	});

	it('returns an empty list for a report with an empty available list', () => {
		expect(updatableFrom(makeReport())).toEqual([]);
	});
});

describe('updatableFromCatalog', () => {
	it('returns an empty list for empty entries', () => {
		expect(updatableFromCatalog([], [])).toEqual([]);
	});

	it('includes only rows whose row state is update_available, and the count matches', () => {
		const updateEntry = makeCatalogEntry({
			mod: makeCatalogMod({ name: 'Updatable' }),
			state: 'update_available',
			installed_version: '1.0.0',
			latest_version: makeCatalogVersion({ version: '2.0.0' })
		});
		const upToDateEntry = makeCatalogEntry({
			mod: makeCatalogMod({ name: 'Current' }),
			state: 'installed',
			installed_version: '1.0.0',
			latest_version: makeCatalogVersion({ version: '1.0.0' })
		});
		const notInstalledEntry = makeCatalogEntry({
			mod: makeCatalogMod({ name: 'Uninstalled' }),
			state: 'available',
			installed_version: null,
			latest_version: makeCatalogVersion({ version: '1.0.0' })
		});

		const result = updatableFromCatalog(
			[updateEntry, upToDateEntry, notInstalledEntry],
			[makeModInfo({ name: 'Current', state: 'installed' })]
		);

		expect(result).toHaveLength(1);
		expect(result[0].name).toBe('Updatable');
	});

	it('excludes an entry with a newer version when the installed mod is disabled', () => {
		const entry = makeCatalogEntry({
			mod: makeCatalogMod({ name: 'Disabled Mod' }),
			state: 'update_available',
			installed_version: '1.0.0',
			latest_version: makeCatalogVersion({ version: '2.0.0' })
		});
		const installed = [makeModInfo({ name: 'Disabled Mod', state: 'disabled' })];

		expect(updatableFromCatalog([entry], installed)).toEqual([]);
	});

	it('excludes an installed, up-to-date entry (state "installed")', () => {
		const entry = makeCatalogEntry({
			mod: makeCatalogMod({ name: 'Current' }),
			state: 'installed',
			installed_version: '1.0.0',
			latest_version: makeCatalogVersion({ version: '1.0.0' })
		});
		const installed = [makeModInfo({ name: 'Current', state: 'installed' })];

		expect(updatableFromCatalog([entry], installed)).toEqual([]);
	});

	it('excludes a not-installed entry (state "available")', () => {
		const entry = makeCatalogEntry({
			mod: makeCatalogMod({ name: 'Never Installed' }),
			state: 'available',
			installed_version: null,
			latest_version: makeCatalogVersion({ version: '1.0.0' })
		});

		expect(updatableFromCatalog([entry], [])).toEqual([]);
	});

	it('resolves the target name by Forge id, taking precedence over a name match', () => {
		const entry = makeCatalogEntry({
			mod: makeCatalogMod({ id: 5, name: 'Catalog Display Name' }),
			state: 'update_available',
			installed_version: '1.0.0',
			latest_version: makeCatalogVersion({ version: '2.0.0' })
		});
		const installed = [
			makeModInfo({ name: 'Wrong Name Match', forge_id: '999' }),
			// Same lowercase name as the catalog entry would also match by name,
			// but the Forge id match below must win.
			makeModInfo({ name: 'catalog display name', forge_id: '111' }),
			makeModInfo({ name: 'Forge Id Match', forge_id: '5' })
		];

		const result = updatableFromCatalog([entry], installed);

		expect(result[0].name).toBe('Forge Id Match');
	});

	it('falls back to a case-insensitive mod-name match when no Forge id matches', () => {
		const entry = makeCatalogEntry({
			mod: makeCatalogMod({ id: 42, name: 'Some Mod' }),
			state: 'update_available',
			installed_version: '1.0.0',
			latest_version: makeCatalogVersion({ version: '2.0.0' })
		});
		const installed = [makeModInfo({ name: 'SOME MOD', forge_id: '' })];

		const result = updatableFromCatalog([entry], installed);

		expect(result[0].name).toBe('SOME MOD');
	});

	it('falls back to the catalog mod name when no installed record matches by Forge id or name', () => {
		const entry = makeCatalogEntry({
			mod: makeCatalogMod({ id: 42, name: 'Unmatched Mod' }),
			state: 'update_available',
			installed_version: '1.0.0',
			latest_version: makeCatalogVersion({ version: '2.0.0' })
		});
		const installed = [makeModInfo({ name: 'Different Mod', forge_id: '999' })];

		const result = updatableFromCatalog([entry], installed);

		expect(result[0].name).toBe('Unmatched Mod');
	});

	it('carries the catalog installed_version and latest_version, not the installed record version', () => {
		const entry = makeCatalogEntry({
			mod: makeCatalogMod({ id: 1, name: 'Versioned Mod' }),
			state: 'update_available',
			installed_version: '1.0.0',
			latest_version: makeCatalogVersion({ version: '2.0.0' })
		});
		const installed = [makeModInfo({ name: 'Versioned Mod', forge_id: '1', version: '9.9.9' })];

		const result = updatableFromCatalog([entry], installed);

		expect(result[0].installedVersion).toBe('1.0.0');
		expect(result[0].newVersion).toBe('2.0.0');
	});

	it('falls back to an empty string for installedVersion and newVersion when catalog data is missing', () => {
		const entry = makeCatalogEntry({
			mod: makeCatalogMod({ name: 'Sparse Mod' }),
			state: 'update_available',
			installed_version: null,
			latest_version: null
		});
		const installed = [makeModInfo({ name: 'Sparse Mod' })];

		const result = updatableFromCatalog([entry], installed);

		expect(result[0].installedVersion).toBe('');
		expect(result[0].newVersion).toBe('');
	});
});

describe('runUpdates', () => {
	it('runs updates strictly sequentially, never starting the next before the previous ends', async () => {
		const log: string[] = [];
		const gates = new Map<string, () => void>();
		const update = vi.fn((name: string) => {
			log.push(`start:${name}`);
			return new Promise<void>((resolve) => {
				gates.set(name, () => {
					log.push(`end:${name}`);
					resolve();
				});
			});
		});
		const deps: UpdateAllDeps = { update, onProgress: vi.fn() };
		const targets = [makeTarget({ name: 'Alpha' }), makeTarget({ name: 'Beta' })];

		const resultPromise = runUpdates(targets, deps);

		// Only the first update has been started; the second must not start
		// until the first's promise settles.
		await vi.waitFor(() => expect(log).toEqual(['start:Alpha']));
		expect(gates.has('Beta')).toBe(false);

		gates.get('Alpha')!();
		await vi.waitFor(() => expect(log).toEqual(['start:Alpha', 'end:Alpha', 'start:Beta']));

		gates.get('Beta')!();
		await resultPromise;

		expect(log).toEqual(['start:Alpha', 'end:Alpha', 'start:Beta', 'end:Beta']);
	});

	it('calls newOperationId once per target and passes each id to update', async () => {
		const ids = ['op-1', 'op-2'];
		const newOperationId = vi.fn(() => ids.shift()!);
		const update = vi.fn().mockResolvedValue(undefined);
		const deps: UpdateAllDeps = { update, onProgress: vi.fn(), newOperationId };
		const targets = [makeTarget({ name: 'Alpha' }), makeTarget({ name: 'Beta' })];

		await runUpdates(targets, deps);

		expect(newOperationId).toHaveBeenCalledTimes(2);
		expect(update).toHaveBeenNthCalledWith(1, 'Alpha', 'op-1');
		expect(update).toHaveBeenNthCalledWith(2, 'Beta', 'op-2');
	});

	it('records a rejection with an Error cause under name and message', async () => {
		const update = vi.fn().mockRejectedValue(new Error('disk full'));
		const deps: UpdateAllDeps = {
			update,
			onProgress: vi.fn(),
			newOperationId: () => 'op-1'
		};

		const result = await runUpdates([makeTarget({ name: 'Alpha' })], deps);

		expect(result.failed).toEqual([{ name: 'Alpha', message: 'disk full' }]);
		expect(result.done).toEqual([]);
	});

	it('records a rejection with a non-Error cause by stringifying it', async () => {
		const update = vi.fn().mockRejectedValue('backend exploded');
		const deps: UpdateAllDeps = {
			update,
			onProgress: vi.fn(),
			newOperationId: () => 'op-1'
		};

		const result = await runUpdates([makeTarget({ name: 'Alpha' })], deps);

		expect(result.failed).toEqual([{ name: 'Alpha', message: 'backend exploded' }]);
	});

	it('continues running later targets after an earlier one fails, and resolves rather than rejecting', async () => {
		const update = vi
			.fn()
			.mockRejectedValueOnce(new Error('boom'))
			.mockResolvedValueOnce(undefined)
			.mockRejectedValueOnce(new Error('boom again'));
		const deps: UpdateAllDeps = {
			update,
			onProgress: vi.fn(),
			newOperationId: () => 'op'
		};
		const targets = [
			makeTarget({ name: 'Alpha' }),
			makeTarget({ name: 'Beta' }),
			makeTarget({ name: 'Gamma' })
		];

		await expect(runUpdates(targets, deps)).resolves.toEqual({
			total: 3,
			done: ['Beta'],
			failed: [
				{ name: 'Alpha', message: 'boom' },
				{ name: 'Gamma', message: 'boom again' }
			]
		});
	});

	it('sets total to the target count and done to the successes in order', async () => {
		const update = vi.fn().mockResolvedValue(undefined);
		const deps: UpdateAllDeps = { update, onProgress: vi.fn(), newOperationId: () => 'op' };
		const targets = [makeTarget({ name: 'Alpha' }), makeTarget({ name: 'Beta' })];

		const result = await runUpdates(targets, deps);

		expect(result).toEqual({ total: 2, done: ['Alpha', 'Beta'], failed: [] });
	});

	it('reports 1-based index, total and the mod name to onProgress before each update starts', async () => {
		const calls: Array<[number, number, string]> = [];
		const onProgress = vi.fn((index: number, total: number, name: string) => {
			calls.push([index, total, name]);
		});
		const update = vi.fn().mockResolvedValue(undefined);
		const deps: UpdateAllDeps = { update, onProgress, newOperationId: () => 'op' };
		const targets = [
			makeTarget({ name: 'Alpha' }),
			makeTarget({ name: 'Beta' }),
			makeTarget({ name: 'Gamma' })
		];

		await runUpdates(targets, deps);

		expect(calls).toEqual([
			[1, 3, 'Alpha'],
			[2, 3, 'Beta'],
			[3, 3, 'Gamma']
		]);
	});

	it('resolves with an empty result and calls neither update, onProgress nor newOperationId for an empty target list', async () => {
		const update = vi.fn();
		const onProgress = vi.fn();
		const newOperationId = vi.fn();
		const deps: UpdateAllDeps = { update, onProgress, newOperationId };

		const result = await runUpdates([], deps);

		expect(result).toEqual({ total: 0, done: [], failed: [] });
		expect(update).not.toHaveBeenCalled();
		expect(onProgress).not.toHaveBeenCalled();
		expect(newOperationId).not.toHaveBeenCalled();
	});
});

describe('summarize', () => {
	it('reports "up to date" wording when total is 0', () => {
		const result: UpdateAllResult = { total: 0, done: [], failed: [] };
		expect(summarize(result)).toBe('Everything is up to date.');
	});

	it('uses singular wording for exactly one successful update', () => {
		const result: UpdateAllResult = { total: 1, done: ['Alpha'], failed: [] };
		expect(summarize(result)).toBe('Updated 1 mod.');
	});

	it('uses plural wording for more than one successful update', () => {
		const result: UpdateAllResult = { total: 2, done: ['Alpha', 'Beta'], failed: [] };
		expect(summarize(result)).toBe('Updated 2 mods.');
	});

	it('uses plural wording for zero successful updates out of a non-empty target list', () => {
		const result: UpdateAllResult = {
			total: 1,
			done: [],
			failed: [{ name: 'Alpha', message: 'boom' }]
		};
		expect(summarize(result)).toContain('Updated 0 mods');
	});

	it('includes each failed mod name and its message when there are failures', () => {
		const result: UpdateAllResult = {
			total: 2,
			done: [],
			failed: [
				{ name: 'Alpha', message: 'disk full' },
				{ name: 'Beta', message: 'network timeout' }
			]
		};
		const text = summarize(result);
		expect(text).toContain('Alpha');
		expect(text).toContain('disk full');
		expect(text).toContain('Beta');
		expect(text).toContain('network timeout');
		expect(text).toContain('2 failed');
	});

	it('never uses git vocabulary in its wording', () => {
		const result: UpdateAllResult = {
			total: 2,
			done: [],
			failed: [{ name: 'Alpha', message: 'boom' }]
		};
		const text = summarize(result).toLowerCase();
		expect(text).not.toContain('commit');
		expect(text).not.toContain('revert');
		expect(text).not.toContain('repo');
		expect(text).not.toContain('branch');
	});
});
