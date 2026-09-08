/**
 * Component tests for the mod browser.
 *
 * Setup: `ModsView` is mounted with canned catalog, install and favorite
 * answers. Assertions read the rendered rows, the shared filter state and the
 * payloads the real `$lib/api/client` produced. Reason: the view is the place
 * where filter, selection and bulk actions meet the backend, and only the
 * mounted component shows whether a filter change reaches the rows and whether
 * a bulk action addresses a mod by its install name.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CatalogEntry, ModInfo } from '$lib/api/client';
import { clearFilter } from '$lib/filterstate';
import { setSubNav } from '$lib/shellstate';
import type { UpdateTarget } from '$lib/updateall';
import { invalidate } from '$lib/viewcache';

import ModsView from '$lib/components/ModsView.svelte';
import { apiError } from '../support/errors';
import { catalogEntry, catalogMod, catalogVersion, modInfo } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

const GAME_DIR = '/games/SPT';

const alpha = catalogEntry({
	mod: catalogMod({ id: 1, name: 'Alpha', author: 'Ada', category: 'Gameplay' })
});

const beta = catalogEntry({
	mod: catalogMod({ id: 2, name: 'Beta', author: 'Bo', category: 'Weapons', downloads: 900 }),
	state: 'installed',
	installed_version: '1.0.0'
});

interface ViewStubs {
	catalog?: CatalogEntry[];
	mods?: ModInfo[];
	favorites?: number[];
}

function stubView(stubs: ViewStubs = {}): void {
	rpc.returns('browse_catalog', stubs.catalog ?? [alpha, beta]);
	rpc.returns('list_mods', stubs.mods ?? []);
	rpc.returns('list_favorites', { mod_ids: stubs.favorites ?? [] });
}

interface Recorded {
	busy: boolean[];
	updatable: UpdateTarget[][];
}

function mount(): Recorded {
	const recorded: Recorded = { busy: [], updatable: [] };
	render(ModsView, {
		props: {
			path: GAME_DIR,
			refreshKey: 0,
			locked: false,
			sptVersion: '4.1.0',
			onbusy: (busy: boolean) => recorded.busy.push(busy),
			onupdatable: (targets: UpdateTarget[]) => recorded.updatable.push(targets)
		}
	});
	return recorded;
}

/**
 * The quick-tab strip. `Installed` is also the label of a sort column, so the
 * tabs have to be addressed inside their own strip.
 */
function quickTabs(): HTMLElement {
	const strip = screen.getByRole('button', { name: 'Favorites' }).parentElement;
	if (strip === null) {
		throw new Error('the quick tabs are not rendered');
	}
	return strip;
}

/** The mod names of the rendered rows, in the order the grid shows them. */
function rowNames(): string[] {
	return screen
		.getAllByRole('checkbox')
		.slice(1)
		.map((box) => (box.getAttribute('aria-label') ?? '').replace('Select ', ''));
}

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setSubNav([], null);
});

describe('loading the catalog', () => {
	it('browses the whole catalog and leaves the compatibility filter to the client', async () => {
		stubView();
		mount();

		expect(await screen.findByText('Alpha')).toBeInTheDocument();
		expect(rpc.payloads('browse_catalog')).toEqual([
			{ path: GAME_DIR, filter: 'all', compatible_only: false }
		]);
	});

	it('shows a row per catalog entry with its versions and its state', async () => {
		stubView();
		mount();

		expect(await screen.findByLabelText('Select Beta')).toBeInTheDocument();
		const grid = screen.getByRole('grid', { name: 'Mods' });
		expect(within(grid).getByText('Active')).toBeInTheDocument();
		expect(within(grid).getByText('Not installed')).toBeInTheDocument();
		expect(screen.getByText('2 results')).toBeInTheDocument();
	});

	it('reports the mods with a newer version to the page that embeds it', async () => {
		const updatable = catalogEntry({
			mod: catalogMod({ id: 3, name: 'Gamma' }),
			state: 'update_available',
			installed_version: '1.0.0',
			latest_version: catalogVersion({ version: '2.0.0' })
		});
		stubView({
			catalog: [alpha, updatable],
			mods: [modInfo({ name: 'gamma-trailer', forge_id: '3', state: 'installed' })]
		});
		const recorded = mount();

		expect(await screen.findByText('Gamma')).toBeInTheDocument();
		expect(recorded.updatable.at(-1)).toEqual([
			{ name: 'gamma-trailer', installedVersion: '1.0.0', newVersion: '2.0.0' }
		]);
	});

	it('shows the failure message when the catalog cannot be read', async () => {
		stubView();
		rpc.rejects('browse_catalog', apiError('The catalog cache is broken.', 'CACHE_BROKEN'));
		mount();

		expect(await screen.findByText('The catalog cache is broken.')).toBeInTheDocument();
		expect(screen.getByText('No mods match the current filters.')).toBeInTheDocument();
	});

	it('hides entries the catalog has no compatible version for', async () => {
		stubView({
			catalog: [alpha, catalogEntry({ mod: catalogMod({ id: 9, name: 'Old' }), latest_version: null })]
		});
		mount();

		expect(await screen.findByText('Alpha')).toBeInTheDocument();
		expect(screen.queryByText('Old')).toBeNull();
		expect(screen.getByText('1 result')).toBeInTheDocument();
	});
});

