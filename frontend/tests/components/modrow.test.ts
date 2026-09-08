/**
 * Component tests for a mod row, driven through the view that owns it.
 *
 * Setup: `ModsView` is mounted with a single catalog entry and the installed
 * mods that decide the row state; the row is then used the way a player uses
 * it. Assertions read the rendered row, its dialogs and the payloads the real
 * `$lib/api/client` produced. Reason: the row turns one catalog entry plus one
 * installed mod into an install, update, enable, disable or remove request, and
 * addresses the mod by its install trailer name — none of that is observable
 * outside the mounted row.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CatalogEntry, ModInfo } from '$lib/api/client';
import { clearFilter } from '$lib/filterstate';
import { requestIntent, takeIntents } from '$lib/intents';
import { setSubNav } from '$lib/shellstate';
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
	mod: catalogMod({
		id: 47,
		name: 'Alpha',
		author: 'Ada',
		teaser: 'Adds a rifle.',
		downloads: 12_345,
		updated_at: '2026-02-03T10:00:00Z',
		detail_url: 'https://sp-mod.com/mod/47/alpha'
	})
});

const installedAlpha = modInfo({
	name: 'alpha-trailer',
	forge_id: '47',
	state: 'installed',
	mod_type: 'client'
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

/**
 * The primary action button of a row. `Disable` and `Enable` also sit in the
 * overflow menu, whose markup is present even while the menu is folded, so the
 * menu entries are excluded here.
 */
function rowAction(name: string, label: string): HTMLElement {
	const button = within(rowOf(name))
		.getAllByRole('button', { name: label })
		.find((candidate) => candidate.closest('details') === null);
	if (button === undefined) {
		throw new Error(`no ${label} action on the row of ${name}`);
	}
	return button;
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

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setSubNav([], null);
	takeIntents(['reveal-mod']);
});

describe('what a row shows', () => {
	it('shows the catalog facts of a mod that is not installed', async () => {
		stubView([alpha], []);
		mount();

		expect(await screen.findByText('Alpha')).toBeInTheDocument();
		expect(screen.getByText('Ada')).toBeInTheDocument();
		expect(screen.getByText('Adds a rifle.')).toBeInTheDocument();
		expect(screen.getByText('12,345')).toBeInTheDocument();
		expect(screen.getByText('2026-02-03')).toBeInTheDocument();
		expect(screen.getByText('~4.1.0')).toBeInTheDocument();
		expect(screen.getByText('Not installed')).toBeInTheDocument();
	});

	it('shows the installed version and the mod type of an installed mod', async () => {
		stubView(
			[catalogEntry({ mod: alpha.mod, state: 'installed', installed_version: '0.9.0' })],
			[installedAlpha]
		);
		mount();

		expect(await screen.findByText('0.9.0')).toBeInTheDocument();
		expect(screen.getByText('client')).toBeInTheDocument();
		expect(screen.getByText('Active')).toBeInTheDocument();
	});

	it('marks a mod without a compatible version as incompatible and refuses the install', async () => {
		const user = userEvent.setup();
		stubView([catalogEntry({ mod: alpha.mod, latest_version: null })], []);
		mount();

		await user.click(await screen.findByRole('button', { name: 'Remove Compatibility filter' }));

		expect(await screen.findByText('Incompatible')).toBeInTheDocument();
		const button = screen.getByRole('button', { name: 'Install' });
		expect(button).toBeDisabled();
		expect(button).toHaveAttribute('title', 'No version compatible with your SPT version');
	});

	it('links to the Forge page of the mod', async () => {
		stubView([alpha], []);
		mount();

		expect(await screen.findByRole('link', { name: 'Open Forge page' })).toHaveAttribute(
			'href',
			'https://sp-mod.com/mod/47/alpha'
		);
	});
});

