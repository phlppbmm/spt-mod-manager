/**
 * Component tests for the command palette.
 *
 * Setup: the palette is mounted with a route, a theme and a theme toggle, and
 * opened through the keyboard the way a player opens it. Assertions read the
 * rendered options, the navigation the stubbed `goto` received and the intents
 * the shared queue holds afterwards. Reason: the palette is keyboard-only, and
 * neither its focus handling nor the route-then-intent order it produces is
 * visible from the unit tests of `commands.ts` and `palettekeys.ts`.
 */

import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { goto } from '$app/navigation';

import { clearFilter, getFilter, setFilter } from '$lib/filterstate';
import { takeIntents } from '$lib/intents';
import { setCommandMods, setGameDir } from '$lib/shellstate';
import { invalidate } from '$lib/viewcache';

import CommandPalette from '$lib/components/CommandPalette.svelte';
import { profileSummary } from '../support/factories';
import { rpc } from '../support/rpc';

vi.mock('$lib/api/transport', async (importOriginal) => {
	const { transportMock } = await import('../support/rpc');
	return transportMock(await importOriginal<typeof import('$lib/api/transport')>());
});

vi.mock('$app/navigation', () => ({ goto: vi.fn(() => Promise.resolve()) }));

const GAME_DIR = '/games/SPT';

interface Mounted {
	toggles: number;
}

function mount(pathname = '/'): Mounted {
	const mounted: Mounted = { toggles: 0 };
	render(CommandPalette, {
		props: {
			pathname,
			theme: 'dark' as const,
			onToggleTheme: () => {
				mounted.toggles += 1;
			}
		}
	});
	return mounted;
}

async function openPalette(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
	await user.keyboard('{Control>}k{/Control}');
	return screen.findByRole('dialog', { name: 'Commands' });
}

/** The titles of the rendered options, in the order the list shows them. */
function optionTitles(): string[] {
	return screen.getAllByRole('option').map((option) => option.textContent?.trim() ?? '');
}

function activeOption(): string {
	const selected = screen
		.getAllByRole('option')
		.find((option) => option.getAttribute('aria-selected') === 'true');
	return selected?.textContent?.trim() ?? '';
}

afterEach(() => {
	rpc.reset();
	invalidate(GAME_DIR);
	clearFilter();
	setGameDir(null);
	setCommandMods([]);
	takeIntents(['snapshot', 'launch', 'sync', 'update-all', 'change-folder', 'reveal-mod']);
	vi.mocked(goto).mockReset();
});

describe('opening and closing', () => {
	it('opens on Ctrl+K with the search field focused', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);

		expect(screen.getByRole('textbox', { name: 'Search commands' })).toHaveFocus();
	});

	it('closes again on a second Ctrl+K', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);
		await user.keyboard('{Control>}k{/Control}');

		expect(screen.queryByRole('dialog', { name: 'Commands' })).toBeNull();
	});

	it('returns the focus to the element that had it before', async () => {
		const user = userEvent.setup();
		const before = document.createElement('button');
		before.textContent = 'Elsewhere';
		document.body.append(before);
		before.focus();
		mount();

		await openPalette(user);
		await user.keyboard('{Escape}');

		expect(screen.queryByRole('dialog', { name: 'Commands' })).toBeNull();
		expect(before).toHaveFocus();
		before.remove();
	});

	it('closes when the backdrop beside the palette is clicked', async () => {
		const user = userEvent.setup();
		mount();

		const dialog = await openPalette(user);
		const backdrop = dialog.parentElement;
		if (backdrop === null) {
			throw new Error('the palette has no backdrop');
		}
		await user.click(backdrop);

		expect(screen.queryByRole('dialog', { name: 'Commands' })).toBeNull();
	});
});

describe('the offered commands', () => {
	it('offers the four pages while no game directory is known', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);

		expect(optionTitles()).toEqual([
			'Mods',
			'Profiles',
			'History',
			'Settings',
			'Clear filters',
			'Switch to light mode'
		]);
	});

	it('adds the game actions and the profiles once a game directory is known', async () => {
		const user = userEvent.setup();
		setGameDir(GAME_DIR);
		rpc.returns('list_profiles', [profileSummary({ profile_id: 'aaa', nickname: 'Nikita' })]);
		mount();

		await openPalette(user);

		expect(await screen.findByText('Launch Nikita')).toBeInTheDocument();
		expect(rpc.payloads('list_profiles')).toEqual([{ path: GAME_DIR }]);
		expect(optionTitles()).toContain('Sync catalog');
		expect(optionTitles()).toContain('Change folder');
	});

	it('offers the mods the mods page published, with their author', async () => {
		const user = userEvent.setup();
		setCommandMods([{ id: 47, name: 'Alpha', author: 'Ada' }]);
		mount();

		await openPalette(user);

		const option = screen.getByRole('option', { name: 'Alpha Ada' });
		expect(within(option).getByText('Ada')).toBeInTheDocument();
	});

	it('narrows the list to what was typed', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);
		await user.keyboard('settings');

		expect(optionTitles()).toEqual(['Settings']);
	});

	it('says so when nothing matches', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);
		await user.keyboard('zzzzz');

		expect(await screen.findByText('No matches')).toBeInTheDocument();
	});
});

