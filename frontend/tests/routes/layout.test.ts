/**
 * Component tests for the app shell.
 *
 * Setup: the layout is mounted with a route, a stubbed Tauri window and updater
 * and a snippet standing in for the page body. Assertions read the rendered
 * navigation, the two notices and the theme attribute of the document. Reason:
 * the shell decides which link is current, when it bothers the player with an
 * SPT or app-update notice, and it is the only place that turns the shared
 * sub-navigation state into markup.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet } from 'svelte';

import { getCurrentWindow } from '@tauri-apps/api/window';
import { check } from '@tauri-apps/plugin-updater';

import { setGameDir, setSubNav } from '$lib/shellstate';

import Layout from '../../src/routes/+layout.svelte';
import { sptReleaseList } from '../support/factories';
import { setPageUrl } from '../support/pagestate';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

vi.mock('$app/navigation', () => ({ goto: vi.fn(() => Promise.resolve()) }));

vi.mock('$app/state', async () => {
	const { pageMock } = await import('../support/pagestate');
	return pageMock;
});

vi.mock('@tauri-apps/api/app', () => ({ getVersion: vi.fn(() => Promise.resolve('1.2.3')) }));
vi.mock('@tauri-apps/plugin-process', () => ({ relaunch: vi.fn(() => Promise.resolve()) }));
vi.mock('@tauri-apps/plugin-updater', () => ({ check: vi.fn(() => Promise.resolve(null)) }));

vi.mock('@tauri-apps/api/window', () => {
	const current = {
		isMaximized: vi.fn(() => Promise.resolve(false)),
		minimize: vi.fn(() => Promise.resolve()),
		close: vi.fn(() => Promise.resolve()),
		toggleMaximize: vi.fn(() => Promise.resolve())
	};
	return { getCurrentWindow: () => current };
});

const GAME_DIR = '/games/SPT';

const body = createRawSnippet(() => ({ render: () => '<p>page body</p>' }));

function mount(): void {
	render(Layout, { props: { children: body } });
}

/** The updater plugin only ever has its version and its notes read here. */
function stubUpdate(version: string): void {
	vi.mocked(check).mockResolvedValue({
		version,
		body: 'Fixes the launcher.',
		downloadAndInstall: () => Promise.resolve(),
		close: () => Promise.resolve()
	} as unknown as Awaited<ReturnType<typeof check>>);
}

beforeEach(() => {
	setPageUrl('http://localhost/profile');
});

afterEach(() => {
	rpc.reset();
	setGameDir(null);
	setSubNav([], null);
	vi.mocked(check).mockReset();
	document.documentElement.removeAttribute('data-theme');
});

describe('the main navigation', () => {
	it('marks the link of the open route as the current page', async () => {
		mount();

		expect(await screen.findByRole('link', { current: 'page' })).toHaveTextContent('Profiles');
	});

	it('offers a link for every page of the app', () => {
		mount();

		const labels = screen
			.getAllByRole('link')
			.map((link) => link.textContent?.trim() ?? '')
			.filter((label) => label !== '');
		expect(labels).toEqual(['Mods', 'Profiles', 'History', 'Settings']);
	});

	it('renders the page body it was given', () => {
		mount();

		expect(screen.getByText('page body')).toBeInTheDocument();
	});
});

describe('the section navigation', () => {
	it('shows nothing while no page published a section list', () => {
		mount();

		expect(screen.queryByRole('navigation', { name: 'Profiles' })).toBeNull();
	});

	it('shows the sections a page published under the label of the open route', async () => {
		mount();

		setSubNav([{ id: 'aaa', label: 'Nikita' }], 'aaa');

		const nav = await screen.findByRole('navigation', { name: 'Profiles' });
		expect(within(nav).getByRole('button', { name: 'Nikita' })).toHaveAttribute(
			'aria-current',
			'true'
		);
	});
});

