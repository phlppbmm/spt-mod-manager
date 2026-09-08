import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getVersion } from '@tauri-apps/api/app';
import { relaunch } from '@tauri-apps/plugin-process';
import { check, type DownloadEvent, type Update } from '@tauri-apps/plugin-updater';
import type { DownloadProgress } from './updatestate';

// Hoisted so that the same spies survive `vi.resetModules()`, which the tests
// use to get an updater module that has not seen a check yet.
const tauri = vi.hoisted(() => ({
	getVersion: vi.fn(),
	relaunch: vi.fn(),
	check: vi.fn()
}));

vi.mock('@tauri-apps/api/app', () => ({ getVersion: tauri.getVersion }));
vi.mock('@tauri-apps/plugin-process', () => ({ relaunch: tauri.relaunch }));
vi.mock('@tauri-apps/plugin-updater', () => ({ check: tauri.check }));

/** A fresh copy of the module, so the remembered update handle starts empty. */
async function loadUpdater(): Promise<typeof import('./updater')> {
	vi.resetModules();
	return import('./updater');
}

interface FakeUpdate {
	version: string;
	body?: string;
	downloadAndInstall: ReturnType<typeof vi.fn>;
	close: ReturnType<typeof vi.fn>;
}

function fakeUpdate(overrides: Partial<FakeUpdate> = {}): FakeUpdate {
	return {
		version: '2.4.0',
		body: 'Fixes the launcher',
		downloadAndInstall: vi.fn().mockResolvedValue(undefined),
		close: vi.fn().mockResolvedValue(undefined),
		...overrides
	};
}

function offer(update: FakeUpdate | null): void {
	vi.mocked(check).mockResolvedValue(update as unknown as Update | null);
}

beforeEach(() => {
	vi.clearAllMocks();
});

describe('appVersion', () => {
	it('reports the version the Tauri app announces', async () => {
		const { appVersion } = await loadUpdater();
		vi.mocked(getVersion).mockResolvedValue('0.4.7');

		await expect(appVersion()).resolves.toBe('0.4.7');
	});
});

describe('checkForUpdate', () => {
	it('reports the offered version and its release notes', async () => {
		const { checkForUpdate } = await loadUpdater();
		offer(fakeUpdate({ version: '3.1.4', body: 'Adds the profile switcher' }));

		await expect(checkForUpdate()).resolves.toEqual({
			version: '3.1.4',
			notes: 'Adds the profile switcher'
		});
	});

	it('reports empty notes when the release carries no description', async () => {
		const { checkForUpdate } = await loadUpdater();
		offer(fakeUpdate({ body: undefined }));

		await expect(checkForUpdate()).resolves.toEqual({ version: '2.4.0', notes: '' });
	});

	it('reports nothing when the feed offers no newer release', async () => {
		const { checkForUpdate } = await loadUpdater();
		offer(null);

		await expect(checkForUpdate()).resolves.toBe(null);
	});

	it('lets a failing check surface to the caller', async () => {
		const { checkForUpdate } = await loadUpdater();
		vi.mocked(check).mockRejectedValue(new Error('the release feed is unreachable'));

		await expect(checkForUpdate()).rejects.toThrow('the release feed is unreachable');
	});
});

describe('installUpdate', () => {
	it('refuses to install before anything has been checked', async () => {
		const { installUpdate } = await loadUpdater();
		const onProgress = vi.fn();

		await expect(installUpdate(onProgress)).rejects.toThrow('Check for updates first.');
		expect(onProgress).not.toHaveBeenCalled();
	});

	it('refuses to install after a check that found no update', async () => {
		const { checkForUpdate, installUpdate } = await loadUpdater();
		offer(null);
		await checkForUpdate();

		await expect(installUpdate(vi.fn())).rejects.toThrow('Check for updates first.');
	});

	it('installs the update the previous check found', async () => {
		const { checkForUpdate, installUpdate } = await loadUpdater();
		const update = fakeUpdate();
		offer(update);
		await checkForUpdate();

		await installUpdate(vi.fn());

		expect(update.downloadAndInstall).toHaveBeenCalledTimes(1);
	});

	it('translates the plugin download events into the app progress events', async () => {
		const events: DownloadEvent[] = [
			{ event: 'Started', data: { contentLength: 4096 } },
			{ event: 'Started', data: {} },
			{ event: 'Progress', data: { chunkLength: 512 } },
			{ event: 'Finished' }
		];
		const update = fakeUpdate({
			downloadAndInstall: vi.fn(async (onEvent: (event: DownloadEvent) => void) => {
				for (const event of events) {
					onEvent(event);
				}
			})
		});
		const { checkForUpdate, installUpdate } = await loadUpdater();
		offer(update);
		await checkForUpdate();
		const reported: DownloadProgress[] = [];

		await installUpdate((progress) => void reported.push(progress));

		expect(reported).toEqual([
			{ kind: 'started', total: 4096 },
			{ kind: 'started', total: null },
			{ kind: 'chunk', bytes: 512 },
			{ kind: 'finished' }
		]);
	});

	it('lets a failing install surface to the caller', async () => {
		const update = fakeUpdate({
			downloadAndInstall: vi.fn().mockRejectedValue(new Error('invalid updater binary format'))
		});
		const { checkForUpdate, installUpdate } = await loadUpdater();
		offer(update);
		await checkForUpdate();

		await expect(installUpdate(vi.fn())).rejects.toThrow('invalid updater binary format');
	});
});

