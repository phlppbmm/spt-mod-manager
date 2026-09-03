import { describe, expect, it, vi } from 'vitest';
import { invoke } from '@tauri-apps/api/core';
import { listFavorites, listMods, setFavorite } from './client';

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

describe('listFavorites', () => {
	it('calls rpc with name list_favorites and an empty payload', async () => {
		invokeMock.mockResolvedValue({ mod_ids: [7] });

		const result = await listFavorites();

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'list_favorites',
			payload: {}
		});
		expect(result).toEqual({ mod_ids: [7] });
	});
});

describe('setFavorite', () => {
	it('sends mod_id and favorite, without a favorites_path', async () => {
		invokeMock.mockResolvedValue({ mod_ids: [7] });

		const result = await setFavorite(7, true);

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'set_favorite',
			payload: { mod_id: 7, favorite: true }
		});
		expect(result).toEqual({ mod_ids: [7] });
	});
});