describe('selecting rows', () => {
	it('counts the rows the player picked', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await user.click(await screen.findByRole('checkbox', { name: 'Select Alpha' }));

		expect(await screen.findByText('1 selected')).toBeInTheDocument();
	});

	it('takes every visible row with the header checkbox and drops them all again', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Alpha');
		const header = screen.getAllByRole('checkbox')[0];
		expect(header).toHaveAccessibleName('Select all mods');

		await user.click(header);
		expect(await screen.findByText('2 selected')).toBeInTheDocument();

		await user.click(screen.getAllByRole('checkbox')[0]);
		expect(screen.queryByText('2 selected')).toBeNull();
	});

	it('forgets a selected row that the filter hides', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await user.click(await screen.findByRole('checkbox', { name: 'Select Alpha' }));
		expect(await screen.findByText('1 selected')).toBeInTheDocument();

		await user.click(within(quickTabs()).getByRole('button', { name: 'Installed' }));

		expect(await screen.findByText('1 result')).toBeInTheDocument();
		expect(screen.queryByText('1 selected')).toBeNull();
	});

	it('clears the selection on request', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await user.click(await screen.findByRole('checkbox', { name: 'Select Alpha' }));
		await user.click(await screen.findByRole('button', { name: 'Clear selection' }));

		expect(screen.queryByText('1 selected')).toBeNull();
	});
});

describe('bulk actions', () => {
	const disabledMod = modInfo({ name: 'beta-trailer', forge_id: '2', state: 'disabled' });

	it('enables the picked mods by their install name, one after another', async () => {
		const user = userEvent.setup();
		stubView({
			catalog: [beta],
			mods: [{ ...disabledMod, name: 'Beta' }]
		});
		rpc.returns('enable_mod', { name: 'Beta', state: 'enabled' });
		mount();

		await user.click(await screen.findByRole('checkbox', { name: 'Select Beta' }));
		await user.click(await screen.findByRole('button', { name: 'Enable (1)' }));

		expect(rpc.payloads('enable_mod')).toEqual([{ path: GAME_DIR, name: 'Beta' }]);
	});

	it('disables the picked mods by the install trailer name, not the Forge name', async () => {
		const user = userEvent.setup();
		stubView({
			catalog: [beta],
			mods: [modInfo({ name: 'beta-trailer', forge_id: '2', state: 'installed' })]
		});
		rpc.returns('disable_mod', { name: 'beta-trailer', state: 'disabled' });
		mount();

		await user.click(await screen.findByRole('checkbox', { name: 'Select Beta' }));
		await user.click(await screen.findByRole('button', { name: 'Disable (1)' }));

		expect(rpc.payloads('disable_mod')).toEqual([{ path: GAME_DIR, name: 'beta-trailer' }]);
	});

	it('updates only the picked rows that carry a newer version', async () => {
		const user = userEvent.setup();
		const outdated = catalogEntry({
			mod: catalogMod({ id: 3, name: 'Gamma' }),
			state: 'update_available',
			installed_version: '1.0.0',
			latest_version: catalogVersion({ version: '2.0.0' })
		});
		stubView({
			catalog: [beta, outdated],
			mods: [
				modInfo({ name: 'beta-trailer', forge_id: '2', state: 'installed' }),
				modInfo({ name: 'gamma-trailer', forge_id: '3', state: 'installed' })
			]
		});
		rpc.returns('update_mod', { name: 'gamma-trailer', version: '2.0.0', commit: 'c0ffee' });
		mount();

		await screen.findByLabelText('Select Gamma');
		await user.click(screen.getAllByRole('checkbox')[0]);
		await user.click(await screen.findByRole('button', { name: 'Update selected (1)' }));

		expect(rpc.payloads('update_mod')).toEqual([
			{ path: GAME_DIR, name: 'gamma-trailer', force: false, operation_id: null }
		]);
	});

	it('names every mod the bulk run could not change', async () => {
		const user = userEvent.setup();
		stubView({
			catalog: [beta],
			mods: [{ ...disabledMod, name: 'Beta' }]
		});
		rpc.rejects('enable_mod', apiError('The files are gone.', 'MOD_MISSING'));
		mount();

		await user.click(await screen.findByRole('checkbox', { name: 'Select Beta' }));
		await user.click(await screen.findByRole('button', { name: 'Enable (1)' }));

		expect(
			await screen.findByText('Some mods could not be changed. Beta: The files are gone.')
		).toBeInTheDocument();
	});

	it('reports the busy state around the run to the page that embeds it', async () => {
		const user = userEvent.setup();
		stubView({
			catalog: [beta],
			mods: [{ ...disabledMod, name: 'Beta' }]
		});
		rpc.returns('enable_mod', { name: 'Beta', state: 'enabled' });
		const recorded = mount();

		await user.click(await screen.findByRole('checkbox', { name: 'Select Beta' }));
		await user.click(await screen.findByRole('button', { name: 'Enable (1)' }));

		expect(recorded.busy).toEqual([true, false]);
	});
});

