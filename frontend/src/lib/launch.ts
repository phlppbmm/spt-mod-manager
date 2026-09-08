import type { LaunchStatus } from './api/client';

export const STOP_SERVER_KEY = 'sptmod.stop_server_on_exit';

const RUNNING_POLL_MS = 3000;
const IDLE_POLL_MS = 10000;

export interface LaunchAction {
	label: string;
	disabled: boolean;
	mode: 'launch' | 'stop';
	hint: string;
}

export function loadStopServerOnExit(): boolean {
	return localStorage.getItem(STOP_SERVER_KEY) !== 'false';
}

export function saveStopServerOnExit(value: boolean): void {
	localStorage.setItem(STOP_SERVER_KEY, value ? 'true' : 'false');
}

export function isGameUp(status: LaunchStatus | null): boolean {
	return status !== null && (status.client_running || status.server_running);
}

export function bannerText(status: LaunchStatus | null): string {
	return isGameUp(status) ? 'The game is running, mod changes are paused' : '';
}

export function launchAction(
	status: LaunchStatus | null,
	profileId: string,
	busy: boolean,
	hostLabel = ''
): LaunchAction {
	if (status !== null && isGameUp(status) && status.profile_id === profileId) {
		return {
			label: 'Stop',
			disabled: busy,
			mode: 'stop',
			hint:
				hostLabel === ''
					? 'Closes the game, and the SPT server if sptmod started it.'
					: "Closes the game. The host's server keeps running."
		};
	}
	const label = hostLabel === '' ? 'Launch' : `Connect to ${hostLabel}`;
	if (busy) {
		return {
			label: hostLabel === '' ? 'Starting…' : 'Connecting…',
			disabled: true,
			mode: 'launch',
			hint: 'A launch is running.'
		};
	}
	if (isGameUp(status)) {
		return { label, disabled: true, mode: 'launch', hint: 'The game is already running.' };
	}
	return { label, disabled: false, mode: 'launch', hint: '' };
}

export function nextPollDelay(status: LaunchStatus | null): number {
	return isGameUp(status) ? RUNNING_POLL_MS : IDLE_POLL_MS;
}