describe('the remembered update handle', () => {
	it('has nothing to close on the very first check', async () => {
		const { checkForUpdate } = await loadUpdater();
		const update = fakeUpdate();
		offer(update);

		await checkForUpdate();

		expect(update.close).not.toHaveBeenCalled();
	});

	it('closes the handle of the previous check before remembering the new one', async () => {
		const { checkForUpdate } = await loadUpdater();
		const first = fakeUpdate({ version: '2.4.0' });
		const second = fakeUpdate({ version: '2.5.0' });
		offer(first);
		await checkForUpdate();

		offer(second);
		await checkForUpdate();

		expect(first.close).toHaveBeenCalledTimes(1);
		expect(second.close).not.toHaveBeenCalled();
	});

	it('closes the previous handle even when the new check finds nothing', async () => {
		const { checkForUpdate, installUpdate } = await loadUpdater();
		const first = fakeUpdate();
		offer(first);
		await checkForUpdate();

		offer(null);
		await checkForUpdate();

		expect(first.close).toHaveBeenCalledTimes(1);
		await expect(installUpdate(vi.fn())).rejects.toThrow('Check for updates first.');
	});

	it('closes the handle after a successful install and refuses to install twice', async () => {
		const { checkForUpdate, installUpdate } = await loadUpdater();
		const update = fakeUpdate();
		offer(update);
		await checkForUpdate();

		await installUpdate(vi.fn());

		expect(update.close).toHaveBeenCalledTimes(1);
		await expect(installUpdate(vi.fn())).rejects.toThrow('Check for updates first.');
		expect(update.downloadAndInstall).toHaveBeenCalledTimes(1);
	});

	it('keeps the handle after a failing install so the install can be retried', async () => {
		const { checkForUpdate, installUpdate } = await loadUpdater();
		const update = fakeUpdate({
			downloadAndInstall: vi.fn().mockRejectedValue(new Error('the download broke off'))
		});
		offer(update);
		await checkForUpdate();

		await expect(installUpdate(vi.fn())).rejects.toThrow('the download broke off');
		await expect(installUpdate(vi.fn())).rejects.toThrow('the download broke off');

		expect(update.downloadAndInstall).toHaveBeenCalledTimes(2);
		expect(update.close).not.toHaveBeenCalled();
	});

	it('completes the check even when closing the previous handle fails', async () => {
		const { checkForUpdate } = await loadUpdater();
		const first = fakeUpdate({
			close: vi.fn().mockRejectedValue(new Error('the handle is already gone'))
		});
		offer(first);
		await checkForUpdate();

		offer(fakeUpdate({ version: '2.5.0', body: 'Adds the profile switcher' }));

		await expect(checkForUpdate()).resolves.toEqual({
			version: '2.5.0',
			notes: 'Adds the profile switcher'
		});
	});

	it('completes the install even when closing the handle fails', async () => {
		const { checkForUpdate, installUpdate } = await loadUpdater();
		const update = fakeUpdate({
			close: vi.fn().mockRejectedValue(new Error('the handle is already gone'))
		});
		offer(update);
		await checkForUpdate();

		await expect(installUpdate(vi.fn())).resolves.toBeUndefined();
	});
});

describe('restartApp', () => {
	it('relaunches the app so the installed version takes over', async () => {
		const { restartApp } = await loadUpdater();
		vi.mocked(relaunch).mockResolvedValue(undefined);

		await restartApp();

		expect(relaunch).toHaveBeenCalledTimes(1);
	});
});
