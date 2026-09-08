/**
 * Component tests for the mods page.
 *
 * Setup: the page is mounted with a stored game directory and canned answers
 * for the status, launch and catalog RPCs. Assertions read the rendered phase,
 * the notes and the payloads the real `$lib/api/client` produced. Reason: the
 * page owns the phase machine, the catalog sync and the "update all" run, and
 * none of that is reachable from the unit tests of the helper modules.
 *
 * Ordering: `+page.svelte` keeps the game directories it auto-synced in a
 * module-level set that no public export resets, and vitest isolates modules
 * per file, not per test. The auto-sync test therefore has to run first.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CatalogEntry, ModInfo, StatusReport, SyncCatalogResponse } from '$lib/api/client';
import { clearFilter } from '$lib/filterstate';
import { takeIntents } from '$lib/intents';
import { setGameDir, setSubNav } from '$lib/shellstate';
import { invalidate } from '$lib/viewcache';

import Page from '../../src/routes/+page.svelte';
import { apiError } from '../support/errors';
import { catalogEntry, catalogMod, catalogVersion, launchStatus, modInfo } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

vi.mock('@tauri-apps/plugin-dialog', () => ({
	open: vi.fn(() => Promise.resolve(null)),
	save: vi.fn(() => Promise.resolve(null))
}));

vi.mock('@tauri-apps/plugin-opener', () => ({
	openUrl: vi.fn(() => Promise.resolve())
}));

const GAME_DIR = '/games/SPT';

const statusReport: StatusReport = {
	spt_version: '4.1.0',
	modified_profiles: [],
	modified_configs: [],
	modified_mod_settings: [],
	manual_mods: [],
	game_files_changed: [],
	other_modified: [],
	other_untracked: []
};

const syncResponse: SyncCatalogResponse = {
	new_mods: 0,
	updated_mods: 0,
	new_versions: 0,
	updated_versions: 0,
	pages: 1,
	full: false,
	operation_id: 'op'
};

const outdated: CatalogEntry = catalogEntry({
	mod: catalogMod({ id: 3, name: 'Gamma' }),
	state: 'update_available',
	installed_version: '1.0.0',
	latest_version: catalogVersion({ version: '2.0.0' })
});

const installedGamma: ModInfo = modInfo({
	name: 'gamma-trailer',
	forge_id: '3',
	state: 'installed'
});

interface PageStubs {
	catalog?: CatalogEntry[];
	mods?: ModInfo[];
	running?: boolean;
}

function stubPage(stubs: PageStubs = {}): void {
	rpc.returns('status', statusReport);
	rpc.returns('launch_status', launchStatus({ client_running: stubs.running ?? false }));
	rpc.returns('sync_catalog', syncResponse);
	rpc.returns('browse_catalog', stubs.catalog ?? []);
	rpc.returns('list_mods', stubs.mods ?? []);
	rpc.returns('list_favorites', { mod_ids: [] });
}

beforeEach(() => {
	localStorage.setItem('sptmod.game_dir', GAME_DIR);
});

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setSubNav([], null);
	setGameDir(null);
	takeIntents(['snapshot', 'launch', 'sync', 'update-all', 'change-folder', 'reveal-mod']);
});

describe('the first visit of a game directory', () => {
	it('syncs the catalog once on its own', async () => {
		stubPage();
		render(Page);

		await screen.findByRole('heading', { name: 'Mods' });
		await vi.waitFor(() => {
			expect(rpc.payloads('sync_catalog')).toHaveLength(1);
		});
		const [payload] = rpc.payloads('sync_catalog') as { path: string; full: boolean }[];
		expect(payload.path).toBe(GAME_DIR);
		expect(payload.full).toBe(false);
	});
});

describe('the catalog sync', () => {
	it('summarizes what the sync brought in', async () => {
		const user = userEvent.setup();
		stubPage();
		rpc.returns('sync_catalog', { ...syncResponse, new_mods: 1 });
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Sync catalog' }));

		expect(await screen.findByText('Catalog updated · 1 new')).toBeInTheDocument();
	});

	it('spells out changed mods and version updates in the summary', async () => {
		const user = userEvent.setup();
		stubPage();
		rpc.returns('sync_catalog', {
			...syncResponse,
			new_mods: 2,
			updated_mods: 3,
			new_versions: 4,
			updated_versions: 1
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Sync catalog' }));

		expect(
			await screen.findByText('Catalog updated · 2 new, 3 changed, 5 version updates')
		).toBeInTheDocument();
	});

	it('says the catalog was already current when nothing changed', async () => {
		const user = userEvent.setup();
		stubPage();
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Sync catalog' }));

		expect(await screen.findByText('Catalog is up to date')).toBeInTheDocument();
	});

	it('shows the sync progress the backend reported', async () => {
		const user = userEvent.setup();
		stubPage();
		let operationId = '';
		let finish: (value: SyncCatalogResponse) => void = () => undefined;
		rpc.handle('sync_catalog', (payload) => {
			operationId = (payload as { operation_id: string }).operation_id;
			return new Promise<SyncCatalogResponse>((resolve) => {
				finish = resolve;
			});
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Sync catalog' }));
		await vi.waitFor(() => {
			expect(rpc.subscribed(operationId)).toBe(true);
		});
		rpc.progress({
			operation_id: operationId,
			step: 'page',
			percent: 40,
			message: 'Page 2 of 5'
		});

		expect(
			await screen.findByText(/Updating the mod catalog…\s*40% — Page 2 of 5/)
		).toBeInTheDocument();

		finish(syncResponse);
		expect(await screen.findByText('Catalog is up to date')).toBeInTheDocument();
	});

	it('shows the failure message when the sync fails', async () => {
		const user = userEvent.setup();
		stubPage();
		rpc.rejects('sync_catalog', apiError('The Forge is unreachable.', 'FORGE_UNREACHABLE'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Sync catalog' }));

		expect(await screen.findByText('The Forge is unreachable.')).toBeInTheDocument();
	});
});

describe('updating every outdated mod', () => {
	it('offers nothing to update while no row reported a newer version', async () => {
		stubPage();
		render(Page);

		const button = await screen.findByRole('button', { name: 'Update all (0)' });
		expect(button).toBeDisabled();
	});

	it('updates the outdated mods by their install name and reports the result', async () => {
		const user = userEvent.setup();
		stubPage({ catalog: [outdated], mods: [installedGamma] });
		rpc.returns('update_mod', { name: 'gamma-trailer', version: '2.0.0', commit: 'c0ffee' });
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Update all (1)' }));

		expect(await screen.findByText('Updated 1 mod.')).toBeInTheDocument();
		const [payload] = rpc.payloads('update_mod') as { name: string; force: boolean }[];
		expect(payload.name).toBe('gamma-trailer');
		expect(payload.force).toBe(false);
	});

	it('shows the progress of the mod that is being updated', async () => {
		const user = userEvent.setup();
		stubPage({ catalog: [outdated], mods: [installedGamma] });
		let finish: (value: unknown) => void = () => undefined;
		rpc.handle('update_mod', () => {
			return new Promise<unknown>((resolve) => {
				finish = resolve;
			});
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Update all (1)' }));
		expect(await screen.findByText('Updating gamma-trailer (1 of 1)')).toBeInTheDocument();

		const [payload] = rpc.payloads('update_mod') as { operation_id: string }[];
		rpc.progress({
			operation_id: payload.operation_id,
			step: 'download',
			percent: 60,
			message: 'Downloading'
		});
		await vi.waitFor(() => {
			expect(screen.getByRole('progressbar')).toHaveAttribute('value', '60');
		});

		finish({ name: 'gamma-trailer', version: '2.0.0', commit: 'c0ffee' });
		expect(await screen.findByText('Updated 1 mod.')).toBeInTheDocument();
	});

	it('keeps the failed mod as a candidate and names the reason', async () => {
		const user = userEvent.setup();
		stubPage({ catalog: [outdated], mods: [installedGamma] });
		rpc.rejects('update_mod', apiError('The archive is broken.', 'BAD_ARCHIVE'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Update all (1)' }));

		expect(
			await screen.findByText('Updated 0 mods, 1 failed. gamma-trailer: The archive is broken.')
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Update all (1)' })).toBeInTheDocument();
	});
});

describe('while the game is running', () => {
	it('warns that mod changes are paused and locks the rows', async () => {
		stubPage({ catalog: [outdated], mods: [installedGamma], running: true });
		render(Page);

		expect(
			await screen.findByText('The game is running, mod changes are paused')
		).toBeInTheDocument();
		const grid = await screen.findByRole('grid', { name: 'Mods' });
		expect(within(grid).getByRole('checkbox', { name: 'Select Gamma' })).toBeDisabled();
	});

	it('shows no banner while the game is down', async () => {
		stubPage();
		render(Page);

		await screen.findByRole('heading', { name: 'Mods' });
		expect(screen.queryByText('The game is running, mod changes are paused')).toBeNull();
	});
});

describe('when the stored folder is no game directory', () => {
	it('drops the folder and asks for a new one', async () => {
		stubPage();
		rpc.rejects('status', apiError('This is not an SPT folder.', 'NOT_A_GAME_DIR'));
		render(Page);

		expect(await screen.findByRole('heading', { name: 'Set up sptmod' })).toBeInTheDocument();
		expect(localStorage.getItem('sptmod.game_dir')).toBeNull();
		expect(screen.getByLabelText('SPT installation folder')).toHaveValue(GAME_DIR);
	});
});

describe('when the backend cannot be reached', () => {
	it('offers to try again and gets through on the retry', async () => {
		const user = userEvent.setup();
		stubPage();
		rpc.rejects('status', apiError('The sptmod backend does not answer.', 'unreachable'));
		render(Page);

		expect(await screen.findByText('The sptmod backend does not answer.')).toBeInTheDocument();

		rpc.returns('status', statusReport);
		await user.click(screen.getByRole('button', { name: 'Try again' }));

		expect(await screen.findByRole('heading', { name: 'Mods' })).toBeInTheDocument();
	});
});
