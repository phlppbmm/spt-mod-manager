import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
	getSubNav,
	onChangeFolder,
	onSubNavSelect,
	onSyncRequest,
	requestChangeFolder,
	requestSync,
	selectSubNav,
	setGameDir,
	setSearchEnabled,
	setSubNav,
	subscribeGameDir,
	subscribeSearchEnabled,
	subscribeSubNav
} from './shellstate';
import type { SubNavItem } from './shellstate';

beforeEach(() => {
	setSubNav([], null);
	setSearchEnabled(false);
	setGameDir(null);
});

describe('subNav', () => {
	it('getSubNav returns an empty item list and no active id before anything was set', () => {
		expect(getSubNav()).toEqual({ items: [], active: null });
	});

	it('a value passed to setSubNav is returned by a later getSubNav call', () => {
		const items: SubNavItem[] = [{ id: 'mods', label: 'Mods' }];

		setSubNav(items, 'mods');

		expect(getSubNav()).toEqual({ items: [{ id: 'mods', label: 'Mods' }], active: 'mods' });
	});

	it('mutating the array passed to setSubNav afterwards does not affect the stored state', () => {
		const items: SubNavItem[] = [{ id: 'mods', label: 'Mods' }];
		setSubNav(items, 'mods');

		items.push({ id: 'profiles', label: 'Profiles' });

		expect(getSubNav().items).toEqual([{ id: 'mods', label: 'Mods' }]);
	});

	it('mutating the array returned by getSubNav does not leak into a later getSubNav call', () => {
		setSubNav([{ id: 'mods', label: 'Mods' }], 'mods');

		const first = getSubNav();
		first.items.push({ id: 'extra', label: 'Extra' });

		expect(getSubNav().items).toEqual([{ id: 'mods', label: 'Mods' }]);
	});

	it('subscribeSubNav calls the listener immediately with the current state', () => {
		setSubNav([{ id: 'mods', label: 'Mods' }], 'mods');
		const listener = vi.fn();

		subscribeSubNav(listener);

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith({
			items: [{ id: 'mods', label: 'Mods' }],
			active: 'mods'
		});
	});

	it('subscribeSubNav notifies listeners with the new state on setSubNav', () => {
		const listener = vi.fn();
		subscribeSubNav(listener);
		listener.mockClear();

		setSubNav([{ id: 'profiles', label: 'Profiles' }], 'profiles');

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith({
			items: [{ id: 'profiles', label: 'Profiles' }],
			active: 'profiles'
		});
	});

	it('mutating a state object delivered to a subscriber does not affect a later getSubNav call', () => {
		let received: { items: SubNavItem[]; active: string | null } | undefined;
		subscribeSubNav((value) => {
			received = value;
		});

		setSubNav([{ id: 'mods', label: 'Mods' }], 'mods');
		received!.items.push({ id: 'extra', label: 'Extra' });
		received!.active = 'extra';

		expect(getSubNav()).toEqual({ items: [{ id: 'mods', label: 'Mods' }], active: 'mods' });
	});

	it('stops delivering notifications to a subscriber after unsubscribe', () => {
		const listener = vi.fn();
		const unsubscribe = subscribeSubNav(listener);
		listener.mockClear();

		unsubscribe();
		setSubNav([{ id: 'mods', label: 'Mods' }], 'mods');

		expect(listener).not.toHaveBeenCalled();
	});
});

describe('searchEnabled', () => {
	it('subscribeSearchEnabled calls the listener immediately with the current value', () => {
		setSearchEnabled(true);
		const listener = vi.fn();

		subscribeSearchEnabled(listener);

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith(true);
	});

	it('setSearchEnabled notifies subscribers of the new value', () => {
		const listener = vi.fn();
		subscribeSearchEnabled(listener);
		listener.mockClear();

		setSearchEnabled(true);

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith(true);
	});

	it('setSearchEnabled notifies subscribers even when the value does not change', () => {
		setSearchEnabled(true);
		const listener = vi.fn();
		subscribeSearchEnabled(listener);
		listener.mockClear();

		setSearchEnabled(true);

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith(true);
	});

	it('stops delivering notifications to a subscriber after unsubscribe', () => {
		const listener = vi.fn();
		const unsubscribe = subscribeSearchEnabled(listener);
		listener.mockClear();

		unsubscribe();
		setSearchEnabled(true);

		expect(listener).not.toHaveBeenCalled();
	});
});