describe('installing a mod', () => {
	it('shows the plan before it installs and then installs the catalog id', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.returns('plan_mod_install', {
			spt_version: '4.1.0',
			entries: [
				{
					mod_id: 47,
					name: 'Alpha',
					guid: null,
					version: '1.0.0',
					version_id: 470,
					link: null,
					content_length: 2_097_152,
					is_dependency: false
				},
				{
					mod_id: 48,
					name: 'Alpha Core',
					guid: null,
					version: '2.0.0',
					version_id: 480,
					link: null,
					content_length: null,
					is_dependency: true
				}
			]
		});
		rpc.returns('install_mod', {
			plan: { spt_version: '4.1.0', entries: [] },
			installed: [],
			operation_id: 'op'
		});
		mount();

		await user.click(await screen.findByRole('button', { name: 'Install' }));

		const dialog = await screen.findByRole('dialog', { name: 'Install Alpha' });
		expect(rpc.payloads('plan_mod_install')).toEqual([
			{ path: GAME_DIR, mod_id_or_name: 47, version: null }
		]);
		expect(within(dialog).getByText('Alpha 1.0.0')).toBeInTheDocument();
		expect(within(dialog).getByText('2.0 MB')).toBeInTheDocument();
		expect(within(dialog).getByText('dependency')).toBeInTheDocument();
		expect(within(dialog).getByText('unknown size')).toBeInTheDocument();
		expect(within(dialog).getByText('Resolved for SPT 4.1.0.')).toBeInTheDocument();

		await user.click(within(dialog).getByRole('button', { name: 'Install' }));

		const [payload] = rpc.payloads('install_mod') as { mod_id_or_name: number; force: boolean }[];
		expect(payload.mod_id_or_name).toBe(47);
		expect(payload.force).toBe(false);
	});

	it('shows the failure message when the plan cannot be made', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.rejects('plan_mod_install', apiError('No version fits your SPT.', 'NO_VERSION'));
		mount();

		await user.click(await screen.findByRole('button', { name: 'Install' }));

		const dialog = await screen.findByRole('dialog', { name: 'Alpha failed' });
		expect(within(dialog).getByText('No version fits your SPT.')).toBeInTheDocument();
	});

	it('offers to install anyway after a conflict and repeats the install with force', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.returns('plan_mod_install', { spt_version: '4.1.0', entries: [] });
		rpc.rejects(
			'install_mod',
			apiError('Another mod owns that file.', 'CONFLICT', {
				conflicts: [
					{
						kind: 'tracked-file',
						severity: 'error',
						message: 'Beta owns it.',
						path: 'user/mods/x.js',
						mod: 'Beta'
					}
				],
				installed: ['Alpha Core']
			})
		);
		mount();

		await user.click(await screen.findByRole('button', { name: 'Install' }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Install Alpha' })).getByRole('button', {
				name: 'Install'
			})
		);

		const failure = await screen.findByRole('dialog', { name: 'Alpha failed' });
		expect(within(failure).getByText('Beta owns it.')).toBeInTheDocument();
		expect(within(failure).getByText('Already installed before the failure:')).toBeInTheDocument();
		expect(within(failure).getByText('Alpha Core')).toBeInTheDocument();

		await user.click(within(failure).getByRole('button', { name: 'Install anyway' }));

		const forced = rpc.payloads('install_mod') as { force: boolean }[];
		expect(forced.map((entry) => entry.force)).toEqual([false, true]);
	});

	it('shows the progress the backend reported for the running install', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.returns('plan_mod_install', { spt_version: '4.1.0', entries: [] });
		let finish: (value: unknown) => void = () => undefined;
		rpc.handle('install_mod', () => new Promise<unknown>((resolve) => (finish = resolve)));
		mount();

		await user.click(await screen.findByRole('button', { name: 'Install' }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Install Alpha' })).getByRole('button', {
				name: 'Install'
			})
		);

		const [payload] = rpc.payloads('install_mod') as { operation_id: string }[];
		rpc.progress({
			operation_id: payload.operation_id,
			step: 'download',
			percent: 42.4,
			message: 'Downloading Alpha'
		});

		expect(await screen.findByTitle('Downloading Alpha')).toHaveTextContent('download 42%');

		finish({ plan: { spt_version: '4.1.0', entries: [] }, installed: [], operation_id: 'op' });
		await vi.waitFor(() => {
			expect(screen.queryByTitle('Downloading Alpha')).toBeNull();
		});
	});
});

describe('updating a mod', () => {
	const outdated = catalogEntry({
		mod: alpha.mod,
		state: 'update_available',
		installed_version: '0.9.0',
		latest_version: catalogVersion({ version: '1.0.0' })
	});

	it('updates by the install trailer name, not the Forge name', async () => {
		const user = userEvent.setup();
		stubView([outdated], [installedAlpha]);
		rpc.returns('update_mod', { name: 'alpha-trailer', version: '1.0.0' });
		mount();

		await user.click(await screen.findByRole('button', { name: 'Update' }));

		const [payload] = rpc.payloads('update_mod') as { name: string; force: boolean }[];
		expect(payload.name).toBe('alpha-trailer');
		expect(payload.force).toBe(false);
	});

	it('offers to update anyway after a refused update', async () => {
		const user = userEvent.setup();
		stubView([outdated], [installedAlpha]);
		rpc.rejects('update_mod', apiError('A dependency is missing.', 'DEPENDENCY'));
		mount();

		await user.click(await screen.findByRole('button', { name: 'Update' }));

		const failure = await screen.findByRole('dialog', { name: 'Alpha failed' });
		expect(within(failure).getByRole('button', { name: 'Update anyway' })).toBeInTheDocument();
	});
});

