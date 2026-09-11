/**
 * Component tests for the settings page.
 *
 * Setup: the page is mounted with a stored game directory, a stubbed Tauri
 * surface (folder picker, file writer, clipboard, updater, process) and canned
 * answers for the three RPCs it issues on mount. Assertions read the rendered
 * sections and the payloads the real `$lib/api/client` produced. Reason: the
 * page wires ten independent sections to the backend, and only the mounted
 * component shows which request a button sends and which wording it reports.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getVersion } from '@tauri-apps/api/app';
import { writeText } from '@tauri-apps/plugin-clipboard-manager';
import { open, save } from '@tauri-apps/plugin-dialog';
import { writeTextFile } from '@tauri-apps/plugin-fs';
import { relaunch } from '@tauri-apps/plugin-process';
import { check } from '@tauri-apps/plugin-updater';

import { goto } from '$app/navigation';

import type {
	FikaSettings,
	ImportDiff,
	ModSet,
	SptCompatibilityReport,
	SptReleaseInfo,
	SptReleaseList
} from '$lib/api/client';
import { setSubNav } from '$lib/shellstate';
import { invalidate } from '$lib/viewcache';

import Page from '../../src/routes/settings/+page.svelte';
import { apiError } from '../support/errors';
import { modSet, sptReleaseList } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

vi.mock('$app/navigation', () => ({ goto: vi.fn(() => Promise.resolve()) }));

vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/settings#spt-version') }
}));

vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn(), save: vi.fn() }));
vi.mock('@tauri-apps/plugin-fs', () => ({ writeTextFile: vi.fn(() => Promise.resolve()) }));
vi.mock('@tauri-apps/plugin-clipboard-manager', () => ({
	writeText: vi.fn(() => Promise.resolve())
}));
vi.mock('@tauri-apps/api/app', () => ({ getVersion: vi.fn(() => Promise.resolve('1.2.3')) }));
vi.mock('@tauri-apps/plugin-process', () => ({ relaunch: vi.fn(() => Promise.resolve()) }));
vi.mock('@tauri-apps/plugin-updater', () => ({ check: vi.fn(() => Promise.resolve(null)) }));

const GAME_DIR = '/games/SPT';

function release(overrides: Partial<SptReleaseInfo> = {}): SptReleaseInfo {
	return {
		version: '4.1.0',
		client_build: '41000',
		published_at: '2026-01-15T00:00:00Z',
		url: 'https://sp-mod.com/spt/4.1.0',
		downloadable: true,
		content_length: 1024,
		installed: false,
		newer: false,
		...overrides
	};
}

/**
 * The verdict the backend returns for one target version. The builder reports a
 * version nothing was found for, so a test states only the outcome it varies.
 */
function report(overrides: Partial<SptCompatibilityReport> = {}): SptCompatibilityReport {
	return {
		spt_version: '4.2.0',
		compatible: [],
		incompatible: [],
		skipped: [],
		checked: true,
		notice: '',
		...overrides
	};
}

interface MountStubs {
	releases?: SptReleaseList;
	fika?: FikaSettings;
	sets?: ModSet[];
}

function stubMount(stubs: MountStubs = {}): void {
	rpc.returns('list_mod_sets', { sets: stubs.sets ?? [], bindings: [] });
	rpc.returns(
		'get_fika_settings',
		stubs.fika ?? { role: 'none', host_address: '', base_url: '' }
	);
	rpc.returns('list_spt_releases', stubs.releases ?? sptReleaseList());
}

/**
 * A stand-in for the `Update` handle of the updater plugin: the page only ever
 * reads its version and its notes and drives it through the two methods.
 */
interface FakeUpdate {
	version: string;
	body: string;
	downloadAndInstall: (onEvent: (event: DownloadEvent) => void) => Promise<void>;
	close: () => Promise<void>;
}

type DownloadEvent =
	| { event: 'Started'; data: { contentLength?: number } }
	| { event: 'Progress'; data: { chunkLength: number } }
	| { event: 'Finished' };

function fakeUpdate(overrides: Partial<FakeUpdate> = {}): FakeUpdate {
	return {
		version: '9.9.9',
		body: 'Fixes the launcher.',
		downloadAndInstall: () => Promise.resolve(),
		close: () => Promise.resolve(),
		...overrides
	};
}

/** The `check` mock is typed against the plugin; the fake carries what is read. */
function stubCheck(update: FakeUpdate | null): void {
	vi.mocked(check).mockResolvedValue(update as unknown as Awaited<ReturnType<typeof check>>);
}

beforeEach(() => {
	localStorage.setItem('sptmod.game_dir', GAME_DIR);
});

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	setSubNav([], null);
	vi.mocked(open).mockReset();
	vi.mocked(save).mockReset();
	vi.mocked(writeText).mockReset();
	vi.mocked(writeTextFile).mockReset();
	vi.mocked(goto).mockReset();
	vi.mocked(relaunch).mockReset();
	vi.mocked(check).mockReset();
	vi.mocked(getVersion).mockReset();
});

describe('mounting the page', () => {
	it('asks the backend for the mod sets, the Fika settings and the SPT releases only', async () => {
		stubMount();
		render(Page);

		await screen.findByRole('heading', { name: 'Settings' });
		await vi.waitFor(() => {
			expect(rpc.names().sort()).toEqual([
				'get_fika_settings',
				'list_mod_sets',
				'list_spt_releases'
			]);
		});
	});

	it('marks the section the URL fragment names as the current one', async () => {
		stubMount();
		render(Page);

		expect(await screen.findByRole('link', { name: 'SPT version' })).toHaveAttribute(
			'aria-current',
			'page'
		);
		expect(screen.getByRole('link', { name: 'Game directory' })).not.toHaveAttribute(
			'aria-current'
		);
	});

	it('shows the app version the Tauri app reported', async () => {
		vi.mocked(getVersion).mockResolvedValue('1.2.3');
		stubMount();
		render(Page);

		expect(await screen.findByText('1.2.3')).toBeInTheDocument();
	});

	it('reports the game directory it works on and returns to the setup on request', async () => {
		const user = userEvent.setup();
		stubMount();
		render(Page);

		expect(await screen.findByText(GAME_DIR)).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Change path' }));

		expect(localStorage.getItem('sptmod.game_dir')).toBeNull();
		expect(vi.mocked(goto).mock.calls).toEqual([['/']]);
	});
});

