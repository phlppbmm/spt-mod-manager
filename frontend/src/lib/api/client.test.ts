import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_BASE_URL } from './transport';
import { listMods } from './client';

function jsonResponse(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('listMods', () => {
	it('posts to /api/list_mods with the snake_case payload and returns the typed body', async () => {
		const mods = [{ name: 'mod-a', version: '1.0.0' }];
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, mods));
		vi.stubGlobal('fetch', fetchMock);

		const result = await listMods('/game', true);

		expect(fetchMock).toHaveBeenCalledWith(`${API_BASE_URL}/api/list_mods`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ path: '/game', include_all: true })
		});
		expect(result).toEqual(mods);
	});

	it('defaults include_all to false when not given', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, []));
		vi.stubGlobal('fetch', fetchMock);

		await listMods('/game');

		expect(fetchMock).toHaveBeenCalledWith(
			`${API_BASE_URL}/api/list_mods`,
			expect.objectContaining({ body: JSON.stringify({ path: '/game', include_all: false }) })
		);
	});
});