describe('turning a mod off and on', () => {
	it('confirms before it disables and then disables the trailer name', async () => {
		const user = userEvent.setup();
		stubView(
			[catalogEntry({ mod: alpha.mod, state: 'installed', installed_version: '1.0.0' })],
			[installedAlpha]
		);
		rpc.returns('disable_mod', { name: 'alpha-trailer', state: 'disabled' });
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(rowAction('Alpha', 'Disable'));
		const dialog = await screen.findByRole('dialog', { name: 'Disable Alpha?' });
		await user.click(within(dialog).getByRole('button', { name: 'Disable' }));

		expect(rpc.payloads('disable_mod')).toEqual([{ path: GAME_DIR, name: 'alpha-trailer' }]);
	});

	it('enables a disabled mod straight from the row', async () => {
		const user = userEvent.setup();
		stubView(
			[catalogEntry({ mod: alpha.mod, state: 'installed', installed_version: '1.0.0' })],
			[{ ...installedAlpha, state: 'disabled', name: 'Alpha' }]
		);
		rpc.returns('enable_mod', { name: 'Alpha', state: 'enabled' });
		mount();

		await screen.findByLabelText('Select Alpha');
		await user.click(rowAction('Alpha', 'Enable'));

		expect(rpc.payloads('enable_mod')).toEqual([{ path: GAME_DIR, name: 'Alpha' }]);
	});
});

describe('removing a mod', () => {
	const installedEntry = catalogEntry({
		mod: alpha.mod,
		state: 'installed',
		installed_version: '1.0.0'
	});

	it('reports what the profiles hold before the removal is confirmed', async () => {
		const user = userEvent.setup();
		stubView([installedEntry], [installedAlpha]);
		rpc.returns('plan_profile_cleanup', {
			mod: 'alpha-trailer',
			profiles: ['aaa'],
			removals: [
				{
					profile: 'aaa',
					category: 'item',
					ids: ['x1', 'x2'],
					count: 2,
					character: 'pmc',
					path: ''
				}
			],
			untouched: [
				{
					residue: {
						profile: 'aaa',
						category: 'string',
						ids: ['y'],
						count: 1,
						character: '',
						path: ''
					},
					reason: 'unidentified'
				}
			],
			warnings: []
		});
		rpc.returns('remove_mod', { name: 'alpha-trailer', commit: 'c0ffee', files: [] });
		mount();

		const menu = await openMenu(user);
		await user.click(within(menu).getByRole('button', { name: 'Remove' }));

		const dialog = await screen.findByRole('dialog', { name: 'Remove Alpha?' });
		expect(rpc.payloads('plan_profile_cleanup')).toEqual([
			{ path: GAME_DIR, mod_name: 'alpha-trailer' }
		]);
		expect(await within(dialog).findByText('aaa.json')).toBeInTheDocument();
		expect(
			within(dialog).getByText('1 reference sptmod cannot identify stay untouched.')
		).toBeInTheDocument();

		await user.click(within(dialog).getByLabelText('Also clean the player profiles'));
		await user.click(within(dialog).getByRole('button', { name: 'Remove' }));

		expect(rpc.payloads('remove_mod')).toEqual([
			{ path: GAME_DIR, name: 'alpha-trailer', force: false, clean_profiles: true }
		]);
	});

	it('says the profiles hold nothing of the mod and keeps the cleanup off', async () => {
		const user = userEvent.setup();
		stubView([installedEntry], [installedAlpha]);
		rpc.returns('plan_profile_cleanup', {
			mod: 'alpha-trailer',
			profiles: [],
			removals: [],
			untouched: [],
			warnings: []
		});
		mount();

		const menu = await openMenu(user);
		await user.click(within(menu).getByRole('button', { name: 'Remove' }));

		const dialog = await screen.findByRole('dialog', { name: 'Remove Alpha?' });
		expect(
			await within(dialog).findByText('The player profiles hold nothing of this mod.')
		).toBeInTheDocument();
		expect(within(dialog).getByLabelText('Also clean the player profiles')).toBeDisabled();
	});

	it('says so when the profiles could not be looked through', async () => {
		const user = userEvent.setup();
		stubView([installedEntry], [installedAlpha]);
		rpc.rejects('plan_profile_cleanup', apiError('A profile is locked.', 'LOCKED'));
		mount();

		const menu = await openMenu(user);
		await user.click(within(menu).getByRole('button', { name: 'Remove' }));

		expect(
			await screen.findByText('sptmod could not look through the player profiles.')
		).toBeInTheDocument();
	});

	it('offers to remove anyway when a profile still holds traces of the mod', async () => {
		const user = userEvent.setup();
		stubView([installedEntry], [installedAlpha]);
		rpc.returns('plan_profile_cleanup', {
			mod: 'alpha-trailer',
			profiles: [],
			removals: [],
			untouched: [],
			warnings: []
		});
		rpc.rejects(
			'remove_mod',
			apiError('The profiles still hold items of it.', 'PROFILE_RESIDUE', {
				residues: [
					{
						profile: 'aaa',
						category: 'item',
						ids: ['x1'],
						count: 1,
						character: 'pmc',
						path: ''
					}
				]
			})
		);
		mount();

		const menu = await openMenu(user);
		await user.click(within(menu).getByRole('button', { name: 'Remove' }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Remove Alpha?' })).getByRole('button', {
				name: 'Remove'
			})
		);

		const failure = await screen.findByRole('dialog', { name: 'Alpha failed' });
		expect(within(failure).getByText('item: 1 reference')).toBeInTheDocument();
		await user.click(within(failure).getByRole('button', { name: 'Remove anyway' }));

		const calls = rpc.payloads('remove_mod') as { force: boolean }[];
		expect(calls.map((entry) => entry.force)).toEqual([false, true]);
	});
});

