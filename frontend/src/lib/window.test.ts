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

describe('minimizeWindow', () => {
	it('minimizes the current window', async () => {
		appWindow.minimize.mockResolvedValue(undefined);

		await minimizeWindow();

		expect(appWindow.minimize).toHaveBeenCalledTimes(1);
	});
});

describe('toggleMaximizeWindow', () => {
	it('toggles the maximized state of the current window', async () => {
		appWindow.toggleMaximize.mockResolvedValue(undefined);

		await toggleMaximizeWindow();

		expect(appWindow.toggleMaximize).toHaveBeenCalledTimes(1);
	});
});

describe('closeWindow', () => {
	it('closes the current window', async () => {
		appWindow.close.mockResolvedValue(undefined);

		await closeWindow();

		expect(appWindow.close).toHaveBeenCalledTimes(1);
	});
});

describe('isWindowMaximized', () => {
	it.each([true, false])('reports the maximized state %s', async (maximized) => {
		appWindow.isMaximized.mockResolvedValue(maximized);

		await expect(isWindowMaximized()).resolves.toBe(maximized);
	});
});
