import { describe, expect, it, vi } from 'vitest';
import { invoke } from '@tauri-apps/api/core';
import { listMods } from './client';

vi.mock('@tauri-apps/api/core', () => ({
	invoke: vi.fn()
}));

const invokeMock = vi.mocked(invoke);

describe('listMods', () => {
	it('calls rpc with the snake_case payload and returns the typed body', async () => {
		const mods = [{ name: 'mod-a', version: '1.0.0' }];
		invokeMock.mockResolvedValue(mods);

		const result = await listMods('/game', true);

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'list_mods',
			payload: { path: '/game', include_all: true }
		});
		expect(result).toEqual(mods);
	});

	it('defaults include_all to false when not given', async () => {
		invokeMock.mockResolvedValue([]);

		await listMods('/game');

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'list_mods',
			payload: { path: '/game', include_all: false }
		});
	});
});