describe('the row menu', () => {
	const installedEntry = catalogEntry({
		mod: alpha.mod,
		state: 'installed',
		installed_version: '1.0.0'
	});

	it('lists the files of the installed mod', async () => {
		const user = userEvent.setup();
		stubView([installedEntry], [installedAlpha]);
		rpc.returns('mod_files', [{ path: 'user/mods/Alpha/mod.js', change: 'A' }]);
		mount();

		const menu = await openMenu(user);
		await user.click(within(menu).getByRole('button', { name: 'Files' }));

		expect(rpc.payloads('mod_files')).toEqual([{ path: GAME_DIR, name: 'alpha-trailer' }]);
		const dialog = await screen.findByRole('dialog', { name: 'Files of Alpha' });
		expect(within(dialog).getByText('user/mods/Alpha/mod.js')).toBeInTheDocument();
	});

	it('offers neither files nor removal for a mod that is not installed', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		mount();

		const menu = await openMenu(user);

		expect(within(menu).queryByRole('button', { name: 'Files' })).toBeNull();
		expect(within(menu).queryByRole('button', { name: 'Remove' })).toBeNull();
		expect(within(menu).getByRole('button', { name: 'Details' })).toBeInTheDocument();
	});
});

describe('the details dialog', () => {
	it('shows what the Forge knows about the mod and its versions', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.returns('get_mod_details', {
			mod: catalogMod({
				id: 47,
				name: 'Alpha',
				author: 'Ada',
				downloads: 12_345,
				favourites_count: 7,
				endorsements_count: 3,
				category: 'Weapons',
				featured: true,
				contains_ads: true,
				contains_ai_content: true,
				fika_compatibility: true,
				license_name: 'MIT',
				license_link: 'https://example.test/mit',
				source_links: [{ label: 'GitHub', url: 'https://example.test/repo' }],
				updated_at: '2026-02-03T10:00:00Z'
			}),
			versions: [
				catalogVersion({ id: 470, version: '1.0.0', fika_compatibility: 'compatible' }),
				catalogVersion({
					id: 471,
					version: '0.9.0',
					fika_compatibility: 'incompatible',
					spt_version_constraint: '',
					published_at: null
				})
			],
			description: 'A rifle pack.'
		});
		mount();

		await user.click(await screen.findByRole('button', { name: 'Alpha' }));

		expect(rpc.payloads('get_mod_details')).toEqual([{ mod_id: 47 }]);
		const dialog = await screen.findByRole('dialog', { name: 'Alpha' });
		expect(within(dialog).getByText('A rifle pack.')).toBeInTheDocument();
		expect(within(dialog).getByText('7 favourites')).toBeInTheDocument();
		expect(within(dialog).getByText('3 endorsements')).toBeInTheDocument();
		expect(within(dialog).getByText('contains ads')).toBeInTheDocument();
		expect(within(dialog).getByText('AI content')).toBeInTheDocument();
		expect(within(dialog).getByRole('link', { name: 'MIT' })).toHaveAttribute(
			'href',
			'https://example.test/mit'
		);
		expect(within(dialog).getByRole('link', { name: 'GitHub' })).toBeInTheDocument();
		expect(within(dialog).getByText('No Fika')).toBeInTheDocument();
		expect(within(dialog).getByText('last updated 2026-02-03')).toBeInTheDocument();
	});

	it('says so when the Forge sent no description', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.returns('get_mod_details', { mod: null, versions: [], description: '' });
		mount();

		await user.click(await screen.findByRole('button', { name: 'Alpha' }));

		const dialog = await screen.findByRole('dialog', { name: 'Alpha' });
		expect(within(dialog).getByText('No description available.')).toBeInTheDocument();
	});

	it('shows the failure message when the details cannot be read', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.rejects('get_mod_details', apiError('The Forge is unreachable.', 'FORGE_DOWN'));
		mount();

		await user.click(await screen.findByRole('button', { name: 'Alpha' }));

		const failure = await screen.findByRole('dialog', { name: 'Alpha failed' });
		expect(within(failure).getByText('The Forge is unreachable.')).toBeInTheDocument();
	});
});