describe('the SPT release list', () => {
	const releases = sptReleaseList({
		current_version: '4.1.0',
		client_build: '41000',
		latest: '4.2.0',
		releases: [
			release({ version: '4.2.0' }),
			release({ version: '4.1.0' }),
			release({ version: '4.0.0', downloadable: false })
		]
	});

	function rowOf(version: string): HTMLElement {
		const cell = screen.getByRole('link', { name: version });
		const row = cell.closest('tr');
		if (row === null) {
			throw new Error(`no release row for ${version}`);
		}
		return row;
	}

	it('names the installed version and the game build the backend reported', async () => {
		stubMount({ releases });
		render(Page);

		expect(await screen.findByText('4.1.0', { selector: 'strong' })).toBeInTheDocument();
		expect(screen.getByText('Escape from Tarkov build 41000')).toBeInTheDocument();
	});

	it('offers no action for the release that is already installed', async () => {
		stubMount({ releases });
		render(Page);

		await screen.findByRole('link', { name: '4.2.0' });
		const row = within(rowOf('4.1.0'));
		expect(row.getByText('Installed')).toBeInTheDocument();
		expect(row.queryByRole('button')).toBeNull();
	});

	it('offers an update for a newer release', async () => {
		stubMount({ releases });
		render(Page);

		await screen.findByRole('link', { name: '4.2.0' });
		const row = within(rowOf('4.2.0'));
		expect(row.getByText('Newer')).toBeInTheDocument();
		expect(row.getByRole('button', { name: 'Update' })).toBeEnabled();
	});

	it('names why an older release cannot be installed any more', async () => {
		stubMount({ releases });
		render(Page);

		await screen.findByRole('link', { name: '4.0.0' });
		const button = within(rowOf('4.0.0')).getByRole('button', {
			name: 'Go back to this version'
		});
		expect(button).toBeDisabled();
		expect(button).toHaveAttribute(
			'title',
			'SPT no longer offers a download for 4.0.0. The official installer at https://sp-mod.com/installer can still change the game to it.'
		);
	});

	it('says so when the backend knows no releases', async () => {
		stubMount({ releases: sptReleaseList({ releases: [] }) });
		render(Page);

		expect(await screen.findByText('No SPT releases found.')).toBeInTheDocument();
	});

	it('shows the failure message when the release list cannot be read', async () => {
		stubMount();
		rpc.rejects('list_spt_releases', apiError('The release feed is down.', 'FEED_DOWN'));
		render(Page);

		expect(await screen.findByText('The release feed is down.')).toBeInTheDocument();
	});

	it('confirms an update before it applies the release', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('apply_spt_release', {
			previous_version: '4.1.0',
			version: '4.2.0',
			commit: 'abcdef1',
			files: [],
			operation_id: 'op'
		});
		render(Page);

		await screen.findByRole('link', { name: '4.2.0' });
		await user.click(within(rowOf('4.2.0')).getByRole('button', { name: 'Update' }));

		const dialog = await screen.findByRole('dialog', { name: 'Update to SPT 4.2.0?' });
		expect(
			within(dialog).getByText(/Your profiles, settings and installed mods stay where they are\./)
		).toBeInTheDocument();
		expect(rpc.payloads('apply_spt_release')).toEqual([]);

		await user.click(within(dialog).getByRole('button', { name: 'Continue' }));

		const [payload] = rpc.payloads('apply_spt_release') as { path: string; version: string }[];
		expect(payload.path).toBe(GAME_DIR);
		expect(payload.version).toBe('4.2.0');
		expect(
			await screen.findByText(
				'SPT is now 4.2.0. Mods built for the other version show up under Updates.'
			)
		).toBeInTheDocument();
	});

	it('applies nothing when the confirmation is dismissed', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		render(Page);

		await screen.findByRole('link', { name: '4.2.0' });
		await user.click(within(rowOf('4.2.0')).getByRole('button', { name: 'Update' }));
		const dialog = await screen.findByRole('dialog', { name: 'Update to SPT 4.2.0?' });
		await user.click(within(dialog).getByRole('button', { name: 'Close' }));

		expect(screen.queryByRole('dialog')).toBeNull();
		expect(rpc.payloads('apply_spt_release')).toEqual([]);
	});

	it('reverts the last SPT change and names the version it went back to', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('revert_spt_update', {
			previous_version: '4.2.0',
			version: '4.1.0',
			commit: 'abcdef1',
			files: []
		});
		render(Page);

		await user.click(
			await screen.findByRole('button', { name: 'Undo the last SPT change' })
		);
		const dialog = await screen.findByRole('dialog', { name: 'Undo the last SPT change?' });
		await user.click(within(dialog).getByRole('button', { name: 'Continue' }));

		expect(rpc.payloads('revert_spt_update')).toEqual([{ path: GAME_DIR }]);
		expect(
			await screen.findByText(
				'SPT is now 4.1.0. Mods built for the other version show up under Updates.'
			)
		).toBeInTheDocument();
	});

	it('keeps the wording vague when the revert reports no version', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('revert_spt_update', {
			previous_version: '',
			version: '',
			commit: '',
			files: []
		});
		render(Page);

		await user.click(
			await screen.findByRole('button', { name: 'Undo the last SPT change' })
		);
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Undo the last SPT change?' })).getByRole(
				'button',
				{ name: 'Continue' }
			)
		);

		expect(
			await screen.findByText(
				'The SPT version changed. Mods built for the other version show up under Updates.'
			)
		).toBeInTheDocument();
	});

	it('reports a change that the backend refused', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.rejects('revert_spt_update', apiError('There is nothing to undo.', 'NO_SPT_CHANGE'));
		render(Page);

		await user.click(
			await screen.findByRole('button', { name: 'Undo the last SPT change' })
		);
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Undo the last SPT change?' })).getByRole(
				'button',
				{ name: 'Continue' }
			)
		);

		expect(await screen.findByText('There is nothing to undo.')).toBeInTheDocument();
	});
});

/**
 * The pre-flight check that runs before an SPT version change: the dialog has
 * to show which installed mods have no release for the target version, and the
 * change must not go through until the player acknowledges them.
 */
