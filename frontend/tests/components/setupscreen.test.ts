/**
 * Component tests for the first-run setup card.
 *
 * Setup: `SetupScreen` is mounted with a folder picker and a link opener that
 * are stubbed, and with canned answers for `init_game_dir`. Assertions read the
 * rendered card, the payload the real `$lib/api/client` produced and the path
 * the card handed on. Reason: the card turns four different backend failures
 * into four different offers to the player, and only the mounted component
 * shows which one it makes.
 */

import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { open } from '@tauri-apps/plugin-dialog';
import { openUrl } from '@tauri-apps/plugin-opener';

import type { InitResult } from '$lib/api/client';

import SetupScreen from '$lib/components/SetupScreen.svelte';
import { apiError } from '../support/errors';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

vi.mock('@tauri-apps/plugin-dialog', () => ({
	open: vi.fn(),
	save: vi.fn()
}));

vi.mock('@tauri-apps/plugin-opener', () => ({
	openUrl: vi.fn(() => Promise.resolve())
}));

function initResult(overrides: Partial<InitResult> = {}): InitResult {
	return {
		game_dir: '/games/SPT',
		commit: '0123456789abcdef',
		spt_version: '4.1.0',
		warnings: [],
		pre_existing_mods: [],
		adopted: [],
		...overrides
	};
}

/** Mounts the card and records every path it reported as ready. */
function mount(initialPath = ''): string[] {
	const ready: string[] = [];
	render(SetupScreen, {
		props: { initialPath, onready: (path: string) => ready.push(path) }
	});
	return ready;
}

afterEach(() => {
	rpc.reset();
	vi.mocked(open).mockReset();
	vi.mocked(openUrl).mockReset();
});

describe('picking the folder', () => {
	it('initializes the folder the picker returned and hands it on', async () => {
		const user = userEvent.setup();
		vi.mocked(open).mockResolvedValue('/picked/SPT');
		rpc.returns('init_game_dir', initResult({ game_dir: '/picked/SPT' }));
		const ready = mount();

		await user.click(screen.getByRole('button', { name: 'Browse…' }));

		expect(rpc.payloads('init_game_dir')).toEqual([
			{ path: '/picked/SPT', adopt: false, spt_version: null }
		]);
		await vi.waitFor(() => {
			expect(ready).toEqual(['/picked/SPT']);
		});
	});

	it('asks the backend for nothing when the picker was cancelled', async () => {
		const user = userEvent.setup();
		vi.mocked(open).mockResolvedValue(null);
		const ready = mount();

		await user.click(screen.getByRole('button', { name: 'Browse…' }));

		expect(rpc.names()).toEqual([]);
		expect(ready).toEqual([]);
	});

	it('reports a picker that failed', async () => {
		const user = userEvent.setup();
		vi.mocked(open).mockRejectedValue(new Error('No file dialog is available.'));
		mount();

		await user.click(screen.getByRole('button', { name: 'Browse…' }));

		expect(await screen.findByText('No file dialog is available.')).toBeInTheDocument();
	});
});

describe('typing the folder', () => {
	it('sends the trimmed path of the input', async () => {
		const user = userEvent.setup();
		rpc.returns('init_game_dir', initResult());
		mount();

		await user.type(screen.getByLabelText('SPT installation folder'), '  /games/SPT  ');
		await user.click(screen.getByRole('button', { name: 'Continue' }));

		expect(rpc.payloads('init_game_dir')).toEqual([
			{ path: '/games/SPT', adopt: false, spt_version: null }
		]);
	});

	it('refuses to continue while no folder was named', () => {
		mount();

		expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
	});

	it('starts with the folder the page passed in', () => {
		mount('/games/SPT');

		expect(screen.getByLabelText('SPT installation folder')).toHaveValue('/games/SPT');
	});
});