describe('favorites', () => {
	it('marks a mod as a favorite and takes the new list from the backend', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.returns('set_favorite', { mod_ids: [47] });
		mount();

		await user.click(await screen.findByRole('button', { name: 'Add to favorites' }));

		expect(rpc.payloads('set_favorite')).toEqual([{ mod_id: 47, favorite: true }]);
		expect(
			await screen.findByRole('button', { name: 'Remove from favorites' })
		).toBeInTheDocument();
	});

	it('takes a mod off the favorites again', async () => {
		const user = userEvent.setup();
		rpc.returns('browse_catalog', [alpha]);
		rpc.returns('list_mods', []);
		rpc.returns('list_favorites', { mod_ids: [47] });
		rpc.returns('set_favorite', { mod_ids: [] });
		mount();

		await user.click(await screen.findByRole('button', { name: 'Remove from favorites' }));

		expect(rpc.payloads('set_favorite')).toEqual([{ mod_id: 47, favorite: false }]);
	});

	it('shows the failure message when the favorite cannot be stored', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.rejects('set_favorite', apiError('The favorites file is read-only.', 'PERMISSION'));
		mount();

		await user.click(await screen.findByRole('button', { name: 'Add to favorites' }));

		const failure = await screen.findByRole('dialog', { name: 'Alpha failed' });
		expect(within(failure).getByText('The favorites file is read-only.')).toBeInTheDocument();
	});
});

describe('keyboard use of the rows', () => {
	const beta = catalogEntry({ mod: catalogMod({ id: 48, name: 'Beta', author: 'Bo' }) });

	it('walks down the rows and selects the focused one with the space bar', async () => {
		const user = userEvent.setup();
		stubView([alpha, beta], []);
		mount();

		await screen.findByLabelText('Select Beta');
		rowOf('Alpha').focus();
		await user.keyboard('{ArrowDown}');
		await user.keyboard(' ');

		expect(await screen.findByText('1 selected')).toBeInTheDocument();
		expect(screen.getByRole('checkbox', { name: 'Select Beta' })).toBeChecked();
	});

	it('opens the details of the focused row with Enter', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.returns('get_mod_details', { mod: null, versions: [], description: 'A rifle pack.' });
		mount();

		await screen.findByLabelText('Select Alpha');
		rowOf('Alpha').focus();
		await user.keyboard('{Enter}');

		expect(await screen.findByRole('dialog', { name: 'Alpha' })).toBeInTheDocument();
	});

	it('marks the focused row as a favorite with the f key', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		rpc.returns('set_favorite', { mod_ids: [47] });
		mount();

		await screen.findByLabelText('Select Alpha');
		rowOf('Alpha').focus();
		await user.keyboard('f');

		expect(rpc.payloads('set_favorite')).toEqual([{ mod_id: 47, favorite: true }]);
	});

	it('gives up the row focus on Escape', async () => {
		const user = userEvent.setup();
		stubView([alpha], []);
		mount();

		await screen.findByLabelText('Select Alpha');
		const row = rowOf('Alpha');
		row.focus();
		expect(row).toHaveFocus();

		await user.keyboard('{Escape}');
		expect(row).not.toHaveFocus();
	});
});

describe('revealing a mod from elsewhere', () => {
	it('focuses the row of the mod another page asked for', async () => {
		stubView([alpha, catalogEntry({ mod: catalogMod({ id: 48, name: 'Beta' }) })], []);
		mount();

		await screen.findByLabelText('Select Beta');
		requestIntent({ kind: 'reveal-mod', modId: 48 });

		await vi.waitFor(() => {
			expect(document.getElementById('modrow-48')).toHaveFocus();
		});
	});
});
