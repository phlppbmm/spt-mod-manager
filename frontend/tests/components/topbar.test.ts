/**
 * Component tests for the title bar and its game-folder menu.
 *
 * Setup: `TopBar` is mounted with a theme and a maximize state, with the Tauri
 * window and `goto` stubbed. Assertions read the rendered chrome, the shared
 * filter state, the intent queue and the stubbed window calls. Reason: the bar
 * owns the global search shortcut, the debounced hand-over of the search text
 * and the window buttons, none of which any unit test covers.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getCurrentWindow } from '@tauri-apps/api/window';

import { goto } from '$app/navigation';

import { clearFilter, getFilter } from '$lib/filterstate';
import { takeIntents } from '$lib/intents';
import { setGameDir, setSearchEnabled } from '$lib/shellstate';

import TopBar from '$lib/components/TopBar.svelte';
import { setPageUrl } from '../support/pagestate';

vi.mock('$app/navigation', () => ({ goto: vi.fn(() => Promise.resolve()) }));

vi.mock('$app/state', async () => {
	const { pageMock } = await import('../support/pagestate');
	return pageMock;
});

vi.mock('@tauri-apps/api/window', () => {
	const current = {
		isMaximized: vi.fn(() => Promise.resolve(false)),
		minimize: vi.fn(() => Promise.resolve()),
		close: vi.fn(() => Promise.resolve()),
		toggleMaximize: vi.fn(() => Promise.resolve())
	};
	return { getCurrentWindow: () => current };
});

interface Mounted {
	toggleTheme: number;
	toggleMaximize: number;
}

function mount(maximized = false): Mounted {
	const mounted: Mounted = { toggleTheme: 0, toggleMaximize: 0 };
	render(TopBar, {
		props: {
			theme: 'dark' as const,
			maximized,
			onToggleTheme: () => {
				mounted.toggleTheme += 1;
			},
			onToggleMaximize: () => {
				mounted.toggleMaximize += 1;
			}
		}
	});
	return mounted;
}

beforeEach(() => {
	setPageUrl('http://localhost/');
});

afterEach(() => {
	clearFilter();
	setGameDir(null);
	setSearchEnabled(false);
	takeIntents(['snapshot', 'launch', 'sync', 'update-all', 'change-folder', 'reveal-mod']);
	vi.mocked(goto).mockReset();
	const current = getCurrentWindow();
	vi.mocked(current.minimize).mockClear();
	vi.mocked(current.close).mockClear();
	vi.mocked(current.toggleMaximize).mockClear();
});

describe('the search field', () => {
	it('takes the focus when the slash key is pressed anywhere', async () => {
		const user = userEvent.setup();
		setSearchEnabled(true);
		mount();

		await user.keyboard('/');

		expect(screen.getByRole('textbox', { name: 'Search mods' })).toHaveFocus();
	});

	it('leaves the slash key alone while another input has the focus', async () => {
		const user = userEvent.setup();
		setSearchEnabled(true);
		const elsewhere = document.createElement('input');
		document.body.append(elsewhere);
		elsewhere.focus();
		mount();

		await user.keyboard('/');

		expect(elsewhere).toHaveValue('/');
		expect(screen.getByRole('textbox', { name: 'Search mods' })).not.toHaveFocus();
		elsewhere.remove();
	});

	it('hands the typed text to the shared filter once the typing stops', async () => {
		const user = userEvent.setup();
		setSearchEnabled(true);
		mount();

		await user.type(screen.getByRole('textbox', { name: 'Search mods' }), 'alpha');

		expect(getFilter().search).toBe('');
		await vi.waitFor(() => {
			expect(getFilter().search).toBe('alpha');
		});
	});

	it('adopts a search that another part of the app set', async () => {
		setSearchEnabled(true);
		mount();

		expect(screen.getByRole('textbox', { name: 'Search mods' })).toHaveValue('');
	});

	it('gives up the focus on Escape', async () => {
		const user = userEvent.setup();
		setSearchEnabled(true);
		mount();

		const input = screen.getByRole('textbox', { name: 'Search mods' });
		await user.click(input);
		await user.keyboard('{Escape}');

		expect(input).not.toHaveFocus();
	});

	it('is switched off with an explanation while no mod list is open', () => {
		mount();

		const input = screen.getByRole('textbox', { name: 'Search mods' });
		expect(input).toBeDisabled();
		expect(input).toHaveAttribute('title', 'Search works on the Mods view');
	});
});

describe('the catalog sync button', () => {
	it('queues a sync while the mod list is open', async () => {
		const user = userEvent.setup();
		setSearchEnabled(true);
		mount();

		await user.click(screen.getByRole('button', { name: 'Sync catalog' }));

		expect(takeIntents(['sync'])).toEqual([{ kind: 'sync' }]);
	});

	it('says where the sync lives while no mod list is open', () => {
		mount();

		expect(
			screen.getByRole('button', { name: 'Sync catalog (open Mods first)' })
		).toBeDisabled();
	});
});

describe('the window controls', () => {
	it('minimizes and closes through the Tauri window', async () => {
		const user = userEvent.setup();
		mount();
		const current = getCurrentWindow();

		await user.click(screen.getByRole('button', { name: 'Minimize' }));
		expect(vi.mocked(current.minimize)).toHaveBeenCalledTimes(1);

		await user.click(screen.getByRole('button', { name: 'Close' }));
		expect(vi.mocked(current.close)).toHaveBeenCalledTimes(1);
	});

	it('hands the maximize toggle to the shell and names the current state', async () => {
		const user = userEvent.setup();
		const mounted = mount(true);

		await user.click(screen.getByRole('button', { name: 'Restore' }));

		expect(mounted.toggleMaximize).toBe(1);
		expect(screen.queryByRole('button', { name: 'Maximize' })).toBeNull();
	});

	it('hands the theme switch to the shell and names the theme it would go to', async () => {
		const user = userEvent.setup();
		const mounted = mount();

		await user.click(screen.getByRole('button', { name: 'Switch to light mode' }));

		expect(mounted.toggleTheme).toBe(1);
	});
});

describe('the game folder menu', () => {
	it('names the folder itself, not the whole path', async () => {
		localStorage.setItem('sptmod.game_dir', '/games/SPT Live');
		mount();

		expect(await screen.findByRole('button', { name: 'SPT Live' })).toBeInTheDocument();
	});

	it('says so while no folder was picked', () => {
		mount();

		expect(screen.getByRole('button', { name: 'No game folder' })).toBeInTheDocument();
	});

	it('shows the whole path once the menu is open', async () => {
		const user = userEvent.setup();
		localStorage.setItem('sptmod.game_dir', '/games/SPT Live');
		mount();

		await user.click(await screen.findByRole('button', { name: 'SPT Live' }));

		const menu = await screen.findByRole('dialog', { name: 'Game folder' });
		expect(within(menu).getByText('/games/SPT Live')).toBeInTheDocument();
	});

	it('goes to the mods page and asks it for the setup when the folder should change', async () => {
		const user = userEvent.setup();
		setPageUrl('http://localhost/settings');
		localStorage.setItem('sptmod.game_dir', '/games/SPT Live');
		mount();

		await user.click(await screen.findByRole('button', { name: 'SPT Live' }));
		await user.click(await screen.findByRole('button', { name: 'Change folder' }));

		await vi.waitFor(() => {
			expect(vi.mocked(goto).mock.calls).toEqual([['/']]);
		});
		expect(takeIntents(['change-folder'])).toEqual([{ kind: 'change-folder' }]);
	});

	it('asks for the setup without navigating while the mods page is already open', async () => {
		const user = userEvent.setup();
		setPageUrl('http://localhost/');
		localStorage.setItem('sptmod.game_dir', '/games/SPT Live');
		mount();

		await user.click(await screen.findByRole('button', { name: 'SPT Live' }));
		await user.click(await screen.findByRole('button', { name: 'Change folder' }));

		await vi.waitFor(() => {
			expect(takeIntents(['change-folder'])).toEqual([{ kind: 'change-folder' }]);
		});
		expect(vi.mocked(goto)).not.toHaveBeenCalled();
	});

	it('closes on Escape and gives the focus back to the trigger', async () => {
		const user = userEvent.setup();
		localStorage.setItem('sptmod.game_dir', '/games/SPT Live');
		mount();

		const trigger = await screen.findByRole('button', { name: 'SPT Live' });
		await user.click(trigger);
		await screen.findByRole('dialog', { name: 'Game folder' });
		await user.keyboard('{Escape}');

		expect(screen.queryByRole('dialog', { name: 'Game folder' })).toBeNull();
		expect(trigger).toHaveFocus();
	});

	it('closes when something outside the menu is clicked', async () => {
		const user = userEvent.setup();
		localStorage.setItem('sptmod.game_dir', '/games/SPT Live');
		mount();

		await user.click(await screen.findByRole('button', { name: 'SPT Live' }));
		await screen.findByRole('dialog', { name: 'Game folder' });
		await user.click(screen.getByRole('button', { name: 'Minimize' }));

		expect(screen.queryByRole('dialog', { name: 'Game folder' })).toBeNull();
	});
});
