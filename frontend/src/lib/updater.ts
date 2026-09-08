/**
 * Self-update boundary. Alongside `api/transport.ts` and `window.ts` this is
 * one of the few modules allowed to talk to the Tauri API; everything the UI
 * decides lives in `updatestate.ts`.
 */

import { getVersion } from '@tauri-apps/api/app';
import { relaunch } from '@tauri-apps/plugin-process';
import { check, type DownloadEvent, type Update } from '@tauri-apps/plugin-updater';
import type { DownloadProgress } from './updatestate';

export interface AvailableUpdate {
	version: string;
	notes: string;
}

let pending: Update | null = null;

export function appVersion(): Promise<string> {
	return getVersion();
}

export async function checkForUpdate(): Promise<AvailableUpdate | null> {
	const update = await check();
	await release();
	pending = update;
	if (update === null) {
		return null;
	}
	return { version: update.version, notes: update.body ?? '' };
}

export async function installUpdate(
	onProgress: (progress: DownloadProgress) => void
): Promise<void> {
	const update = pending;
	if (update === null) {
		throw new Error('Check for updates first.');
	}
	await update.downloadAndInstall((event) => {
		onProgress(toProgress(event));
	});
	await release();
}

/**
 * An `Update` owns a handle on the Rust side; dropping the reference without
 * closing it leaks that handle. Closing itself must never turn a successful
 * check or install into a failure.
 */
async function release(): Promise<void> {
	const update = pending;
	pending = null;
	if (update === null) {
		return;
	}
	await update.close().catch(() => undefined);
}

export function restartApp(): Promise<void> {
	return relaunch();
}

function toProgress(event: DownloadEvent): DownloadProgress {
	if (event.event === 'Started') {
		return { kind: 'started', total: event.data.contentLength ?? null };
	}
	if (event.event === 'Progress') {
		return { kind: 'chunk', bytes: event.data.chunkLength };
	}
	return { kind: 'finished' };
}