describe('the SPT notice', () => {
	it('names the newer SPT release once a game directory is known', async () => {
		rpc.returns(
			'list_spt_releases',
			sptReleaseList({ current_version: '4.1.0', latest: '4.2.0' })
		);
		mount();

		setGameDir(GAME_DIR);

		expect(
			await screen.findByText('SPT 4.2.0 is out. You are on 4.1.0.')
		).toBeInTheDocument();
		expect(rpc.payloads('list_spt_releases')).toEqual([{ path: GAME_DIR }]);
	});

	it('stays quiet while the installed release is the newest one', async () => {
		rpc.returns(
			'list_spt_releases',
			sptReleaseList({ current_version: '4.2.0', latest: '4.2.0' })
		);
		mount();

		setGameDir(GAME_DIR);

		await vi.waitFor(() => {
			expect(rpc.payloads('list_spt_releases')).toHaveLength(1);
		});
		expect(screen.queryByText(/is out\. You are on/)).toBeNull();
	});

	it('stays quiet when the release list cannot be read', async () => {
		rpc.rejects('list_spt_releases', new Error('The feed is down.'));
		mount();

		setGameDir(GAME_DIR);

		await vi.waitFor(() => {
			expect(rpc.payloads('list_spt_releases')).toHaveLength(1);
		});
		expect(screen.queryByText('The feed is down.')).toBeNull();
	});

	it('forgets the release the player dismissed', async () => {
		const user = userEvent.setup();
		rpc.returns(
			'list_spt_releases',
			sptReleaseList({ current_version: '4.1.0', latest: '4.2.0' })
		);
		mount();

		setGameDir(GAME_DIR);
		await screen.findByText('SPT 4.2.0 is out. You are on 4.1.0.');
		await user.click(screen.getByRole('button', { name: 'Dismiss' }));

		expect(screen.queryByText('SPT 4.2.0 is out. You are on 4.1.0.')).toBeNull();
		expect(localStorage.getItem('sptmod.dismissedSptRelease')).toBe('4.2.0');
	});

	it('stays quiet about a release that was dismissed before', async () => {
		localStorage.setItem('sptmod.dismissedSptRelease', '4.2.0');
		rpc.returns(
			'list_spt_releases',
			sptReleaseList({ current_version: '4.1.0', latest: '4.2.0' })
		);
		mount();

		setGameDir(GAME_DIR);

		await vi.waitFor(() => {
			expect(rpc.payloads('list_spt_releases')).toHaveLength(1);
		});
		expect(screen.queryByText('SPT 4.2.0 is out. You are on 4.1.0.')).toBeNull();
	});
});

describe('the app update notice', () => {
	it('names the new app version and points at the update settings', async () => {
		stubUpdate('9.9.9');
		mount();

		expect(await screen.findByText(/Version 9\.9\.9 is available\./)).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Open the update settings' })).toHaveAttribute(
			'href',
			'/settings#updates'
		);
	});

	it('stays quiet while the app is the newest version', async () => {
		vi.mocked(check).mockResolvedValue(null);
		mount();

		await vi.waitFor(() => {
			expect(vi.mocked(check)).toHaveBeenCalledTimes(1);
		});
		expect(screen.queryByRole('link', { name: 'Open the update settings' })).toBeNull();
	});
});

describe('the theme', () => {
	it('applies the stored preference to the document', async () => {
		localStorage.setItem('sptmod.theme', 'dark');
		mount();

		await vi.waitFor(() => {
			expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
		});
	});

	it('switches the document over when the title bar asks for it', async () => {
		const user = userEvent.setup();
		localStorage.setItem('sptmod.theme', 'dark');
		mount();

		await user.click(await screen.findByRole('button', { name: 'Switch to light mode' }));

		expect(document.documentElement).toHaveAttribute('data-theme', 'light');
		expect(localStorage.getItem('sptmod.theme')).toBe('light');
	});
});

describe('the maximize button', () => {
	it('toggles the Tauri window and reads the new state back', async () => {
		const user = userEvent.setup();
		const current = getCurrentWindow();
		vi.mocked(current.isMaximized).mockResolvedValue(false);
		mount();

		vi.mocked(current.isMaximized).mockResolvedValue(true);
		await user.click(await screen.findByRole('button', { name: 'Maximize' }));

		expect(vi.mocked(current.toggleMaximize)).toHaveBeenCalledTimes(1);
		expect(await screen.findByRole('button', { name: 'Restore' })).toBeInTheDocument();
	});
});
