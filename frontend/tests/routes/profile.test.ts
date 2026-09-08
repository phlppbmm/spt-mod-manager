/**
 * Component tests for the profiles page.
 *
 * Setup: the page is mounted with a stored game directory and canned answers
 * for the six RPCs it issues on mount. Assertions read the rendered document
 * and the payloads the real `$lib/api/client` produced, so both the wording the
 * page shows and the request it sent are pinned. Reason: the page decides which
 * launch action, which note and which dialog a player sees, and none of those
 * decisions are covered by the unit tests of the helper modules.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
	CheckResult,
	CleanupPlan,
	CleanupResult,
	FikaSettings,
	HostProfile,
	LaunchProfileResponse,
	LaunchStatus,
	ModInfo,
	ModSetOverview,
	ProfileResidue,
	ProfileSummary,
	SnapshotResult,
	StopResult
} from '$lib/api/client';
import { clearFilter } from '$lib/filterstate';
import { takeIntents } from '$lib/intents';
import { setSubNav } from '$lib/shellstate';
import { invalidate } from '$lib/viewcache';

import Page from '../../src/routes/profile/+page.svelte';
import { apiError } from '../support/errors';
import { launchStatus, modInfo, modSet, profileSummary } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

const GAME_DIR = '/games/SPT';

interface MountStubs {
	profiles?: ProfileSummary[];
	check?: CheckResult;
	mods?: ModInfo[];
	sets?: ModSetOverview;
	fika?: FikaSettings;
	status?: LaunchStatus;
}

/** Canned answers for every RPC the page issues while mounting. */
function stubMount(stubs: MountStubs = {}): void {
	rpc.returns(
		'list_profiles',
		stubs.profiles ?? [profileSummary({ profile_id: 'aaa', file: 'aaa.json', nickname: 'Nikita' })]
	);
	rpc.returns('check_profiles', stubs.check ?? { residues: {}, warnings: [] });
	rpc.returns('list_mods', stubs.mods ?? []);
	rpc.returns('list_mod_sets', stubs.sets ?? { sets: [], bindings: [] });
	rpc.returns('get_fika_settings', stubs.fika ?? { role: 'none', host_address: '', base_url: '' });
	rpc.returns('launch_status', stubs.status ?? launchStatus());
}

function residue(overrides: Partial<ProfileResidue> = {}): ProfileResidue {
	return {
		profile: 'aaa',
		category: 'item',
		ids: ['5449016a4bdc2d6f028b456f'],
		count: 2,
		character: 'pmc',
		path: '',
		...overrides
	};
}

function stopResult(message: string): StopResult {
	return { server_stopped: true, client_stopped: true, message };
}

function launchResponse(nickname: string): LaunchProfileResponse {
	return {
		profile_id: 'aaa',
		nickname,
		server_pid: 4711,
		client_pid: 4712,
		server_started: true,
		operation_id: 'op'
	};
}

function snapshotResult(overrides: Partial<SnapshotResult> = {}): SnapshotResult {
	return {
		commit: 'abcdef1234567890abcdef1234567890abcdef12',
		message: 'Before the raid',
		files: ['profiles/aaa.json'],
		game_files: [],
		mod_settings: [],
		...overrides
	};
}

function hostProfile(overrides: Partial<HostProfile> = {}): HostProfile {
	return {
		profile_id: 'hhh',
		username: 'host-user',
		nickname: 'Guest',
		level: 7,
		side: 'Bear',
		has_password: false,
		...overrides
	};
}

beforeEach(() => {
	localStorage.setItem('sptmod.game_dir', GAME_DIR);
});

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setSubNav([], null);
	takeIntents(['snapshot', 'launch', 'sync', 'update-all', 'change-folder', 'reveal-mod']);
});

