/**
 * Component tests for the two mod-table columns that need an installation.
 *
 * Setup: `ModsView` is mounted with a catalog whose entries are installed,
 * outdated, favorited and new at once, so every quick tab lists the same
 * rows and only the layout differs; the tabs are then used the way a player
 * uses them. Assertions read the rendered header cells, the rendered row cells
 * and the row order. Reason: the installed version and the mod type are only
 * known for the tabs that list installed mods, and the sort those columns carry
 * loses its only control when they go — neither is observable outside the
 * mounted view.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { clearFilter } from '$lib/filterstate';
import { setSubNav } from '$lib/shellstate';
import { invalidate } from '$lib/viewcache';

import ModsView from '$lib/components/ModsView.svelte';
import { catalogEntry, catalogMod, catalogVersion, modInfo } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

const GAME_DIR = '/games/SPT';

/** Yesterday, so the entry falls inside the window the "New" tab asks for. */
const SEEN_RECENTLY = new Date(Date.now() - 86_400_000).toISOString();

const gamma = catalogEntry({
	mod: catalogMod({ id: 3, name: 'Gamma', author: 'Gus', first_seen_at: SEEN_RECENTLY }),
	state: 'update_available',
	installed_version: '3.1.0',
	latest_version: catalogVersion({ version: '4.0.0' })
});

const delta = catalogEntry({
	mod: catalogMod({ id: 4, name: 'Delta', author: 'Dee', first_seen_at: SEEN_RECENTLY }),
	state: 'update_available',
	installed_version: '1.2.0',
	latest_version: catalogVersion({ version: '4.0.0' })
});

const epsilon = catalogEntry({
	mod: catalogMod({ id: 5, name: 'Epsilon', author: 'Eve', first_seen_at: SEEN_RECENTLY }),
	state: 'update_available',
	installed_version: '2.0.0',
	latest_version: catalogVersion({ version: '4.0.0' })
});

/**
 * A mod the catalog only knows as disabled: it is listed on the tabs for
 * installed mods, but the catalog join carries no installed version for it.
 */
const zeta = catalogEntry({
	mod: catalogMod({ id: 6, name: 'Zeta', author: 'Zoe' }),
	latest_version: catalogVersion({ version: '4.0.0' })
});

function stubView(): void {
	rpc.returns('browse_catalog', [gamma, delta, epsilon]);
	rpc.returns('list_mods', [
		modInfo({ name: 'gamma-trailer', forge_id: '3', state: 'installed', mod_type: 'client' }),
		modInfo({ name: 'delta-trailer', forge_id: '4', state: 'installed', mod_type: 'server' }),
		modInfo({ name: 'epsilon-trailer', forge_id: '5', state: 'installed', mod_type: 'server' })
	]);
	rpc.returns('list_favorites', { mod_ids: [3, 4, 5] });
}

function stubDisabledOnly(): void {
	rpc.returns('browse_catalog', [zeta]);
	rpc.returns('list_mods', [
		modInfo({ name: 'Zeta', forge_id: '', state: 'disabled', mod_type: 'server' })
	]);
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

/**
 * The quick-tab strip. `Installed` is also the label of the column header that
 * sorts by installed version, so the tabs have to be addressed inside their own
 * strip and that header inside the grid.
 */
function tab(label: string): HTMLElement {
	const strip = screen.getByRole('button', { name: 'Favorites' }).parentElement;
	if (strip === null) {
		throw new Error('the quick tabs are not rendered');
	}
	return within(strip).getByRole('button', { name: label });
}

function grid(): HTMLElement {
	return screen.getByRole('grid', { name: 'Mods' });
}

/** The header button that sorts by installed version, with its sort marker. */
function installedHeader(): HTMLElement {
	return within(grid()).getByRole('button', { name: /^Installed/ });
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

/** The mod names of the rendered rows, in the order the grid shows them. */
function rowNames(): string[] {
	return screen
		.getAllByRole('checkbox')
		.slice(1)
		.map((box) => (box.getAttribute('aria-label') ?? '').replace('Select ', ''));
}

interface Layout {
	installedHeader: boolean;
	typeHeader: boolean;
	installedVersion: boolean;
	modType: boolean;
}

/**
 * What the table renders of the two columns that need an installation: the two
 * header cells, and the two cells of Gamma's row that fill them.
 */
function layout(): Layout {
	const row = rowOf('Gamma');
	return {
		installedHeader: within(grid()).queryByRole('button', { name: /^Installed/ }) !== null,
		typeHeader: within(grid()).queryByText('Type') !== null,
		installedVersion: within(row).queryByText('3.1.0') !== null,
		modType: within(row).queryByText('client') !== null
	};
}

const FULL: Layout = {
	installedHeader: true,
	typeHeader: true,
	installedVersion: true,
	modType: true
};

const LEAN: Layout = {
	installedHeader: false,
	typeHeader: false,
	installedVersion: false,
	modType: false
};

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setSubNav([], null);
});