describe('the mod check before an SPT version change', () => {
	const releases = sptReleaseList({
		current_version: '4.1.0',
		client_build: '41000',
		latest: '4.3.0',
		releases: [
			release({ version: '4.3.0' }),
			release({ version: '4.2.0' }),
			release({ version: '4.1.0' }),
			release({ version: '4.0.0' })
		]
	});

	function rowOf(version: string): HTMLElement {
		const cell = screen.getByRole('link', { name: version });
		const row = cell.closest('tr');
		if (row === null) {
			throw new Error(`no release row for ${version}`);
		}
		return row;
	}

	function stubApply(version: string): void {
		rpc.returns('apply_spt_release', {
			previous_version: '4.1.0',
			version,
			commit: 'abcdef1',
			files: [],
			operation_id: 'op'
		});
	}

	/** Open the confirmation dialog for one release row and return the dialog. */
	async function openChange(
		user: ReturnType<typeof userEvent.setup>,
		version: string,
		action: string,
		title: string
	): Promise<HTMLElement> {
		await screen.findByRole('link', { name: version });
		await user.click(within(rowOf(version)).getByRole('button', { name: action }));
		return await screen.findByRole('dialog', { name: title });
	}

	function openUpdate(
		user: ReturnType<typeof userEvent.setup>,
		version = '4.2.0'
	): Promise<HTMLElement> {
		return openChange(user, version, 'Update', `Update to SPT ${version}?`);
	}

	it('asks the backend about the target version the player picked', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('check_spt_compatibility', report({ spt_version: '4.3.0' }));
		render(Page);

		await openUpdate(user, '4.3.0');

		expect(await screen.findByText('No installed mods to check against SPT 4.3.0.')).toBeInTheDocument();
		expect(rpc.payloads('check_spt_compatibility')).toEqual([
			{ path: GAME_DIR, version: '4.3.0' }
		]);
	});

	it('names every installed mod without a release before the player commits', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns(
			'check_spt_compatibility',
			report({ incompatible: ['SVM', 'Looting Bots'], compatible: ['SAIN'] })
		);
		render(Page);

		const dialog = await openUpdate(user);

		expect(
			await within(dialog).findByText('2 installed mods have no release for SPT 4.2.0:')
		).toBeInTheDocument();
		expect(within(dialog).getByText('SVM')).toBeInTheDocument();
		expect(within(dialog).getByText('Looting Bots')).toBeInTheDocument();
		expect(rpc.payloads('apply_spt_release')).toEqual([]);
	});

	it('holds Continue back while the verdict is still on its way and frees it on arrival', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		let deliver = (): void => {};
		const verdict = new Promise<SptCompatibilityReport>((resolve) => {
			deliver = () => resolve(report({ compatible: ['SAIN'] }));
		});
		rpc.handle('check_spt_compatibility', () => verdict);
		render(Page);

		const dialog = await openUpdate(user);
		await within(dialog).findByText('Checking your installed mods against this version…');
		expect(within(dialog).getByRole('button', { name: 'Continue' })).toBeDisabled();

		deliver();

		expect(
			await within(dialog).findByText('1 installed mod has a release for SPT 4.2.0.')
		).toBeInTheDocument();
		expect(within(dialog).getByRole('button', { name: 'Continue' })).toBeEnabled();
	});

	it('holds Continue back behind an unticked override while a mod has no release', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('check_spt_compatibility', report({ incompatible: ['SVM'] }));
		render(Page);

		const dialog = await openUpdate(user);
		await within(dialog).findByText('1 installed mod has no release for SPT 4.2.0:');

		const override = within(dialog).getByRole('checkbox', {
			name: 'Change the SPT version anyway'
		});
		expect(override).not.toBeChecked();
		expect(within(dialog).getByRole('button', { name: 'Continue' })).toBeDisabled();
	});

	it('sends the override to the backend once the player ticks it', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('check_spt_compatibility', report({ incompatible: ['SVM'] }));
		stubApply('4.2.0');
		render(Page);

		const dialog = await openUpdate(user);
		await within(dialog).findByText('1 installed mod has no release for SPT 4.2.0:');
		await user.click(
			within(dialog).getByRole('checkbox', { name: 'Change the SPT version anyway' })
		);

		const proceed = within(dialog).getByRole('button', { name: 'Continue' });
		expect(proceed).toBeEnabled();
		await user.click(proceed);

		expect(rpc.payloads('apply_spt_release')).toEqual([
			{
				path: GAME_DIR,
				version: '4.2.0',
				allow_incompatible_mods: true,
				operation_id: expect.any(String)
			}
		]);
		expect(
			await screen.findByText(
				'SPT is now 4.2.0. Mods built for the other version show up under Updates.'
			)
		).toBeInTheDocument();
	});

	it('sends no override for a clean verdict the player did not have to acknowledge', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('check_spt_compatibility', report({ compatible: ['SAIN'] }));
		stubApply('4.2.0');
		render(Page);

		const dialog = await openUpdate(user);
		await within(dialog).findByText('1 installed mod has a release for SPT 4.2.0.');
		await user.click(within(dialog).getByRole('button', { name: 'Continue' }));

		expect(rpc.payloads('apply_spt_release')).toEqual([
			{
				path: GAME_DIR,
				version: '4.2.0',
				allow_incompatible_mods: false,
				operation_id: expect.any(String)
			}
		]);
	});

	it('forgets the override when the dialog is closed and opened again', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('check_spt_compatibility', report({ incompatible: ['SVM'] }));
		render(Page);

		const first = await openUpdate(user);
		await within(first).findByText('1 installed mod has no release for SPT 4.2.0:');
		await user.click(
			within(first).getByRole('checkbox', { name: 'Change the SPT version anyway' })
		);
		await user.click(within(first).getByRole('button', { name: 'Close' }));

		const second = await openUpdate(user);
		await within(second).findByText('1 installed mod has no release for SPT 4.2.0:');
		expect(
			within(second).getByRole('checkbox', { name: 'Change the SPT version anyway' })
		).not.toBeChecked();
		expect(within(second).getByRole('button', { name: 'Continue' })).toBeDisabled();
	});

	it('forgets the override when the player picks another release', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.handle('check_spt_compatibility', (payload) => {
			const version = (payload as { version: string }).version;
			return report({ spt_version: version, incompatible: [`no-release-for-${version}`] });
		});
		render(Page);

		const first = await openUpdate(user, '4.3.0');
		await within(first).findByText('1 installed mod has no release for SPT 4.3.0:');
		await user.click(
			within(first).getByRole('checkbox', { name: 'Change the SPT version anyway' })
		);
		await user.click(within(first).getByRole('button', { name: 'Close' }));

		const second = await openUpdate(user, '4.2.0');
		await within(second).findByText('1 installed mod has no release for SPT 4.2.0:');
		expect(within(second).getByText('no-release-for-4.2.0')).toBeInTheDocument();
		expect(
			within(second).getByRole('checkbox', { name: 'Change the SPT version anyway' })
		).not.toBeChecked();
		expect(within(second).getByRole('button', { name: 'Continue' })).toBeDisabled();
	});

	it('reports mods it could not judge without holding the change back', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns(
			'check_spt_compatibility',
			report({ compatible: ['SAIN'], skipped: ['Fika', 'Waypoints'] })
		);
		stubApply('4.2.0');
		render(Page);

		const dialog = await openUpdate(user);

		expect(
			await within(dialog).findByText(
				'2 installed mods could not be checked: Fika, Waypoints.'
			)
		).toBeInTheDocument();
		expect(
			within(dialog).queryByRole('checkbox', { name: 'Change the SPT version anyway' })
		).toBeNull();

		await user.click(within(dialog).getByRole('button', { name: 'Continue' }));

		expect(rpc.payloads('apply_spt_release')).toEqual([
			{
				path: GAME_DIR,
				version: '4.2.0',
				allow_incompatible_mods: false,
				operation_id: expect.any(String)
			}
		]);
	});

	it('repeats why the mods could not be checked and lets the change proceed', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns(
			'check_spt_compatibility',
			report({ checked: false, notice: 'The mod list has never been downloaded.' })
		);
		stubApply('4.2.0');
		render(Page);

		const dialog = await openUpdate(user);

		expect(
			await within(dialog).findByText(
				'The mod list has never been downloaded. The change can still go ahead.'
			)
		).toBeInTheDocument();

		await user.click(within(dialog).getByRole('button', { name: 'Continue' }));

		expect(rpc.payloads('apply_spt_release')).toEqual([
			{
				path: GAME_DIR,
				version: '4.2.0',
				allow_incompatible_mods: false,
				operation_id: expect.any(String)
			}
		]);
	});

	it('tells the player the check itself failed and still lets the change proceed', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.rejects(
			'check_spt_compatibility',
			apiError('The mod list could not be read.', 'CATALOG_UNREADABLE')
		);
		stubApply('4.2.0');
		render(Page);

		const dialog = await openUpdate(user);

		expect(
			await within(dialog).findByText(
				'Your installed mods could not be checked against this version. ' +
					'The change can still go ahead.'
			)
		).toBeInTheDocument();

		await user.click(within(dialog).getByRole('button', { name: 'Continue' }));

		expect(rpc.payloads('apply_spt_release')).toEqual([
			{
				path: GAME_DIR,
				version: '4.2.0',
				allow_incompatible_mods: false,
				operation_id: expect.any(String)
			}
		]);
	});

	it('checks the mods for a downgrade the same way as for an update', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns(
			'check_spt_compatibility',
			report({ spt_version: '4.0.0', incompatible: ['SVM'] })
		);
		render(Page);

		const dialog = await openChange(
			user,
			'4.0.0',
			'Go back to this version',
			'Go back to SPT 4.0.0?'
		);

		expect(
			await within(dialog).findByText('1 installed mod has no release for SPT 4.0.0:')
		).toBeInTheDocument();
		expect(within(dialog).getByRole('button', { name: 'Continue' })).toBeDisabled();
	});

	it('does not check any mods for an undo, which has no target version', async () => {
		const user = userEvent.setup();
		stubMount({ releases });
		rpc.returns('revert_spt_update', {
			previous_version: '4.2.0',
			version: '4.1.0',
			commit: 'abcdef1',
			files: []
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Undo the last SPT change' }));
		const dialog = await screen.findByRole('dialog', { name: 'Undo the last SPT change?' });
		await user.click(within(dialog).getByRole('button', { name: 'Continue' }));

		expect(
			await screen.findByText(
				'SPT is now 4.1.0. Mods built for the other version show up under Updates.'
			)
		).toBeInTheDocument();
		expect(rpc.names()).not.toContain('check_spt_compatibility');
	});
});