describe('closing a running game', () => {
	const running = launchStatus({
		client_running: true,
		profile_id: 'aaa',
		server_stoppable: true
	});

	it('shows the wording the backend returned for the stop, not a wording of its own', async () => {
		const user = userEvent.setup();
		stubMount({ status: running });
		rpc.returns('stop_game', stopResult('Backend worded this itself.'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Stop' }));
		await user.click(await screen.findByRole('button', { name: 'Close the game' }));

		expect(await screen.findByText('Backend worded this itself.')).toBeInTheDocument();
		expect(rpc.payloads('stop_game')).toEqual([{ path: GAME_DIR }]);
	});

	it('reports the failure message when the stop is refused', async () => {
		const user = userEvent.setup();
		stubMount({ status: running });
		rpc.rejects('stop_game', apiError('The game does not answer.', 'STOP_FAILED'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Stop' }));
		await user.click(await screen.findByRole('button', { name: 'Close the game' }));

		expect(await screen.findByText('The game does not answer.')).toBeInTheDocument();
	});

	it('promises that the SPT server goes down with the game when it is stoppable', async () => {
		const user = userEvent.setup();
		stubMount({ status: running });
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Stop' }));

		expect(await screen.findByText('The SPT server is closed with it.')).toBeInTheDocument();
	});

	it('makes no promise about the server when the server is not stoppable', async () => {
		const user = userEvent.setup();
		stubMount({
			status: launchStatus({ client_running: true, profile_id: 'aaa', server_stoppable: false })
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Stop' }));

		expect(await screen.findByRole('dialog', { name: 'Close the game' })).toBeInTheDocument();
		expect(screen.queryByText('The SPT server is closed with it.')).toBeNull();
	});
});

describe('snapshots', () => {
	it('reports the message, the file count and the short commit the backend returned', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns(
			'snapshot',
			snapshotResult({
				message: 'Before the raid',
				files: ['profiles/aaa.json', 'user/mods/Alpha/config.json'],
				commit: 'abcdef1234567890abcdef1234567890abcdef12'
			})
		);
		render(Page);

		await user.type(await screen.findByLabelText('Snapshot message'), 'Before the raid');
		await user.click(screen.getByRole('button', { name: 'Snapshot' }));

		expect(
			await screen.findByText('Snapshot "Before the raid" (2 files) as abcdef12.')
		).toBeInTheDocument();
		expect(rpc.payloads('snapshot')).toEqual([
			{ path: GAME_DIR, message: 'Before the raid' }
		]);
	});

	it('sends no message when the input was left empty', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('snapshot', snapshotResult());
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Snapshot' }));

		expect(rpc.payloads('snapshot')).toEqual([{ path: GAME_DIR, message: null }]);
	});

	it('shows the failure message instead of a snapshot note when the snapshot fails', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('snapshot', apiError('Nothing to save.', 'NOTHING_TO_COMMIT'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Snapshot' }));

		expect(await screen.findByText('Nothing to save.')).toBeInTheDocument();
	});
});

describe('launching a profile whose mod set is incomplete', () => {
	const bound: ModSetOverview = {
		sets: [
			modSet({
				name: 'Loadout',
				entries: [{ forge_id: '47', name: 'Alpha Mod', version: '1.0.0', enabled: true }]
			})
		],
		bindings: [{ profile_id: 'aaa', set_name: 'Loadout' }]
	};

	function stubIncompleteLaunch(): void {
		stubMount({ sets: bound });
		rpc.returns('plan_mod_set', {
			name: 'Loadout',
			enable: [],
			disable: [],
			missing: [],
			different: [],
			unchanged: []
		});
		rpc.rejects(
			'launch_profile',
			apiError('The set is incomplete.', 'MOD_SET_INCOMPLETE', { missing: ['Alpha Mod'] })
		);
	}

	it('offers the mods the backend named as missing', async () => {
		const user = userEvent.setup();
		stubIncompleteLaunch();
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Launch' }));

		const dialog = await screen.findByRole('dialog', { name: 'Mods are missing' });
		expect(within(dialog).getByText('Alpha Mod')).toBeInTheDocument();
	});

	it('installs the missing mods by the Forge id the bound set recorded', async () => {
		const user = userEvent.setup();
		stubIncompleteLaunch();
		rpc.returns('install_mod', { name: 'Alpha Mod', version: '1.0.0', commit: 'c0ffee' });
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Launch' }));
		await user.click(await screen.findByRole('button', { name: 'Install the missing mods' }));

		expect(rpc.payloads('install_mod')).toEqual([
			{
				path: GAME_DIR,
				mod_id_or_name: 47,
				version: null,
				force: false,
				operation_id: null
			}
		]);
		expect(
			await screen.findByText('The missing mods were installed. The profile can be started now.')
		).toBeInTheDocument();
	});

	it('names the mods that could not be installed', async () => {
		const user = userEvent.setup();
		stubIncompleteLaunch();
		rpc.rejects('install_mod', apiError('The download failed.', 'DOWNLOAD_FAILED'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Launch' }));
		await user.click(await screen.findByRole('button', { name: 'Install the missing mods' }));

		expect(
			await screen.findByText('Some mods could not be installed. Alpha Mod: The download failed.')
		).toBeInTheDocument();
	});

	it('relaunches with the missing mods ignored when the player plays without them', async () => {
		const user = userEvent.setup();
		stubIncompleteLaunch();
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Launch' }));
		await user.click(await screen.findByRole('button', { name: 'Play without them' }));

		const modes = rpc.payloads('launch_profile').map((payload) => {
			return (payload as { mod_set: string }).mod_set;
		});
		expect(modes).toEqual(['apply', 'ignore_missing']);
	});
});

describe('launching a profile', () => {
	it('names the profile the backend reported as started', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.returns('launch_profile', launchResponse('Nikita'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Launch' }));

		expect(await screen.findByText('The game is starting for Nikita.')).toBeInTheDocument();
		const [payload] = rpc.payloads('launch_profile') as { operation_id: string }[];
		expect({ ...payload, operation_id: '' }).toEqual({
			path: GAME_DIR,
			profile_id: 'aaa',
			stop_server_on_exit: true,
			mod_set: 'apply',
			operation_id: ''
		});
		expect(payload.operation_id).not.toBe('');
	});

	it('sends the stored choice of stopping the SPT server on exit', async () => {
		const user = userEvent.setup();
		localStorage.setItem('sptmod.stop_server_on_exit', 'false');
		stubMount();
		rpc.returns('launch_profile', launchResponse('Nikita'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Launch' }));

		const [payload] = rpc.payloads('launch_profile') as { stop_server_on_exit: boolean }[];
		expect(payload.stop_server_on_exit).toBe(false);
	});

	it('shows the failure message of a launch that is not about missing mods', async () => {
		const user = userEvent.setup();
		stubMount();
		rpc.rejects('launch_profile', apiError('The SPT server did not come up.', 'LAUNCH_FAILED'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Launch' }));

		expect(await screen.findByText('The SPT server did not come up.')).toBeInTheDocument();
	});

	it('shows the progress of the running launch and drops the subscription afterwards', async () => {
		const user = userEvent.setup();
		stubMount();
		let operationId = '';
		let finish: (value: LaunchProfileResponse) => void = () => undefined;
		rpc.handle('launch_profile', (payload) => {
			operationId = (payload as { operation_id: string }).operation_id;
			return new Promise<LaunchProfileResponse>((resolve) => {
				finish = resolve;
			});
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Launch' }));
		await vi.waitFor(() => {
			expect(rpc.subscribed(operationId)).toBe(true);
		});
		rpc.progress({
			operation_id: operationId,
			step: 'server',
			percent: 40,
			message: 'Waiting for the SPT server…'
		});

		expect(await screen.findByText('Waiting for the SPT server…')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Starting…' })).toBeDisabled();

		finish(launchResponse('Nikita'));

		expect(await screen.findByText('The game is starting for Nikita.')).toBeInTheDocument();
		expect(rpc.subscribed(operationId)).toBe(false);
	});
});

describe('cleaning what a mod left in a profile', () => {
	const check: CheckResult = {
		residues: { 'Alpha Mod': [residue()] },
		warnings: ['One profile could not be read.']
	};

	const plan: CleanupPlan = {
		mod: 'Alpha Mod',
		profiles: ['aaa'],
		removals: [residue({ count: 2 })],
		untouched: [{ residue: residue({ category: 'quest', count: 1 }), reason: 'Quest is finished.' }],
		warnings: ['Two entries look hand-edited.']
	};

	it('lists the warnings the check reported', async () => {
		stubMount({ check });
		render(Page);

		expect(await screen.findByText('One profile could not be read.')).toBeInTheDocument();
	});

	it('shows the traces of the mod that the check found for the profile', async () => {
		stubMount({ check });
		render(Page);

		expect(await screen.findByRole('heading', { name: 'Alpha Mod' })).toBeInTheDocument();
		expect(screen.getByText('inventory items')).toBeInTheDocument();
		expect(screen.getByText('PMC')).toBeInTheDocument();
		expect(screen.getByText('2 references')).toBeInTheDocument();
	});

	it('asks the backend for the plan of the mod whose preview was opened', async () => {
		const user = userEvent.setup();
		stubMount({ check });
		rpc.returns('plan_profile_cleanup', plan);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Preview cleanup' }));

		expect(rpc.payloads('plan_profile_cleanup')).toEqual([
			{ path: GAME_DIR, mod_name: 'Alpha Mod' }
		]);
		const dialog = await screen.findByRole('dialog', { name: 'Clean aaa.json for Alpha Mod' });
		expect(within(dialog).getByText('2 entries')).toBeInTheDocument();
		expect(within(dialog).getByText('Quest is finished.')).toBeInTheDocument();
		expect(within(dialog).getByText('Two entries look hand-edited.')).toBeInTheDocument();
	});

	it('cleans only the previewed profile and reports that a snapshot was taken', async () => {
		const user = userEvent.setup();
		stubMount({ check });
		rpc.returns('plan_profile_cleanup', plan);
		const cleaned: CleanupResult = {
			mod: 'Alpha Mod',
			profiles: ['aaa'],
			backups: ['backup/aaa.json'],
			removed: [residue()],
			untouched: [],
			snapshot: 'abcdef1',
			warnings: []
		};
		rpc.returns('clean_profile', cleaned);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Preview cleanup' }));
		await user.click(await screen.findByRole('button', { name: 'Clean' }));

		expect(rpc.payloads('clean_profile')).toEqual([
			{ path: GAME_DIR, mod_name: 'Alpha Mod', profiles: ['aaa'] }
		]);
		expect(await screen.findByText('Profiles cleaned. Snapshot taken.')).toBeInTheDocument();
	});

	it('leaves out the snapshot promise when the backend took none', async () => {
		const user = userEvent.setup();
		stubMount({ check });
		rpc.returns('plan_profile_cleanup', plan);
		rpc.returns('clean_profile', {
			mod: 'Alpha Mod',
			profiles: ['aaa'],
			backups: [],
			removed: [],
			untouched: [],
			snapshot: '',
			warnings: []
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Preview cleanup' }));
		await user.click(await screen.findByRole('button', { name: 'Clean' }));

		expect(await screen.findByText('Profiles cleaned.')).toBeInTheDocument();
	});

	it('refuses to clean when the plan holds nothing for the previewed profile', async () => {
		const user = userEvent.setup();
		stubMount({ check });
		rpc.returns('plan_profile_cleanup', {
			mod: 'Alpha Mod',
			profiles: [],
			removals: [],
			untouched: [],
			warnings: []
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Preview cleanup' }));

		expect(
			await screen.findByText('This profile holds nothing of this mod that sptmod can clean.')
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Clean' })).toBeDisabled();
	});

	it('shows the failure message when the plan cannot be read', async () => {
		const user = userEvent.setup();
		stubMount({ check });
		rpc.rejects('plan_profile_cleanup', apiError('The profile is locked.', 'PROFILE_LOCKED'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Preview cleanup' }));

		expect(await screen.findByText('The profile is locked.')).toBeInTheDocument();
		expect(screen.queryByRole('dialog')).toBeNull();
	});
});

describe('as a Fika client', () => {
	const client: FikaSettings = {
		role: 'client',
		host_address: '192.168.1.20',
		base_url: 'https://192.168.1.20:6969'
	};

	it('reads the profiles from the host and offers to connect to it by name', async () => {
		stubMount({ fika: client });
		rpc.returns('list_host_profiles', [hostProfile()]);
		render(Page);

		expect(
			await screen.findByRole('button', { name: 'Connect to 192.168.1.20' })
		).toBeInTheDocument();
		expect(rpc.payloads('list_host_profiles')).toEqual([{ path: GAME_DIR }]);
		expect(screen.getByRole('heading', { name: 'Guest' })).toBeInTheDocument();
		expect(screen.getByText('Level 7')).toBeInTheDocument();
	});

	it('refuses a host profile that is locked with a password', async () => {
		stubMount({ fika: client });
		rpc.returns('list_host_profiles', [hostProfile({ has_password: true })]);
		render(Page);

		expect(await screen.findByRole('button', { name: 'Connect to 192.168.1.20' })).toBeDisabled();
		expect(
			screen.getByText('Locked with a password; it cannot be used from here.')
		).toBeInTheDocument();
	});

	it('falls back to the user name when the host profile carries no nickname', async () => {
		stubMount({ fika: client });
		rpc.returns('list_host_profiles', [hostProfile({ nickname: '', level: 0, side: '' })]);
		render(Page);

		expect(await screen.findByRole('heading', { name: 'host-user' })).toBeInTheDocument();
		expect(screen.queryByText('Level 0')).toBeNull();
	});

	it('says the host has nothing to play when it returns no profiles', async () => {
		stubMount({ fika: client });
		rpc.returns('list_host_profiles', []);
		render(Page);

		expect(await screen.findByText('The host has no profiles to play.')).toBeInTheDocument();
	});

	it('retries reading the host profiles after a failure', async () => {
		const user = userEvent.setup();
		stubMount({ fika: client });
		rpc.rejects('list_host_profiles', apiError('The host cannot be reached.', 'HOST_UNREACHABLE'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Try again' }));
		expect(rpc.payloads('list_host_profiles')).toEqual([{ path: GAME_DIR }, { path: GAME_DIR }]);

		rpc.returns('list_host_profiles', [hostProfile()]);
		await user.click(screen.getByRole('button', { name: 'Try again' }));

		expect(await screen.findByRole('heading', { name: 'Guest' })).toBeInTheDocument();
		expect(screen.queryByText('The host cannot be reached.')).toBeNull();
	});

	it('promises the host server keeps running instead of the local-server wording', async () => {
		const user = userEvent.setup();
		stubMount({
			fika: client,
			status: launchStatus({ client_running: true, profile_id: 'hhh', server_stoppable: true })
		});
		rpc.returns('list_host_profiles', [hostProfile()]);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Stop' }));

		expect(await screen.findByText("The host's server keeps running.")).toBeInTheDocument();
		expect(screen.queryByText('The SPT server is closed with it.')).toBeNull();
	});
});

describe('without a stored game directory', () => {
	it('points at the setup instead of loading anything', async () => {
		localStorage.removeItem('sptmod.game_dir');
		render(Page);

		expect(await screen.findByRole('link', { name: 'Run the setup' })).toBeInTheDocument();
		expect(rpc.names()).toEqual([]);
	});
});

describe('when the profiles cannot be read', () => {
	it('shows the failure message and no profile list', async () => {
		stubMount();
		rpc.rejects('check_profiles', apiError('The game folder is gone.', 'NOT_A_GAME_DIR'));
		render(Page);

		expect(await screen.findByText('The game folder is gone.')).toBeInTheDocument();
		expect(
			screen.getByText('No player profiles found in this installation.')
		).toBeInTheDocument();
	});

	it('names the unreadable profile with the error the backend gave', async () => {
		stubMount({
			profiles: [
				profileSummary({
					profile_id: 'bbb',
					file: 'bbb.json',
					nickname: '',
					readable: false,
					error: 'The file is not valid JSON.'
				})
			]
		});
		render(Page);

		expect(await screen.findByText('The file is not valid JSON.')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Launch' })).toBeNull();
		expect(screen.getByRole('heading', { name: 'bbb.json' })).toBeInTheDocument();
	});
});

describe('the mod set of a profile', () => {
	const loadout = modSet({
		name: 'Loadout',
		entries: [
			{ forge_id: '47', name: 'Alpha', version: '1.0.0', enabled: true },
			{ forge_id: '48', name: 'Beta', version: '2.0.0', enabled: false },
			{ forge_id: '49', name: 'Gamma', version: '', enabled: true },
			{ forge_id: '50', name: 'Delta', version: '3.0.0', enabled: true }
		]
	});

	const installed: ModInfo[] = [
		modInfo({ name: 'alpha-trailer', forge_id: '47', version: '1.0.0', state: 'installed' }),
		modInfo({ name: 'beta-trailer', forge_id: '48', version: '2.0.0', state: 'installed' }),
		modInfo({ name: 'delta-trailer', forge_id: '50', version: '2.0.0', state: 'installed' })
	];

	const bound: ModSetOverview = {
		sets: [loadout],
		bindings: [{ profile_id: 'aaa', set_name: 'Loadout' }]
	};

	function stubPlan(): void {
		rpc.returns('plan_mod_set', {
			name: 'Loadout',
			enable: [{ forge_id: '47', name: 'Alpha', from: '', to: '1.0.0' }],
			disable: [{ forge_id: '48', name: 'Beta', from: '2.0.0', to: '' }],
			missing: [],
			different: [],
			unchanged: []
		});
	}

	it('says the profile runs with whatever is on while it has no set', async () => {
		stubMount();
		render(Page);

		expect(
			await screen.findByText('This profile is played with whatever mods are turned on at the time.')
		).toBeInTheDocument();
	});

	it('shows one row per set entry with the state it has in the installation', async () => {
		stubMount({ sets: bound, mods: installed });
		stubPlan();
		render(Page);

		expect(await screen.findByRole('checkbox', { name: 'Alpha' })).toBeChecked();
		expect(screen.getByRole('checkbox', { name: 'Beta' })).not.toBeChecked();
		expect(screen.getByText('On')).toBeInTheDocument();
		expect(screen.getByText('Off')).toBeInTheDocument();
		expect(screen.getByText('Not installed')).toBeInTheDocument();
		expect(screen.getByText('Other version')).toBeInTheDocument();
		expect(screen.getByText('any version')).toBeInTheDocument();
	});

	it('summarizes what starting the profile would change', async () => {
		stubMount({ sets: bound, mods: installed });
		stubPlan();
		render(Page);

		expect(
			await screen.findByText('Starting this profile: 1 mod on, 1 off.')
		).toBeInTheDocument();
		expect(rpc.payloads('plan_mod_set')).toEqual([{ path: GAME_DIR, name: 'Loadout' }]);
	});

	it('binds the picked set to the profile', async () => {
		const user = userEvent.setup();
		stubMount({ sets: { sets: [loadout], bindings: [] }, mods: installed });
		stubPlan();
		rpc.returns('bind_profile_set', bound);
		render(Page);

		await user.selectOptions(
			await screen.findByLabelText('Mods for this profile'),
			screen.getByRole('option', { name: 'Loadout' })
		);

		expect(rpc.payloads('bind_profile_set')).toEqual([
			{ path: GAME_DIR, profile_id: 'aaa', set_name: 'Loadout' }
		]);
	});

	it('unbinds the profile again', async () => {
		const user = userEvent.setup();
		stubMount({ sets: bound, mods: installed });
		stubPlan();
		rpc.returns('bind_profile_set', { sets: [loadout], bindings: [] });
		render(Page);

		await user.selectOptions(
			await screen.findByLabelText('Mods for this profile'),
			screen.getByRole('option', { name: 'No set' })
		);

		expect(rpc.payloads('bind_profile_set')).toEqual([
			{ path: GAME_DIR, profile_id: 'aaa', set_name: null }
		]);
	});

	it('reports a binding the backend refused', async () => {
		const user = userEvent.setup();
		stubMount({ sets: { sets: [loadout], bindings: [] }, mods: installed });
		stubPlan();
		rpc.rejects('bind_profile_set', apiError('That set is gone.', 'NO_SUCH_SET'));
		render(Page);

		await user.selectOptions(
			await screen.findByLabelText('Mods for this profile'),
			screen.getByRole('option', { name: 'Loadout' })
		);

		expect(await screen.findByText('That set is gone.')).toBeInTheDocument();
	});

	it('saves the set with the entry the player turned off', async () => {
		const user = userEvent.setup();
		stubMount({ sets: bound, mods: installed });
		stubPlan();
		rpc.returns('save_mod_set', loadout);
		render(Page);

		await user.click(await screen.findByRole('checkbox', { name: 'Alpha' }));
		await user.click(screen.getByRole('button', { name: 'Save' }));

		expect(rpc.payloads('save_mod_set')).toEqual([
			{
				path: GAME_DIR,
				name: 'Loadout',
				entries: [
					{ forge_id: '47', name: 'Alpha', version: '1.0.0', enabled: false },
					{ forge_id: '48', name: 'Beta', version: '2.0.0', enabled: false },
					{ forge_id: '49', name: 'Gamma', version: '', enabled: true },
					{ forge_id: '50', name: 'Delta', version: '3.0.0', enabled: true }
				]
			}
		]);
		expect(await screen.findByText('The set was saved.')).toBeInTheDocument();
	});

	it('takes the installed mods over into the set, sorted and without removed ones', async () => {
		const user = userEvent.setup();
		stubMount({
			sets: bound,
			mods: [
				modInfo({ name: 'Zulu', forge_id: '9', version: '1.0.0', state: 'disabled' }),
				modInfo({ name: 'alpha', forge_id: '47', version: '1.0.0', state: 'installed' }),
				modInfo({ name: 'Gone', forge_id: '8', version: '1.0.0', state: 'removed' })
			]
		});
		stubPlan();
		rpc.returns('save_mod_set', loadout);
		render(Page);

		await user.click(
			await screen.findByRole('button', { name: 'Save the installed mods as this set' })
		);

		expect(rpc.payloads('save_mod_set')).toEqual([
			{
				path: GAME_DIR,
				name: 'Loadout',
				entries: [
					{ forge_id: '47', name: 'alpha', version: '1.0.0', enabled: true },
					{ forge_id: '9', name: 'Zulu', version: '1.0.0', enabled: false }
				]
			}
		]);
	});

	it('reports a set that could not be saved', async () => {
		const user = userEvent.setup();
		stubMount({ sets: bound, mods: installed });
		stubPlan();
		rpc.rejects('save_mod_set', apiError('The sets file is read-only.', 'PERMISSION'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Save' }));

		expect(await screen.findByText('The sets file is read-only.')).toBeInTheDocument();
	});

	it('shows no summary when the plan cannot be read', async () => {
		stubMount({ sets: bound, mods: installed });
		rpc.rejects('plan_mod_set', apiError('The set is broken.', 'BAD_SET'));
		render(Page);

		await screen.findByRole('checkbox', { name: 'Alpha' });
		expect(screen.queryByText(/Starting this profile:/)).toBeNull();
	});

	it('says so when the bound set holds no mods yet', async () => {
		stubMount({
			sets: {
				sets: [modSet({ name: 'Empty', entries: [] })],
				bindings: [{ profile_id: 'aaa', set_name: 'Empty' }]
			}
		});
		rpc.returns('plan_mod_set', {
			name: 'Empty',
			enable: [],
			disable: [],
			missing: [],
			different: [],
			unchanged: []
		});
		render(Page);

		expect(await screen.findByText('This set holds no mods yet.')).toBeInTheDocument();
		expect(await screen.findByText('Starting this profile: Nothing to change.')).toBeInTheDocument();
	});
});