describe('gameDir', () => {
	it('subscribeGameDir calls the listener immediately with the current value', () => {
		setGameDir('/games/foo');
		const listener = vi.fn();

		subscribeGameDir(listener);

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith('/games/foo');
	});

	it('setGameDir notifies subscribers of the new value', () => {
		const listener = vi.fn();
		subscribeGameDir(listener);
		listener.mockClear();

		setGameDir('/games/bar');

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith('/games/bar');
	});

	it('setGameDir can clear a previously set value back to null', () => {
		setGameDir('/games/foo');
		const listener = vi.fn();
		subscribeGameDir(listener);
		listener.mockClear();

		setGameDir(null);

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith(null);
	});

	it('stops delivering notifications to a subscriber after unsubscribe', () => {
		const listener = vi.fn();
		const unsubscribe = subscribeGameDir(listener);
		listener.mockClear();

		unsubscribe();
		setGameDir('/games/foo');

		expect(listener).not.toHaveBeenCalled();
	});
});

describe('subNav selection signal', () => {
	it('does not notify a listener before selectSubNav is called', () => {
		const listener = vi.fn();

		onSubNavSelect(listener);

		expect(listener).not.toHaveBeenCalled();
	});

	it('onSubNavSelect receives the id passed to selectSubNav', () => {
		const listener = vi.fn();
		onSubNavSelect(listener);

		selectSubNav('profiles');

		expect(listener).toHaveBeenCalledTimes(1);
		expect(listener).toHaveBeenCalledWith('profiles');
	});

	it('notifies every registered listener', () => {
		const listenerA = vi.fn();
		const listenerB = vi.fn();
		onSubNavSelect(listenerA);
		onSubNavSelect(listenerB);

		selectSubNav('mods');

		expect(listenerA).toHaveBeenCalledWith('mods');
		expect(listenerB).toHaveBeenCalledWith('mods');
	});

	it('stops notifying a listener after it unsubscribes', () => {
		const listener = vi.fn();
		const unsubscribe = onSubNavSelect(listener);

		unsubscribe();
		selectSubNav('mods');

		expect(listener).not.toHaveBeenCalled();
	});
});

describe('sync request signal', () => {
	it('does not notify a listener before requestSync is called', () => {
		const listener = vi.fn();

		onSyncRequest(listener);

		expect(listener).not.toHaveBeenCalled();
	});

	it('onSyncRequest is called when requestSync is invoked', () => {
		const listener = vi.fn();
		onSyncRequest(listener);

		requestSync();

		expect(listener).toHaveBeenCalledTimes(1);
	});

	it('stops notifying a listener after it unsubscribes', () => {
		const listener = vi.fn();
		const unsubscribe = onSyncRequest(listener);

		unsubscribe();
		requestSync();

		expect(listener).not.toHaveBeenCalled();
	});
});

describe('change folder signal', () => {
	it('does not notify a listener before requestChangeFolder is called', () => {
		const listener = vi.fn();

		onChangeFolder(listener);

		expect(listener).not.toHaveBeenCalled();
	});

	it('onChangeFolder is called when requestChangeFolder is invoked', () => {
		const listener = vi.fn();
		onChangeFolder(listener);

		requestChangeFolder();

		expect(listener).toHaveBeenCalledTimes(1);
	});

	it('stops notifying a listener after it unsubscribes', () => {
		const listener = vi.fn();
		const unsubscribe = onChangeFolder(listener);

		unsubscribe();
		requestChangeFolder();

		expect(listener).not.toHaveBeenCalled();
	});
});