describe('the app updater', () => {
	it('says the app is current when the updater found nothing', async () => {
		const user = userEvent.setup();
		stubMount();
		stubCheck(null);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Check for updates' }));

		expect(await screen.findByText('This is the newest version.')).toBeInTheDocument();
	});

	it('announces the found version together with its notes', async () => {
		const user = userEvent.setup();
		stubMount();
		stubCheck(fakeUpdate());
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Check for updates' }));

		expect(await screen.findByText('Version 9.9.9 is available.')).toBeInTheDocument();
		expect(screen.getByText('Fixes the launcher.')).toBeInTheDocument();
	});

	it('goes from download over install to the restart offer', async () => {
		const user = userEvent.setup();
		stubMount();
		let emit: (event: DownloadEvent) => void = () => undefined;
		let finish: () => void = () => undefined;
		stubCheck(
			fakeUpdate({
				downloadAndInstall: (onEvent) => {
					emit = onEvent;
					return new Promise<void>((resolve) => {
						finish = resolve;
					});
				}
			})
		);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Check for updates' }));
		await user.click(await screen.findByRole('button', { name: 'Install' }));

		emit({ event: 'Started', data: { contentLength: 200 } });
		expect(await screen.findByText('Downloading version 9.9.9…')).toBeInTheDocument();

		emit({ event: 'Progress', data: { chunkLength: 50 } });
		expect(await screen.findByText('25%')).toBeInTheDocument();

		emit({ event: 'Finished' });
		expect(await screen.findByText('Installing version 9.9.9…')).toBeInTheDocument();

		finish();
		expect(
			await screen.findByText('Version 9.9.9 is installed. Restart sptmod to use it.')
		).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Restart now' }));
		expect(vi.mocked(relaunch)).toHaveBeenCalledTimes(1);
	});

	it('turns an unsigned download into a wording the player can act on', async () => {
		const user = userEvent.setup();
		stubMount();
		stubCheck(
			fakeUpdate({
				downloadAndInstall: () => Promise.reject(new Error('invalid updater binary format'))
			})
		);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Check for updates' }));
		await user.click(await screen.findByRole('button', { name: 'Install' }));

		expect(
			await screen.findByText(
				'Automatic updates are only available in the AppImage build — download the new version from the releases page.'
			)
		).toBeInTheDocument();
	});

	it('reports a restart that the process plugin refused', async () => {
		const user = userEvent.setup();
		stubMount();
		stubCheck(fakeUpdate());
		vi.mocked(relaunch).mockRejectedValue(new Error('The app cannot restart itself.'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Check for updates' }));
		await user.click(await screen.findByRole('button', { name: 'Install' }));
		await user.click(await screen.findByRole('button', { name: 'Restart now' }));

		expect(await screen.findByText('The app cannot restart itself.')).toBeInTheDocument();
	});

	it('explains a check that could not reach the release feed', async () => {
		const user = userEvent.setup();
		stubMount();
		vi.mocked(check).mockRejectedValue(
			new Error('Could not fetch a valid release JSON from the remote')
		);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Check for updates' }));

		expect(
			await screen.findByText(
				'The update server could not be reached, so there is no release information available.'
			)
		).toBeInTheDocument();
	});
});

describe('exporting the mod list', () => {
	it('asks the backend for the chosen format and shows what came back', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('export_mods', { content: 'Alpha 1.0.0' });
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Export' }));

		expect(rpc.payloads('export_mods')).toEqual([
			{ path: GAME_DIR, format: 'text', client_only: false }
		]);
		expect(await screen.findByLabelText('Exported mod list')).toHaveValue('Alpha 1.0.0');
	});

	it('asks for the client mods only when that box is ticked', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('export_mods', { content: '[]' });
		render(Page);

		await screen.findByRole('heading', { name: 'Export the mod list' });
		await user.click(screen.getByLabelText('Only mods with a client part (Fika)'));
		await user.click(screen.getByRole('button', { name: 'Export' }));

		expect(rpc.payloads('export_mods')).toEqual([
			{ path: GAME_DIR, format: 'text', client_only: true }
		]);
	});

	it('puts the exported list into the clipboard', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('export_mods', { content: 'Alpha 1.0.0' });
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Export' }));
		await user.click(await screen.findByRole('button', { name: 'Copy' }));

		expect(vi.mocked(writeText).mock.calls).toEqual([['Alpha 1.0.0']]);
		expect(await screen.findByText('Copied to the clipboard.')).toBeInTheDocument();
	});

	it('writes the exported list to the file the save dialog returned', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('export_mods', { content: 'Alpha 1.0.0' });
		vi.mocked(save).mockResolvedValue('/out/mods.txt');
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Export' }));
		await user.click(await screen.findByRole('button', { name: 'Save as text' }));

		expect(vi.mocked(save).mock.calls).toEqual([[{ defaultPath: 'mods.txt' }]]);
		expect(vi.mocked(writeTextFile).mock.calls).toEqual([['/out/mods.txt', 'Alpha 1.0.0']]);
		expect(await screen.findByText('Saved to /out/mods.txt.')).toBeInTheDocument();
	});

	it('writes no file when the save dialog was cancelled', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('export_mods', { content: 'Alpha 1.0.0' });
		vi.mocked(save).mockResolvedValue(null);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Export' }));
		await user.click(await screen.findByRole('button', { name: 'Save as text' }));

		expect(vi.mocked(writeTextFile)).not.toHaveBeenCalled();
	});

	it('shows the failure message when the export is refused', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('export_mods', apiError('No mods are installed.', 'NO_MODS'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Export' }));

		expect(await screen.findByText('No mods are installed.')).toBeInTheDocument();
	});
});

