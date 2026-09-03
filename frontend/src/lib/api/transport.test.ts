import { describe, expect, it, vi } from 'vitest';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { ApiError, call, subscribe, type ErrorDetails } from './transport';

vi.mock('@tauri-apps/api/core', () => ({
	invoke: vi.fn()
}));

vi.mock('@tauri-apps/api/event', () => ({
	listen: vi.fn()
}));

const invokeMock = vi.mocked(invoke);
const listenMock = vi.mocked(listen);

const sampleDetails: ErrorDetails = {
	conflicts: [],
	conflicting_paths: [],
	residues: [],
	installed: [],
	name: 'some-mod',
	candidates: [],
	dependency: '',
	reason: 'not compatible',
	api_code: '',
	api_status: 0
};

describe('call', () => {
	it('returns the resolved value of invoke as-is', async () => {
		invokeMock.mockResolvedValue({ mods: ['a', 'b'] });

		const result = await call<{ mods: string[] }>('list_mods', { path: '/game' });

		expect(result).toEqual({ mods: ['a', 'b'] });
	});

	it('invokes the "rpc" command with name and payload forwarded', async () => {
		invokeMock.mockResolvedValue({});

		await call('list_mods', { path: '/game', include_all: true });

		expect(invokeMock).toHaveBeenCalledWith('rpc', {
			name: 'list_mods',
			payload: { path: '/game', include_all: true }
		});
	});

	it('defaults the payload to an empty object when none is given', async () => {
		invokeMock.mockResolvedValue({});

		await call('status');

		expect(invokeMock).toHaveBeenCalledWith('rpc', { name: 'status', payload: {} });
	});

	it('rejects with an ApiError built from a structured error response', async () => {
		invokeMock.mockRejectedValue({
			error: 'Mod conflicts with another mod.',
			code: 'conflict',
			details: sampleDetails
		});

		const promise = call('remove_mod', { path: '/game', name: 'x' });

		await expect(promise).rejects.toBeInstanceOf(ApiError);
		await expect(promise).rejects.toMatchObject({
			message: 'Mod conflicts with another mod.',
			code: 'conflict',
			details: sampleDetails
		});
	});

	it('leaves details undefined when the structured error response omits it', async () => {
		invokeMock.mockRejectedValue({ error: 'Not found', code: 'not_found' });

		const promise = call('status', { path: '/game' });

		await expect(promise).rejects.toMatchObject({
			message: 'Not found',
			code: 'not_found',
			details: undefined
		});
	});

	it('rejects with code "unreachable" and the stringified cause for an Error rejection', async () => {
		invokeMock.mockRejectedValue(new Error('network down'));

		const promise = call('status', { path: '/game' });

		await expect(promise).rejects.toBeInstanceOf(ApiError);
		await expect(promise).rejects.toMatchObject({
			code: 'unreachable',
			message: 'Error: network down'
		});
	});

	it('rejects with code "unreachable" for a plain string rejection', async () => {
		invokeMock.mockRejectedValue('offline');

		await expect(call('status', { path: '/game' })).rejects.toMatchObject({
			code: 'unreachable',
			message: 'offline'
		});
	});

	it('rejects with code "unreachable" for a plain object rejection without a code field', async () => {
		invokeMock.mockRejectedValue({ unexpected: 'shape' });

		await expect(call('status', { path: '/game' })).rejects.toMatchObject({
			code: 'unreachable',
			message: '[object Object]'
		});
	});
});

describe('subscribe', () => {
	it('registers a listener on the "progress" event exactly once', async () => {
		listenMock.mockResolvedValue(() => {});

		await subscribe('op-1', () => {});

		expect(listenMock).toHaveBeenCalledTimes(1);
		expect(listenMock).toHaveBeenCalledWith('progress', expect.any(Function));
	});

	it('forwards only events whose payload operation_id matches, passing the payload itself', async () => {
		let emit: (message: { payload: ReturnType<typeof structuredClone> }) => void = () => {};
		listenMock.mockImplementation(async (_event, callback) => {
			emit = callback as typeof emit;
			return () => {};
		});
		const handler = vi.fn();

		await subscribe('op-1', handler);

		emit({ payload: { operation_id: 'op-2', step: 'x', percent: 10, message: 'other' } });
		emit({ payload: { operation_id: 'op-1', step: 'copy', percent: 50, message: 'copying' } });

		expect(handler).toHaveBeenCalledTimes(1);
		expect(handler).toHaveBeenCalledWith({
			operation_id: 'op-1',
			step: 'copy',
			percent: 50,
			message: 'copying'
		});
	});

	it('resolves to the unlisten function returned by listen', async () => {
		const unlisten = vi.fn();
		listenMock.mockResolvedValue(unlisten);

		const returned = await subscribe('op-1', () => {});
		returned();

		expect(returned).toBe(unlisten);
		expect(unlisten).toHaveBeenCalledTimes(1);
	});
});
