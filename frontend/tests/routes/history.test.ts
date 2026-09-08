/**
 * Component tests for the history page.
 *
 * Setup: the page is mounted with a stored game directory, a canned commit log
 * and a canned list of installed mods. Assertions read the rendered entries and
 * the payloads the real `$lib/api/client` produced. Reason: the page decides
 * from the join of commit trailer and installed state which entry can still be
 * undone, and that join is only observable in the mounted component.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CommitInfo, ModInfo } from '$lib/api/client';
import { setSubNav } from '$lib/shellstate';
import { invalidate } from '$lib/viewcache';

import Page from '../../src/routes/history/+page.svelte';
import { apiError } from '../support/errors';
import { commitInfo, modInfo } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

const GAME_DIR = '/games/SPT';

const installCommit = commitInfo({
	hash: 'aaaaaaaabbbbbbbbccccccccdddddddd11111111',
	subject: 'Install Alpha 1.0.0',
	trailers: {
		Mod: 'alpha-trailer',
		'Mod-Version': '1.0.0',
		'Mod-Type': 'server',
		'Mod-State': 'installed'
	}
});

const snapshotCommit = commitInfo({
	hash: 'eeeeeeeeffffffff000000001111111122222222',
	subject: 'Snapshot before the raid',
	trailers: {}
});

function stubHistory(commits: CommitInfo[], mods: ModInfo[]): void {
	rpc.returns('history', commits);
	rpc.returns('list_mods', mods);
}

const installedAlpha = modInfo({ name: 'alpha-trailer', state: 'installed' });

beforeEach(() => {
	localStorage.setItem('sptmod.game_dir', GAME_DIR);
});

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	setSubNav([], null);
});

describe('the entry list', () => {
	it('shows the subject, the short hash and the mod summary of a commit', async () => {
		stubHistory([installCommit], [installedAlpha]);
		render(Page);

		expect(await screen.findByText('Install Alpha 1.0.0')).toBeInTheDocument();
		expect(screen.getByText('aaaaaaaa')).toBeInTheDocument();
		expect(screen.getByText(/alpha-trailer · 1\.0\.0 · server/)).toBeInTheDocument();
		expect(screen.getByText('installed')).toBeInTheDocument();
		expect(screen.getByText('1 entry')).toBeInTheDocument();
	});

	it('asks the backend for the whole log of the game directory', async () => {
		stubHistory([installCommit], [installedAlpha]);
		render(Page);

		await screen.findByText('Install Alpha 1.0.0');
		expect(rpc.payloads('history')).toEqual([{ path: GAME_DIR, name: null }]);
	});

	it('says so when the repository holds no commits', async () => {
		stubHistory([], []);
		render(Page);

		expect(await screen.findByText('No history yet.')).toBeInTheDocument();
		expect(screen.getByText('0 entries')).toBeInTheDocument();
	});

	it('shows the failure message when the log cannot be read', async () => {
		stubHistory([], []);
		rpc.rejects('history', apiError('The repository is broken.', 'BAD_REPO'));
		render(Page);

		expect(await screen.findByText('The repository is broken.')).toBeInTheDocument();
	});

	it('points at the setup when no game directory was stored', async () => {
		localStorage.removeItem('sptmod.game_dir');
		render(Page);

		expect(await screen.findByRole('link', { name: 'Run the setup' })).toBeInTheDocument();
		expect(rpc.names()).toEqual([]);
	});
});

describe('which entries can still be undone', () => {
	it.each([
		['installed' as const],
		['updated' as const],
		['enabled' as const]
	])('offers Disable and Remove while the mod is %s', async (state) => {
		stubHistory([installCommit], [modInfo({ name: 'alpha-trailer', state })]);
		render(Page);

		expect(await screen.findByRole('button', { name: 'Disable' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument();
	});

	it.each([['disabled' as const], ['removed' as const]])(
		'offers no undo while the mod is %s',
		async (state) => {
			stubHistory([installCommit], [modInfo({ name: 'alpha-trailer', state })]);
			render(Page);

			await screen.findByText('Install Alpha 1.0.0');
			expect(screen.queryByRole('button', { name: 'Disable' })).toBeNull();
			expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
		}
	);

	it('offers no undo for a commit whose mod is not installed at all', async () => {
		stubHistory([installCommit], []);
		render(Page);

		await screen.findByText('Install Alpha 1.0.0');
		expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
	});

	it('offers neither files nor undo for a commit without a mod trailer', async () => {
		stubHistory([snapshotCommit], [installedAlpha]);
		render(Page);

		await screen.findByText('Snapshot before the raid');
		expect(screen.queryByRole('button', { name: 'Files' })).toBeNull();
		expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
	});
});

describe('the files of a commit', () => {
	it('reads the files of the mod the commit names and folds them away again', async () => {
		const user = userEvent.setup();
		stubHistory([installCommit], [installedAlpha]);
		rpc.returns('mod_files', [
			{ path: 'user/mods/Alpha/package.json', change: 'A' },
			{ path: 'user/mods/Alpha/src/mod.js', change: 'A' }
		]);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Files' }));

		expect(rpc.payloads('mod_files')).toEqual([{ path: GAME_DIR, name: 'alpha-trailer' }]);
		expect(await screen.findByText('user/mods/Alpha/src/mod.js')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Hide files' }));
		expect(screen.queryByText('user/mods/Alpha/src/mod.js')).toBeNull();
	});

	it('shows the failure message when the files cannot be read', async () => {
		const user = userEvent.setup();
		stubHistory([installCommit], [installedAlpha]);
		rpc.rejects('mod_files', apiError('That commit is gone.', 'NO_SUCH_COMMIT'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Files' }));

		expect(await screen.findByText('That commit is gone.')).toBeInTheDocument();
	});
});

describe('undoing an entry', () => {
	it('confirms before it removes and then removes without force', async () => {
		const user = userEvent.setup();
		stubHistory([installCommit], [installedAlpha]);
		rpc.returns('remove_mod', {
			name: 'alpha-trailer',
			commit: 'c0ffee',
			files: [],
			warnings: [],
			cleanup: null
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Remove' }));

		const dialog = await screen.findByRole('dialog', { name: 'Remove alpha-trailer?' });
		expect(rpc.payloads('remove_mod')).toEqual([]);
		await user.click(within(dialog).getByRole('button', { name: 'Remove' }));

		expect(rpc.payloads('remove_mod')).toEqual([
			{ path: GAME_DIR, name: 'alpha-trailer', force: false, clean_profiles: false }
		]);
	});

	it('removes nothing when the confirmation is dismissed', async () => {
		const user = userEvent.setup();
		stubHistory([installCommit], [installedAlpha]);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Remove' }));
		const dialog = await screen.findByRole('dialog', { name: 'Remove alpha-trailer?' });
		await user.click(within(dialog).getByRole('button', { name: 'Close' }));

		expect(rpc.payloads('remove_mod')).toEqual([]);
	});

	it('confirms before it disables and then disables the mod', async () => {
		const user = userEvent.setup();
		stubHistory([installCommit], [installedAlpha]);
		rpc.returns('disable_mod', {
			name: 'alpha-trailer',
			state: 'disabled',
			commit: 'c0ffee',
			files: [],
			notice: ''
		});
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Disable' }));
		const dialog = await screen.findByRole('dialog', { name: 'Disable alpha-trailer?' });
		await user.click(within(dialog).getByRole('button', { name: 'Disable' }));

		expect(rpc.payloads('disable_mod')).toEqual([{ path: GAME_DIR, name: 'alpha-trailer' }]);
	});
});

describe('a removal the backend refused', () => {
	function stubRefusal(code: string): void {
		stubHistory([installCommit], [installedAlpha]);
		rpc.rejects(
			'remove_mod',
			apiError('Another mod depends on it.', code, {
				conflicts: [
					{
						kind: 'missing-dependency',
						severity: 'error',
						message: 'Beta needs Alpha.',
						path: 'user/mods/Beta',
						mod: 'Beta'
					}
				],
				residues: [
					{
						profile: 'aaa',
						category: 'item',
						ids: ['x1'],
						count: 3,
						character: 'pmc',
						path: ''
					}
				]
			})
		);
	}

	it.each([
		['CONFLICT'],
		['DEPENDENCY'],
		['UNRESOLVABLE_DEPENDENCY'],
		['PROFILE_RESIDUE']
	])('offers to remove anyway after a %s failure', async (code) => {
		const user = userEvent.setup();
		stubRefusal(code);
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Remove' }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Remove alpha-trailer?' })).getByRole(
				'button',
				{ name: 'Remove' }
			)
		);

		const failure = await screen.findByRole('dialog', { name: 'alpha-trailer failed' });
		expect(within(failure).getByText('Another mod depends on it.')).toBeInTheDocument();
		expect(within(failure).getByText('Beta needs Alpha.')).toBeInTheDocument();
		expect(within(failure).getByText('item: 3 references')).toBeInTheDocument();
		expect(within(failure).getByRole('button', { name: 'Remove anyway' })).toBeInTheDocument();
	});

	it('retries the removal with force once the player insists', async () => {
		const user = userEvent.setup();
		stubRefusal('CONFLICT');
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Remove' }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Remove alpha-trailer?' })).getByRole(
				'button',
				{ name: 'Remove' }
			)
		);
		await user.click(
			within(await screen.findByRole('dialog', { name: 'alpha-trailer failed' })).getByRole(
				'button',
				{ name: 'Remove anyway' }
			)
		);

		expect(rpc.payloads('remove_mod')).toEqual([
			{ path: GAME_DIR, name: 'alpha-trailer', force: false, clean_profiles: false },
			{ path: GAME_DIR, name: 'alpha-trailer', force: true, clean_profiles: false }
		]);
	});

	it('offers no force for a failure that force cannot solve', async () => {
		const user = userEvent.setup();
		stubHistory([installCommit], [installedAlpha]);
		rpc.rejects('remove_mod', apiError('The files are gone.', 'MOD_MISSING'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Remove' }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Remove alpha-trailer?' })).getByRole(
				'button',
				{ name: 'Remove' }
			)
		);

		const failure = await screen.findByRole('dialog', { name: 'alpha-trailer failed' });
		expect(within(failure).getByText('The files are gone.')).toBeInTheDocument();
		expect(within(failure).queryByRole('button', { name: 'Remove anyway' })).toBeNull();
	});

	it('offers no force after a failed disable', async () => {
		const user = userEvent.setup();
		stubHistory([installCommit], [installedAlpha]);
		rpc.rejects('disable_mod', apiError('Another mod depends on it.', 'DEPENDENCY'));
		render(Page);

		await user.click(await screen.findByRole('button', { name: 'Disable' }));
		await user.click(
			within(await screen.findByRole('dialog', { name: 'Disable alpha-trailer?' })).getByRole(
				'button',
				{ name: 'Disable' }
			)
		);

		const failure = await screen.findByRole('dialog', { name: 'alpha-trailer failed' });
		expect(within(failure).queryByRole('button', { name: 'Remove anyway' })).toBeNull();
	});
});
