import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getCurrentWindow } from '@tauri-apps/api/window';
import {
	closeWindow,
	isWindowMaximized,
	minimizeWindow,
	toggleMaximizeWindow
} from './window';

vi.mock('@tauri-apps/api/window', () => ({
	getCurrentWindow: vi.fn()
}));

const appWindow = {
	minimize: vi.fn(),
	toggleMaximize: vi.fn(),
	close: vi.fn(),
	isMaximized: vi.fn()
};

beforeEach(() => {
	vi.clearAllMocks();
	vi.mocked(getCurrentWindow).mockReturnValue(
		appWindow as unknown as ReturnType<typeof getCurrentWindow>
	);
});

describe('window controls', () => {
	it.each([
		{ id: 'minimize', call: minimizeWindow, spy: appWindow.minimize },
		{ id: 'toggleMaximize', call: toggleMaximizeWindow, spy: appWindow.toggleMaximize },
		{ id: 'close', call: closeWindow, spy: appWindow.close }
	])('$id acts on the current window', async ({ call, spy }) => {
		spy.mockResolvedValue(undefined);

		await call();

		expect(spy).toHaveBeenCalledTimes(1);
	});
});

describe('isWindowMaximized', () => {
	it.each([true, false])('reports the maximized state %s', async (maximized) => {
		appWindow.isMaximized.mockResolvedValue(maximized);

		await expect(isWindowMaximized()).resolves.toBe(maximized);
	});
});