describe('importing a mod list', () => {
	const diff: ImportDiff = {
		missing: [
			{ name: 'Alpha', wanted_version: '1.2.0', installed_version: '', link: 'https://x/1' }
		],
		different: [
			{ name: 'Beta', wanted_version: '2.0.0', installed_version: '1.0.0', link: '' }
		],
		extra: [],
		installed: []
	};

	it('compares the pasted list and groups what it found', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('import_list', diff);
		render(Page);

		await user.type(await screen.findByLabelText('Mod list content'), 'Alpha 1.2.0');
		await user.click(screen.getByRole('button', { name: 'Compare' }));

		expect(rpc.payloads('import_list')).toEqual([
			{ path: GAME_DIR, list_file: null, content: 'Alpha 1.2.0', apply: false }
		]);
		expect(await screen.findByRole('heading', { name: 'Missing (1)' })).toBeInTheDocument();
		expect(screen.getByRole('heading', { name: 'Different version (1)' })).toBeInTheDocument();
		expect(screen.getByRole('heading', { name: 'Extra (0)' })).toBeInTheDocument();
		expect(screen.getByText('Nothing.')).toBeInTheDocument();
	});

	it('sends the file path when nothing was pasted', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('import_list', diff);
		vi.mocked(open).mockResolvedValue('/lists/mods.txt');
		render(Page);

		await screen.findByRole('heading', { name: 'Import a mod list' });
		const section = screen.getByRole('heading', { name: 'Import a mod list' }).closest('section');
		if (section === null) {
			throw new Error('the import section is not rendered');
		}
		await user.click(within(section).getByRole('button', { name: 'Choose file…' }));
		await vi.waitFor(() => {
			expect(screen.getByLabelText('Mod list file')).toHaveValue('/lists/mods.txt');
		});
		await user.click(within(section).getByRole('button', { name: 'Compare' }));

		expect(rpc.payloads('import_list')).toEqual([
			{ path: GAME_DIR, list_file: '/lists/mods.txt', content: null, apply: false }
		]);
	});

	it('installs the missing mods only after a comparison', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('import_list', diff);
		render(Page);

		await screen.findByRole('heading', { name: 'Import a mod list' });
		expect(screen.getByRole('button', { name: 'Install missing mods' })).toBeDisabled();

		await user.type(screen.getByLabelText('Mod list content'), 'Alpha 1.2.0');
		await user.click(screen.getByRole('button', { name: 'Compare' }));
		await screen.findByRole('heading', { name: 'Missing (1)' });
		await user.click(screen.getByRole('button', { name: 'Install missing mods' }));

		expect(rpc.payloads('import_list').at(-1)).toEqual({
			path: GAME_DIR,
			list_file: null,
			content: 'Alpha 1.2.0',
			apply: true
		});
		expect(await screen.findByText('The missing mods were installed.')).toBeInTheDocument();
	});

	it('shows the failure message of a list that cannot be read', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('import_list', apiError('The list is not valid JSON.', 'BAD_LIST'));
		render(Page);

		await user.type(await screen.findByLabelText('Mod list content'), 'not-a-list');
		await user.click(screen.getByRole('button', { name: 'Compare' }));

		expect(await screen.findByText('The list is not valid JSON.')).toBeInTheDocument();
	});
});