describe('keyboard navigation', () => {
	it('runs the highlighted command on Enter', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);
		await user.keyboard('settings{Enter}');

		expect(vi.mocked(goto).mock.calls).toEqual([['/settings']]);
		expect(screen.queryByRole('dialog', { name: 'Commands' })).toBeNull();
	});

	it('moves the highlight down and wraps around at the end', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);
		expect(activeOption()).toBe('Mods');

		await user.keyboard('{ArrowDown}');
		expect(activeOption()).toBe('Profiles');

		for (let step = 0; step < 5; step += 1) {
			await user.keyboard('{ArrowDown}');
		}
		expect(activeOption()).toBe('Mods');
	});

	it('wraps to the last command when moving up from the first', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);
		await user.keyboard('{ArrowUp}');

		expect(activeOption()).toBe('Switch to light mode');
	});

	it('starts over at the first command after every keystroke in the search field', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);
		await user.keyboard('{ArrowDown}{ArrowDown}');
		expect(activeOption()).toBe('History');

		await user.keyboard('s');
		expect(activeOption()).toBe(optionTitles()[0]);
	});

	it('runs nothing on Enter while nothing matches', async () => {
		const user = userEvent.setup();
		mount();

		await openPalette(user);
		await user.keyboard('zzzzz{Enter}');

		expect(vi.mocked(goto)).not.toHaveBeenCalled();
		expect(screen.getByRole('dialog', { name: 'Commands' })).toBeInTheDocument();
	});
});

describe('what a command does', () => {
	it('navigates to the route of an intent command before it queues the intent', async () => {
		const user = userEvent.setup();
		setGameDir(GAME_DIR);
		rpc.returns('list_profiles', []);
		mount('/settings');

		await openPalette(user);
		await user.keyboard('Sync catalog{Enter}');

		await vi.waitFor(() => {
			expect(vi.mocked(goto).mock.calls).toEqual([['/']]);
		});
		expect(takeIntents(['sync'])).toEqual([{ kind: 'sync' }]);
	});

	it('queues the intent without navigating when the target route is already open', async () => {
		const user = userEvent.setup();
		setGameDir(GAME_DIR);
		rpc.returns('list_profiles', []);
		mount('/');

		await openPalette(user);
		await user.keyboard('Sync catalog{Enter}');

		await vi.waitFor(() => {
			expect(takeIntents(['sync'])).toEqual([{ kind: 'sync' }]);
		});
		expect(vi.mocked(goto)).not.toHaveBeenCalled();
	});

	it('queues the launch of the profile the command names', async () => {
		const user = userEvent.setup();
		setGameDir(GAME_DIR);
		rpc.returns('list_profiles', [profileSummary({ profile_id: 'aaa', nickname: 'Nikita' })]);
		mount('/profile');

		await openPalette(user);
		await screen.findByText('Launch Nikita');
		await user.keyboard('Launch Nikita{Enter}');

		await vi.waitFor(() => {
			expect(takeIntents(['launch'])).toEqual([{ kind: 'launch', profileId: 'aaa' }]);
		});
	});

	it('goes to the mods page and asks it to reveal the chosen mod', async () => {
		const user = userEvent.setup();
		setCommandMods([{ id: 47, name: 'Alpha', author: 'Ada' }]);
		mount('/history');

		await openPalette(user);
		await user.keyboard('Alpha{Enter}');

		await vi.waitFor(() => {
			expect(vi.mocked(goto).mock.calls).toEqual([['/']]);
		});
		expect(takeIntents(['reveal-mod'])).toEqual([{ kind: 'reveal-mod', modId: 47 }]);
	});

	it('resets the shared filter without leaving the page', async () => {
		const user = userEvent.setup();
		setFilter({ chips: new Set(['featured']), category: 'Weapons', search: 'alpha' });
		mount('/');

		await openPalette(user);
		await user.keyboard('Clear filters{Enter}');

		expect(getFilter()).toEqual({
			chips: new Set(['compatible']),
			category: null,
			search: ''
		});
		expect(vi.mocked(goto)).not.toHaveBeenCalled();
	});

	it('hands the theme switch to the shell', async () => {
		const user = userEvent.setup();
		const mounted = mount();

		await openPalette(user);
		await user.keyboard('Switch to light{Enter}');

		expect(mounted.toggles).toBe(1);
	});

	it('keeps working when the profiles of the game directory cannot be read', async () => {
		const user = userEvent.setup();
		setGameDir(GAME_DIR);
		rpc.rejects('list_profiles', new Error('The folder is gone.'));
		mount();

		await openPalette(user);

		expect(optionTitles()).toContain('Sync catalog');
		expect(optionTitles().some((title) => title.startsWith('Launch'))).toBe(false);
	});
});
