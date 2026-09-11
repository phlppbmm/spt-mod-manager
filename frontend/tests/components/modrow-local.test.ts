/**
 * Component tests for the row of a mod the player installed themselves.
 *
 * Setup: `ModsView` is mounted with one catalog entry marked `local` — the
 * shape the backend synthesises for an installed mod the Forge catalog holds no
 * row for — and the installed mod behind it. Assertions read the rendered row.
 * Reason: such a row has no Forge facts at all, so everything the list shows for
 * it comes from what git recorded, and every Forge action has to be gone. None
 * of that is observable outside the mounted row.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CatalogEntry, ModInfo } from '$lib/api/client';
import { clearFilter } from '$lib/filterstate';
import { setSubNav } from '$lib/shellstate';
import { invalidate } from '$lib/viewcache';

import ModsView from '$lib/components/ModsView.svelte';
import { catalogEntry, catalogMod, modInfo } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

const GAME_DIR = '/games/SPT';

const ownEntry = catalogEntry({
	mod: catalogMod({
		id: -4711,
		name: 'My Own Mod',
		author: 'Me',
		teaser: null,
		downloads: 0,
		updated_at: null,
		detail_url: ''
	}),
	state: 'installed',
	installed_version: '1.2.0',
	latest_version: null,
	local: true
});

const ownMod = modInfo({
	name: 'My Own Mod',
	version: '1.2.0',
	forge_id: '',
	forge_version_id: '',
	state: 'installed',
	mod_type: 'server',
	spt_range: '~4.1.0'
});

function stubView(catalog: CatalogEntry[], mods: ModInfo[]): void {
	rpc.returns('browse_catalog', catalog);
	rpc.returns('list_mods', mods);
	rpc.returns('list_favorites', { mod_ids: [] });
}

function mount(): void {
	render(ModsView, {
		props: {
			path: GAME_DIR,
			refreshKey: 0,
			locked: false,
			sptVersion: '4.1.0',
			onbusy: () => undefined,
			onupdatable: () => undefined
		}
	});
}

/** The grid row of `name`, addressed through its own select checkbox. */
function rowOf(name: string): HTMLElement {
	const row = screen
		.getAllByRole('row')
		.find((candidate) => within(candidate).queryByLabelText(`Select ${name}`) !== null);
	if (row === undefined) {
		throw new Error(`no row for ${name}`);
	}
	return row;
}

/** The row's overflow menu, opened the way a player opens it. */
async function openMenu(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
	await user.click(await screen.findByLabelText('More actions'));
	const menu = screen.getByLabelText('More actions').closest('details');
	if (menu === null) {
		throw new Error('the row has no action menu');
	}
	return menu;
}

/** Narrow the list to one mod type, the way a player picks that filter. */
async function filterByType(
	user: ReturnType<typeof userEvent.setup>,
	label: string
): Promise<void> {
	await user.click(screen.getByRole('button', { name: 'Add filter' }));
	await user.click(await screen.findByRole('button', { name: 'Type' }));
	const popover = await screen.findByRole('dialog', { name: 'Filter by Type' });
	await user.click(within(popover).getByRole('radio', { name: new RegExp(`^${label}`) }));
	await user.click(within(popover).getByRole('button', { name: 'Apply' }));
}

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setSubNav([], null);
});

describe('the row of a mod the player installed themselves', () => {
	it('is listed although the Forge knows nothing about the mod', async () => {
		stubView([ownEntry], [ownMod]);
		mount();

		expect(await screen.findByText('My Own Mod')).toBeInTheDocument();
	});

	it('survives the compatibility filter the list starts with', async () => {
		stubView([ownEntry], [ownMod]);
		mount();

		await screen.findByText('My Own Mod');

		expect(
			screen.getByRole('button', { name: 'Remove Compatibility filter' })
		).toBeInTheDocument();
		expect(await screen.findByText('1 result')).toBeInTheDocument();
	});

	it('marks the row as local so it is not mistaken for a Forge mod', async () => {
		stubView([ownEntry], [ownMod]);
		mount();

		await screen.findByText('My Own Mod');

		expect(within(rowOf('My Own Mod')).getByText('Local')).toBeInTheDocument();
	});

	it('shows the SPT versions git recorded instead of a dash', async () => {
		stubView([ownEntry], [ownMod]);
		mount();

		expect(await screen.findByText('~4.1.0')).toBeInTheDocument();
	});

	it('offers no version to update to', async () => {
		stubView([ownEntry], [ownMod]);
		mount();

		await screen.findByText('My Own Mod');
		const row = rowOf('My Own Mod');
		expect(within(row).queryByRole('button', { name: 'Update' })).toBeNull();
		expect(within(row).queryByRole('button', { name: 'Install' })).toBeNull();
	});

	it('still offers turning the mod off, removing it and listing its files', async () => {
		const user = userEvent.setup();
		stubView([ownEntry], [ownMod]);
		mount();

		const menu = await openMenu(user);

		expect(screen.getAllByRole('button', { name: 'Disable' }).length).toBeGreaterThan(0);
		expect(within(menu).getByRole('button', { name: 'Remove' })).toBeInTheDocument();
		expect(within(menu).getByRole('button', { name: 'Files' })).toBeInTheDocument();
	});

	it('offers neither the Forge page nor the catalog details', async () => {
		const user = userEvent.setup();
		stubView([ownEntry], [ownMod]);
		mount();

		const menu = await openMenu(user);

		expect(within(menu).queryByRole('link', { name: 'Open Forge page' })).toBeNull();
		expect(within(menu).queryByRole('button', { name: 'Details' })).toBeNull();
	});

	it('asks the backend for no catalog details when the row is used', async () => {
		const user = userEvent.setup();
		stubView([ownEntry], [ownMod]);
		mount();

		await user.click(await screen.findByText('My Own Mod'));

		expect(rpc.names()).not.toContain('get_mod_details');
	});

	it('asks for no catalog details when the row is opened from the keyboard', async () => {
		const user = userEvent.setup();
		stubView([ownEntry], [ownMod]);
		mount();

		await screen.findByText('My Own Mod');
		rowOf('My Own Mod').focus();
		await user.keyboard('{Enter}');

		expect(rpc.names()).not.toContain('get_mod_details');
	});

	it('is kept by the mod-type filter of its own type', async () => {
		const user = userEvent.setup();
		stubView([ownEntry], [ownMod]);
		mount();

		await screen.findByText('My Own Mod');
		await filterByType(user, 'Server');

		expect(await screen.findByText('1 result')).toBeInTheDocument();
	});

	it('is dropped by the mod-type filter of the other type', async () => {
		const user = userEvent.setup();
		stubView([ownEntry], [ownMod]);
		mount();

		await screen.findByText('My Own Mod');
		await filterByType(user, 'Client');

		expect(screen.queryByText('My Own Mod')).toBeNull();
	});
});
