/**
 * Window chrome for the undecorated Tauri window. Alongside `api/transport.ts`
 * this is the only module allowed to talk to the Tauri API, and it never calls
 * the `rpc` command.
 */

import { getCurrentWindow } from '@tauri-apps/api/window';

export function minimizeWindow(): Promise<void> {
	return getCurrentWindow().minimize();
}

export function toggleMaximizeWindow(): Promise<void> {
	return getCurrentWindow().toggleMaximize();
}

export function closeWindow(): Promise<void> {
	return getCurrentWindow().close();
}

export function isWindowMaximized(): Promise<boolean> {
	return getCurrentWindow().isMaximized();
}