describe('the columns a quick tab shows', () => {
	it.each([
		['Installed', FULL],
		['Updates', FULL],
		['All', LEAN],
		['Favorites', LEAN],
		['New', LEAN]
	] as const)('renders the %s tab with its own set of columns', async (label, expected) => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Gamma');
		await user.click(tab(label));

		expect(tab(label)).toHaveAttribute('aria-current', 'page');
		expect(layout()).toEqual(expected);
	});

	it.each(['Installed', 'Updates', 'All', 'Favorites', 'New'])(
		'leaves everything else about a row alone on the %s tab',
		async (label) => {
			const user = userEvent.setup();
			stubView();
			mount();

			await screen.findByLabelText('Select Gamma');
			await user.click(tab(label));
			const row = rowOf('Gamma');

			expect(within(row).getByText('Update available')).toBeInTheDocument();
			expect(within(row).getByText('4.0.0')).toBeInTheDocument();
			expect(within(row).getByText('~4.1.0')).toBeInTheDocument();
			expect(within(row).getByRole('button', { name: 'Update' })).toBeEnabled();
			expect(screen.getByText('3 results')).toBeInTheDocument();
		}
	);

	it('lands on the full layout again after a detour through a tab without the columns', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Gamma');
		await user.click(tab('Installed'));
		await user.click(screen.getByRole('checkbox', { name: 'Select Gamma' }));
		expect(layout()).toEqual(FULL);
		expect(screen.getByText('1 selected')).toBeInTheDocument();

		await user.click(tab('All'));
		expect(layout()).toEqual(LEAN);

		await user.click(tab('Installed'));
		expect(layout()).toEqual(FULL);
		expect(screen.getByText('1 selected')).toBeInTheDocument();
		expect(screen.getByText('3 results')).toBeInTheDocument();
	});

	it('shows a dash where the catalog knows no installed version', async () => {
		const user = userEvent.setup();
		stubDisabledOnly();
		mount();

		await screen.findByLabelText('Select Zeta');
		await user.click(tab('Installed'));
		const row = rowOf('Zeta');

		expect(within(row).getByText('Disabled')).toBeInTheDocument();
		expect(within(row).getByText('—')).toBeInTheDocument();
		expect(within(row).getByText('server')).toBeInTheDocument();
	});
});

describe('the sort the installed column carries', () => {
	it('drops the sort by installed version when the player leaves that column behind', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Gamma');
		await user.click(tab('Installed'));
		await user.click(installedHeader());

		expect(rowNames()).toEqual(['Delta', 'Epsilon', 'Gamma']);
		expect(installedHeader()).toHaveAccessibleName('Installed ▲');

		await user.click(tab('All'));
		expect(rowNames()).toEqual(['Gamma', 'Delta', 'Epsilon']);

		await user.click(tab('Installed'));
		expect(rowNames()).toEqual(['Gamma', 'Delta', 'Epsilon']);
		expect(installedHeader()).toHaveAccessibleName('Installed');
	});

	it('turns the sort by installed version around on the second click', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Gamma');
		await user.click(tab('Installed'));
		await user.click(installedHeader());
		await user.click(installedHeader());

		expect(rowNames()).toEqual(['Gamma', 'Epsilon', 'Delta']);
		expect(installedHeader()).toHaveAccessibleName('Installed ▼');
	});

	it('keeps a sort by a column every tab shows', async () => {
		const user = userEvent.setup();
		stubView();
		mount();

		await screen.findByLabelText('Select Gamma');
		await user.click(tab('Installed'));
		await user.click(within(grid()).getByRole('button', { name: 'Mod' }));
		expect(rowNames()).toEqual(['Delta', 'Epsilon', 'Gamma']);

		await user.click(tab('All'));
		expect(rowNames()).toEqual(['Delta', 'Epsilon', 'Gamma']);
		expect(within(grid()).getByRole('button', { name: 'Mod ▲' })).toBeInTheDocument();

		await user.click(tab('Favorites'));
		expect(rowNames()).toEqual(['Delta', 'Epsilon', 'Gamma']);
	});
});