describe('the catalog cache', () => {
	it('removes the cache database and says where it was', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('clear_catalog_cache', {
			db_path: '/cfg/forge-catalog.db',
			removed: true,
			downloads_path: null,
			downloads_freed_bytes: 0
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Clear the catalog cache' }));

		expect(rpc.payloads('clear_catalog_cache')).toEqual([{ downloads: false }]);
		expect(
			await screen.findByText(
				'Removed /cfg/forge-catalog.db. Run a catalog sync to rebuild it.'
			)
		).toBeInTheDocument();
	});

	it('says so when there was no cache to remove', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('clear_catalog_cache', {
			db_path: '/cfg/forge-catalog.db',
			removed: false,
			downloads_path: null,
			downloads_freed_bytes: 0
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Clear the catalog cache' }));

		expect(
			await screen.findByText(
				'Nothing to remove; /cfg/forge-catalog.db does not exist.'
			)
		).toBeInTheDocument();
	});

	it('reports the freed space when the kept mod files go too', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('clear_catalog_cache', {
			db_path: '/cfg/forge-catalog.db',
			removed: true,
			downloads_path: '/cfg/downloads',
			downloads_freed_bytes: 12_300_000
		});
		render(Page);

		await user.click(
			await screen.findByRole('button', {
				name: 'Clear the cache and the downloaded mod files'
			})
		);

		expect(rpc.payloads('clear_catalog_cache')).toEqual([{ downloads: true }]);
		expect(
			await screen.findByText(
				'Removed /cfg/forge-catalog.db. Run a catalog sync to rebuild it. Freed 12.3 MB of kept mod files.'
			)
		).toBeInTheDocument();
	});

	it('shows the failure message when the cache cannot be removed', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('clear_catalog_cache', apiError('The file is in use.', 'LOCKED'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Clear the catalog cache' }));

		expect(await screen.findByText('The file is in use.')).toBeInTheDocument();
	});
});

describe('the mod sets section', () => {
	function stubSets(): void {
		stubMount({ sets: [modSet({ name: 'Raid night', entries: [] })] });
	}

	/** `Export` is also the label of the mod-list button, so the section scopes it. */
	async function modSetsSection(): Promise<HTMLElement> {
		const heading = await screen.findByRole('heading', { name: 'Mod sets' });
		const section = heading.closest('section');
		if (section === null) {
			throw new Error('the mod sets section is not rendered');
		}
		return section;
	}

	it('says so when no set was saved yet', async () => {
		stubMount();
		render(Page);

		expect(await screen.findByText('No sets yet.')).toBeInTheDocument();
	});

	it('renames a set under the name the player typed', async () => {
		const user = userEvent.setup();
		stubSets();
		rpc.returns('rename_mod_set', { name: 'Weekend', created_at: '2026-01-01', entries: [] });
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Rename' }));
		const dialog = await screen.findByRole('dialog', { name: 'Rename the set' });
		await user.clear(within(dialog).getByLabelText('Name'));
		await user.type(within(dialog).getByLabelText('Name'), 'Weekend');
		await user.click(within(dialog).getByRole('button', { name: 'Save' }));

		expect(rpc.payloads('rename_mod_set')).toEqual([
			{ path: GAME_DIR, name: 'Raid night', new_name: 'Weekend' }
		]);
		expect(await screen.findByText('The set is now called "Weekend".')).toBeInTheDocument();
	});

	it('deletes a set and confirms it by name', async () => {
		const user = userEvent.setup();
		stubSets();
		rpc.returns('delete_mod_set', { sets: [], bindings: [] });
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Delete' }));

		expect(rpc.payloads('delete_mod_set')).toEqual([{ path: GAME_DIR, name: 'Raid night' }]);
		expect(await screen.findByText('"Raid night" was deleted.')).toBeInTheDocument();
	});

	it('writes an exported set to the file the save dialog returned', async () => {
		const user = userEvent.setup();
		stubSets();
		rpc.returns('export_mod_set', {
			name: 'Raid night',
			content: '{"name":"Raid night"}',
			code: 'sptmod:abc'
		});
		vi.mocked(save).mockResolvedValue('/out/Raid night.json');
		render(Page);

		await user.click(within(await modSetsSection()).getByRole('button', { name: 'Export' }));

		expect(vi.mocked(save).mock.calls).toEqual([[{ defaultPath: 'Raid night.json' }]]);
		expect(vi.mocked(writeTextFile).mock.calls).toEqual([
			['/out/Raid night.json', '{"name":"Raid night"}']
		]);
		expect(await screen.findByText('Saved to /out/Raid night.json.')).toBeInTheDocument();
	});

	it('puts the share code of a set into the clipboard', async () => {
		const user = userEvent.setup();
		stubSets();
		rpc.returns('export_mod_set', {
			name: 'Raid night',
			content: '{}',
			code: 'sptmod:abc'
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Copy share code' }));

		expect(vi.mocked(writeText).mock.calls).toEqual([['sptmod:abc']]);
		expect(
			await screen.findByText('The share code for "Raid night" is in the clipboard.')
		).toBeInTheDocument();
	});

	it('adds a set from a pasted share code', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('import_mod_set', { name: 'Shared', created_at: '2026-01-01', entries: [] });
		render(Page);

		await user.type(await screen.findByLabelText('Mod set share code'), 'sptmod:abc');
		await user.click(screen.getByRole('button', { name: 'Add the set' }));

		expect(rpc.payloads('import_mod_set')).toEqual([
			{ path: GAME_DIR, set_file: null, content: 'sptmod:abc', name: null }
		]);
		expect(await screen.findByText('"Shared" was added.')).toBeInTheDocument();
	});

	it('asks for another name when the set name is taken', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('import_mod_set', apiError('That set exists.', 'MOD_SET_EXISTS'));
		render(Page);

		await user.type(await screen.findByLabelText('Mod set share code'), 'sptmod:abc');
		await user.click(screen.getByRole('button', { name: 'Add the set' }));

		const dialog = await screen.findByRole('dialog', { name: 'Choose another name' });
		rpc.returns('import_mod_set', { name: 'Shared 2', created_at: '2026-01-01', entries: [] });
		await user.type(within(dialog).getByLabelText('Name'), 'Shared 2');
		await user.click(within(dialog).getByRole('button', { name: 'Save' }));

		expect(rpc.payloads('import_mod_set').at(-1)).toEqual({
			path: GAME_DIR,
			set_file: null,
			content: 'sptmod:abc',
			name: 'Shared 2'
		});
	});

	it('shows the failure message when the mod sets cannot be read', async () => {
		stubMount();
		rpc.rejects('list_mod_sets', apiError('The sets file is broken.', 'BAD_SETS'));
		render(Page);

		expect(await screen.findByText('The sets file is broken.')).toBeInTheDocument();
	});
});

describe('the Fika section', () => {
	it('refuses to save a client without the address of the host', async () => {
		const user = userEvent.setup();
		stubMount();
		render(Page);

		await user.click(await screen.findByRole('radio', { name: 'I join a host' }));

		expect(
			await screen.findByText('A Fika client needs the address of the host.')
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
	});

	it('names an address that cannot be used', async () => {
		const user = userEvent.setup();
		stubMount();
		render(Page);

		await user.click(await screen.findByRole('radio', { name: 'I join a host' }));
		await user.type(screen.getByLabelText('Host address'), 'ftp://pc.local');

		expect(
			await screen.findByText(
				'This address cannot be used. Enter something like 192.168.1.20 or 192.168.1.20:7000.'
			)
		).toBeInTheDocument();
	});

	it('saves the role and the address and reports where the game connects to', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('set_fika_settings', {
			role: 'client',
			host_address: '192.168.1.20',
			base_url: 'https://192.168.1.20:7000'
		});
		render(Page);

		await user.click(await screen.findByRole('radio', { name: 'I join a host' }));
		await user.type(screen.getByLabelText('Host address'), '192.168.1.20:7000');
		await user.click(screen.getByRole('button', { name: 'Save' }));

		expect(rpc.payloads('set_fika_settings')).toEqual([
			{ path: GAME_DIR, role: 'client', host_address: '192.168.1.20:7000' }
		]);
		expect(
			await screen.findByText('Saved. The game connects to 192.168.1.20:7000.')
		).toBeInTheDocument();
	});

	it('drops the address again when the machine plays alone', async () => {
		const user = userEvent.setup();
		stubMount({
			fika: { role: 'client', host_address: '192.168.1.20', base_url: 'https://192.168.1.20:6969' }
		});
		rpc.returns('set_fika_settings', { role: 'none', host_address: '', base_url: '' });
		render(Page);

		await user.click(await screen.findByRole('radio', { name: 'No Fika' }));
		await user.click(screen.getByRole('button', { name: 'Save' }));

		expect(rpc.payloads('set_fika_settings')).toEqual([
			{ path: GAME_DIR, role: 'none', host_address: '' }
		]);
		expect(await screen.findByText('Saved.')).toBeInTheDocument();
	});

	it('lists what a Fika install would download before it starts', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('plan_fika_install', {
			server_mod_id: 47,
			client_mod_id: 48,
			plan: {
				spt_version: '4.1.0',
				entries: [
					{
						mod_id: 47,
						name: 'Fika Server',
						guid: null,
						version: '2.0.0',
						version_id: 470,
						link: null,
						content_length: 2_097_152,
						is_dependency: false
					}
				]
			}
		});
		rpc.returns('install_mod', { plan: { spt_version: '4.1.0', entries: [] }, installed: [], operation_id: 'op' });
		render(Page);

		await user.click(
			await screen.findByRole('button', { name: 'Install Fika (server mod + client plugin)' })
		);

		const dialog = await screen.findByRole('dialog', { name: 'Install Fika' });
		expect(within(dialog).getByText('Fika Server 2.0.0')).toBeInTheDocument();
		expect(within(dialog).getByText('2.0 MB')).toBeInTheDocument();

		await user.click(within(dialog).getByRole('button', { name: 'Install' }));

		const installed = rpc.payloads('install_mod') as { mod_id_or_name: number }[];
		expect(installed.map((entry) => entry.mod_id_or_name)).toEqual([47, 48]);
		expect(
			await screen.findByText('Fika is installed: the server mod and the client plugin.')
		).toBeInTheDocument();
	});
});

describe('the launching section', () => {
	it('remembers that the server should stay up when the game closes', async () => {
		const user = userEvent.setup();
		stubMount();
		render(Page);

		const box = await screen.findByLabelText('Stop the server when the game closes');
		expect(box).toBeChecked();

		await user.click(box);

		expect(localStorage.getItem('sptmod.stop_server_on_exit')).toBe('false');
	});
});

describe("applying the host's mod set", () => {
	const shared: ModSet = {
		name: 'Host night',
		created_at: '2026-01-01T00:00:00Z',
		entries: [{ forge_id: '47', name: 'Alpha', version: '1.0.0', enabled: true }]
	};

	it('imports the pasted set and applies it right away', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('import_mod_set', shared);
		rpc.returns('apply_mod_set', {
			name: 'Host night',
			enabled: ['Alpha'],
			disabled: ['Beta'],
			missing: [],
			notices: [],
			operation_id: 'op'
		});
		render(Page);

		await user.type(await screen.findByLabelText('Host mod set share code'), 'sptmod:host');
		await user.click(screen.getByRole('button', { name: "Use the host's mods" }));

		expect(rpc.payloads('import_mod_set')).toEqual([
			{ path: GAME_DIR, set_file: null, content: 'sptmod:host', name: null }
		]);
		expect(rpc.payloads('apply_mod_set')).toEqual([
			{ path: GAME_DIR, name: 'Host night', ignore_missing: false, operation_id: null }
		]);
		expect(
			await screen.findByText('"Host night": 1 on, 1 off, 0 missing.')
		).toBeInTheDocument();
	});

	it('takes the host set from the file the picker returned', async () => {
		const user = userEvent.setup();
		stubMount();
		vi.mocked(open).mockResolvedValue('/shared/host.json');
		rpc.returns('import_mod_set', shared);
		rpc.returns('apply_mod_set', {
			name: 'Host night',
			enabled: [],
			disabled: [],
			missing: [],
			notices: [],
			operation_id: 'op'
		});
		render(Page);

		const heading = await screen.findByRole('heading', { name: "Apply the host's mod set" });
		const section = heading.parentElement;
		if (section === null) {
			throw new Error('the Fika section is not rendered');
		}
		await user.click(within(section).getAllByRole('button', { name: 'Choose file…' })[0]);
		await vi.waitFor(() => {
			expect(screen.getByLabelText('Host mod set file')).toHaveValue('/shared/host.json');
		});
		await user.click(screen.getByRole('button', { name: "Use the host's mods" }));

		expect(rpc.payloads('import_mod_set')).toEqual([
			{ path: GAME_DIR, set_file: '/shared/host.json', content: null, name: null }
		]);
	});

	it('offers to install the mods the host set is missing', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('import_mod_set', shared);
		rpc.rejects(
			'apply_mod_set',
			apiError('The set is incomplete.', 'MOD_SET_INCOMPLETE', { missing: ['Alpha'] })
		);
		render(Page);

		await user.type(await screen.findByLabelText('Host mod set share code'), 'sptmod:host');
		await user.click(screen.getByRole('button', { name: "Use the host's mods" }));

		const dialog = await screen.findByRole('dialog', { name: 'Mods are missing' });
		expect(within(dialog).getByText('Alpha')).toBeInTheDocument();

		rpc.returns('install_mod', {
			plan: { spt_version: '4.1.0', entries: [] },
			installed: [],
			operation_id: 'op'
		});
		rpc.returns('apply_mod_set', {
			name: 'Host night',
			enabled: ['Alpha'],
			disabled: [],
			missing: [],
			notices: [],
			operation_id: 'op'
		});
		await user.click(within(dialog).getByRole('button', { name: 'Install the missing mods' }));

		const installed = rpc.payloads('install_mod') as { mod_id_or_name: number }[];
		expect(installed.map((entry) => entry.mod_id_or_name)).toEqual([47]);
		expect(
			await screen.findByText('"Host night": 1 on, 0 off, 0 missing.')
		).toBeInTheDocument();
	});

	it('applies the host set without the missing mods on request', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('import_mod_set', shared);
		rpc.rejects(
			'apply_mod_set',
			apiError('The set is incomplete.', 'MOD_SET_INCOMPLETE', { missing: ['Alpha'] })
		);
		render(Page);

		await user.type(await screen.findByLabelText('Host mod set share code'), 'sptmod:host');
		await user.click(screen.getByRole('button', { name: "Use the host's mods" }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Mods are missing' })).getByRole('button', {
				name: 'Play without them'
			})
		);

		const applied = rpc.payloads('apply_mod_set') as { ignore_missing: boolean }[];
		expect(applied.map((entry) => entry.ignore_missing)).toEqual([false, true]);
	});

	it('names the mods that could not be installed for the host set', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('import_mod_set', shared);
		rpc.rejects(
			'apply_mod_set',
			apiError('The set is incomplete.', 'MOD_SET_INCOMPLETE', { missing: ['Alpha'] })
		);
		rpc.rejects('install_mod', apiError('The download failed.', 'DOWNLOAD_FAILED'));
		render(Page);

		await user.type(await screen.findByLabelText('Host mod set share code'), 'sptmod:host');
		await user.click(screen.getByRole('button', { name: "Use the host's mods" }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Mods are missing' })).getByRole('button', {
				name: 'Install the missing mods'
			})
		);

		expect(
			await screen.findByText('Some mods could not be installed. Alpha: The download failed.')
		).toBeInTheDocument();
	});

	it('asks for another name when the host set collides with an existing one', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('import_mod_set', apiError('That set exists.', 'MOD_SET_EXISTS'));
		render(Page);

		await user.type(await screen.findByLabelText('Host mod set share code'), 'sptmod:host');
		await user.click(screen.getByRole('button', { name: "Use the host's mods" }));

		const dialog = await screen.findByRole('dialog', { name: 'Choose another name' });
		rpc.returns('import_mod_set', { ...shared, name: 'Host night 2' });
		rpc.returns('apply_mod_set', {
			name: 'Host night 2',
			enabled: [],
			disabled: [],
			missing: [],
			notices: [],
			operation_id: 'op'
		});
		await user.type(within(dialog).getByLabelText('Name'), 'Host night 2');
		await user.click(within(dialog).getByRole('button', { name: 'Save' }));

		expect(rpc.payloads('import_mod_set').at(-1)).toEqual({
			path: GAME_DIR,
			set_file: null,
			content: 'sptmod:host',
			name: 'Host night 2'
		});
	});

	it('reports a host set that cannot be read', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('import_mod_set', apiError('The share code is broken.', 'BAD_CODE'));
		render(Page);

		await user.type(await screen.findByLabelText('Host mod set share code'), 'sptmod:host');
		await user.click(screen.getByRole('button', { name: "Use the host's mods" }));

		expect(await screen.findByText('The share code is broken.')).toBeInTheDocument();
	});

	it('reports a Fika install plan the backend refused', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('plan_fika_install', apiError('Fika has no build for this SPT.', 'NO_VERSION'));
		render(Page);

		await user.click(
			await screen.findByRole('button', { name: 'Install Fika (server mod + client plugin)' })
		);

		expect(await screen.findByText('Fika has no build for this SPT.')).toBeInTheDocument();
	});
});