describe('when Git is missing', () => {
	it('offers the download page of Git beside the failure message', async () => {
		const user = userEvent.setup();
		rpc.rejects('init_game_dir', apiError('Git is not installed.', 'GIT_MISSING'));
		mount('/games/SPT');

		await user.click(screen.getByRole('button', { name: 'Continue' }));

		expect(await screen.findByText('Git is not installed.')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Download Git' }));

		expect(vi.mocked(openUrl).mock.calls).toEqual([['https://git-scm.com/downloads']]);
	});

	it('offers no download for a failure that has nothing to do with Git', async () => {
		const user = userEvent.setup();
		rpc.rejects('init_game_dir', apiError('This is not an SPT folder.', 'NOT_A_GAME_DIR'));
		mount('/games/SPT');

		await user.click(screen.getByRole('button', { name: 'Continue' }));

		expect(await screen.findByText('This is not an SPT folder.')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Download Git' })).toBeNull();
	});
});

describe('when the folder was set up before', () => {
	it('goes straight in with the folder the player named', async () => {
		const user = userEvent.setup();
		rpc.rejects('init_game_dir', apiError('Already set up.', 'ALREADY_INITIALIZED'));
		const ready = mount('  /games/SPT  ');

		await user.click(screen.getByRole('button', { name: 'Continue' }));

		await vi.waitFor(() => {
			expect(ready).toEqual(['/games/SPT']);
		});
		expect(screen.queryByText('Already set up.')).toBeNull();
	});
});

describe('when the SPT version cannot be detected', () => {
	it('asks for the version and sends it with the next attempt', async () => {
		const user = userEvent.setup();
		rpc.rejects('init_game_dir', apiError('No version file.', 'SPT_VERSION_UNKNOWN'));
		const ready = mount('/games/SPT');

		await user.click(screen.getByRole('button', { name: 'Continue' }));

		expect(
			await screen.findByText('The SPT version cannot be detected. Enter it and continue.')
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();

		rpc.returns('init_game_dir', initResult());
		await user.type(screen.getByLabelText('SPT version'), '4.1.2');
		await user.click(screen.getByRole('button', { name: 'Continue' }));

		expect(rpc.payloads('init_game_dir').at(-1)).toEqual({
			path: '/games/SPT',
			adopt: false,
			spt_version: '4.1.2'
		});
		await vi.waitFor(() => {
			expect(ready).toEqual(['/games/SPT']);
		});
	});
});

describe('adopting the mods that are already there', () => {
	function stubCandidates(): void {
		rpc.returns(
			'init_game_dir',
			initResult({ pre_existing_mods: ['Alpha', 'Beta', 'Gamma'] })
		);
	}

	it('offers every found mod preselected', async () => {
		const user = userEvent.setup();
		stubCandidates();
		mount('/games/SPT');

		await user.click(screen.getByRole('button', { name: 'Continue' }));

		expect(
			await screen.findByRole('button', { name: 'Adopt 3 of 3 and finish' })
		).toBeInTheDocument();
		expect(screen.getByRole('checkbox', { name: 'Beta' })).toBeChecked();
	});

	it('sends only the mods that are still ticked', async () => {
		const user = userEvent.setup();
		stubCandidates();
		mount('/games/SPT');

		await user.click(screen.getByRole('button', { name: 'Continue' }));
		await user.click(await screen.findByRole('checkbox', { name: 'Beta' }));

		expect(screen.getByRole('button', { name: 'Adopt 2 of 3 and finish' })).toBeInTheDocument();

		rpc.returns('init_game_dir', initResult({ game_dir: '/games/SPT' }));
		await user.click(screen.getByRole('button', { name: 'Adopt 2 of 3 and finish' }));

		expect(rpc.payloads('init_game_dir').at(-1)).toEqual({
			path: '/games/SPT',
			adopt: ['Alpha', 'Gamma'],
			spt_version: null
		});
	});

	it('adopts nothing when every mod was unticked', async () => {
		const user = userEvent.setup();
		stubCandidates();
		mount('/games/SPT');

		await user.click(screen.getByRole('button', { name: 'Continue' }));
		for (const name of ['Alpha', 'Beta', 'Gamma']) {
			await user.click(await screen.findByRole('checkbox', { name }));
		}

		rpc.returns('init_game_dir', initResult());
		await user.click(screen.getByRole('button', { name: 'Adopt 0 of 3 and finish' }));

		expect(rpc.payloads('init_game_dir').at(-1)).toEqual({
			path: '/games/SPT',
			adopt: false,
			spt_version: null
		});
	});

	it('hands on the folder the backend reported, not the typed one', async () => {
		const user = userEvent.setup();
		stubCandidates();
		const ready = mount('/games/spt');

		await user.click(screen.getByRole('button', { name: 'Continue' }));
		rpc.returns('init_game_dir', initResult({ game_dir: '/games/SPT' }));
		await user.click(await screen.findByRole('button', { name: 'Adopt 3 of 3 and finish' }));

		await vi.waitFor(() => {
			expect(ready).toEqual(['/games/SPT']);
		});
	});

	it('reports a failed adoption without leaving the list', async () => {
		const user = userEvent.setup();
		stubCandidates();
		mount('/games/SPT');

		await user.click(screen.getByRole('button', { name: 'Continue' }));
		rpc.rejects('init_game_dir', apiError('The mod folder is read-only.', 'PERMISSION'));
		await user.click(await screen.findByRole('button', { name: 'Adopt 3 of 3 and finish' }));

		expect(await screen.findByText('The mod folder is read-only.')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Adopt 3 of 3 and finish' })).toBeInTheDocument();
	});

	it('goes back to the folder input on request', async () => {
		const user = userEvent.setup();
		stubCandidates();
		mount('/games/SPT');

		await user.click(screen.getByRole('button', { name: 'Continue' }));
		await user.click(await screen.findByRole('button', { name: 'Back' }));

		expect(screen.getByLabelText('SPT installation folder')).toHaveValue('/games/SPT');
	});
});