describe('quick tabs', () => {
	it('narrows the rows to the installed mods and marks the tab as the current one', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(within(quickTabs()).getByRole('button', { name: 'Installed' }));

		expect(await screen.findByText('1 result')).toBeInTheDocument();
		expect(rowNames()).toEqual(['Beta']);
		expect(within(quickTabs()).getByRole('button', { name: 'Installed' })).toHaveAttribute(
			'aria-current',
			'page'
		);
	});

	it('narrows the rows to the favorites the backend reported', async () => {
		const user = userEvent.setup();
		stubView({ favorites: [1] });
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(screen.getByRole('button', { name: 'Favorites' }));

		expect(await screen.findByText('1 result')).toBeInTheDocument();
		expect(rowNames()).toEqual(['Alpha']);
	});

	it('leaves no row when nothing was seen inside the new-mods window', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(screen.getByRole('button', { name: 'New' }));

		expect(await screen.findByText('0 results')).toBeInTheDocument();
	});

	it('keeps the mods the catalog first saw inside the new-mods window', async () => {
		const user = userEvent.setup();
		const fresh = catalogEntry({
			mod: catalogMod({
				id: 4,
				name: 'Fresh',
				first_seen_at: new Date(Date.now() - 86_400_000).toISOString()
			})
		});
		stubView({ catalog: [alpha, fresh] });
		mount();

		await screen.findByLabelText('Select Fresh');
		await user.click(screen.getByRole('button', { name: 'New' }));

		expect(await screen.findByText('1 result')).toBeInTheDocument();
		expect(rowNames()).toEqual(['Fresh']);
	});

	it('goes back to every row when All is picked again', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(within(quickTabs()).getByRole('button', { name: 'Installed' }));
		await screen.findByText('1 result');
		await user.click(within(quickTabs()).getByRole('button', { name: 'All' }));

		expect(await screen.findByText('2 results')).toBeInTheDocument();
	});
});

describe('the filter bar', () => {
	it('offers the SPT version of the game directory as the compatibility value', async () => {
		stubView();
		mount();

		expect(await screen.findByRole('button', { name: 'SPT 4.1.0' })).toBeInTheDocument();
	});

	it('adds a category pill and narrows the rows to that category', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(screen.getByRole('button', { name: 'Add filter' }));
		await user.click(await screen.findByRole('button', { name: 'Category' }));

		const popover = await screen.findByRole('dialog', { name: 'Filter by Category' });
		await user.click(within(popover).getByRole('radio', { name: 'Weapons' }));
		await user.click(within(popover).getByRole('button', { name: 'Apply' }));

		expect(await screen.findByText('1 result')).toBeInTheDocument();
		expect(rowNames()).toEqual(['Beta']);
	});

	it('removes the compatibility pill and lets incompatible mods back in', async () => {
		const user = userEvent.setup();
		stubView({
			catalog: [alpha, catalogEntry({ mod: catalogMod({ id: 9, name: 'Old' }), latest_version: null })]
		});
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(screen.getByRole('button', { name: 'Remove Compatibility filter' }));

		expect(await screen.findByText('2 results')).toBeInTheDocument();
		expect(rowNames()).toEqual(['Alpha', 'Old']);
	});

	it('restores the default filter when the filters are cleared', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(screen.getByRole('button', { name: 'Remove Compatibility filter' }));
		await screen.findByText('2 results');

		await user.click(screen.getByRole('button', { name: 'Clear filters' }));

		expect(await screen.findByRole('button', { name: 'SPT 4.1.0' })).toBeInTheDocument();
	});
});

describe('sorting', () => {
	it('sorts by name ascending, then descending, then back to the catalog order', async () => {
		const user = userEvent.setup();
		stubView({ catalog: [beta, alpha] });
		mount();

		await screen.findByLabelText('Select Alpha');
		expect(rowNames()).toEqual(['Beta', 'Alpha']);

		await user.click(screen.getByRole('button', { name: 'Mod' }));
		expect(rowNames()).toEqual(['Alpha', 'Beta']);

		await user.click(screen.getByRole('button', { name: 'Mod ▲' }));
		expect(rowNames()).toEqual(['Beta', 'Alpha']);

		await user.click(screen.getByRole('button', { name: 'Mod ▼' }));
		expect(rowNames()).toEqual(['Beta', 'Alpha']);
		expect(screen.getByRole('button', { name: 'Mod' })).toBeInTheDocument();
	});

	it('starts the download column at the biggest number', async () => {
		const user = userEvent.setup();
		stubView({ catalog: [beta, alpha] });
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(screen.getByRole('button', { name: 'Downloads' }));

		expect(rowNames()).toEqual(['Beta', 'Alpha']);
		expect(screen.getByRole('button', { name: 'Downloads ▼' })).toBeInTheDocument();
	});
});

describe('saving the current mods as a set', () => {
	it('stores the set under the name the player typed and confirms it', async () => {
		const user = userEvent.setup();
		stubView();
		rpc.returns('list_mod_sets', { sets: [], bindings: [] });
		rpc.returns('save_mod_set', { name: 'Raid night', created_at: '2026-01-01', entries: [] });
		mount();

		await user.click(await screen.findByRole('button', { name: 'Save current mods as set' }));
		await user.type(screen.getByLabelText('Set name'), 'Raid night');
		await user.click(screen.getByRole('button', { name: 'Save' }));

		expect(rpc.payloads('save_mod_set')).toEqual([
			{ path: GAME_DIR, name: 'Raid night', entries: null }
		]);
		expect(
			await screen.findByText('The mods that are on now are saved as "Raid night".')
		).toBeInTheDocument();
	});

	it('asks before replacing a set that already carries the name', async () => {
		const user = userEvent.setup();
		stubView();
		rpc.returns('list_mod_sets', {
			sets: [{ name: 'Raid night', created_at: '2026-01-01', entries: [] }],
			bindings: []
		});
		rpc.returns('save_mod_set', { name: 'Raid night', created_at: '2026-01-02', entries: [] });
		mount();

		await user.click(await screen.findByRole('button', { name: 'Save current mods as set' }));
		await user.type(screen.getByLabelText('Set name'), 'Raid night');
		await user.click(screen.getByRole('button', { name: 'Save' }));

		expect(
			await screen.findByText('A set named "Raid night" already exists. Replace its mods?')
		).toBeInTheDocument();
		expect(rpc.payloads('save_mod_set')).toEqual([]);

		await user.click(screen.getByRole('button', { name: 'Replace' }));

		expect(rpc.payloads('save_mod_set')).toEqual([
			{ path: GAME_DIR, name: 'Raid night', entries: null }
		]);
	});

	it('shows the failure message when the set cannot be stored', async () => {
		const user = userEvent.setup();
		stubView();
		rpc.returns('list_mod_sets', { sets: [], bindings: [] });
		rpc.rejects('save_mod_set', apiError('That name is not allowed.', 'BAD_NAME'));
		mount();

		await user.click(await screen.findByRole('button', { name: 'Save current mods as set' }));
		await user.type(screen.getByLabelText('Set name'), 'Raid night');
		await user.click(screen.getByRole('button', { name: 'Save' }));

		expect(await screen.findByText('That name is not allowed.')).toBeInTheDocument();
	});
});
