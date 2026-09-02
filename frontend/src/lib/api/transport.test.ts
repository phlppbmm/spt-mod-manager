import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, API_BASE_URL, call, subscribe, type ErrorDetails } from './transport';

function jsonResponse(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

const sampleDetails: ErrorDetails = {
	conflicts: [],
	conflicting_paths: [],
	name: 'some-mod',
	candidates: [],
	dependency: '',
	reason: 'not compatible',
	api_code: '',
	api_status: 0
};

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('call', () => {
	it('returns the parsed JSON body on a 2xx response', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { mods: ['a', 'b'] }));
		vi.stubGlobal('fetch', fetchMock);

		const result = await call<{ mods: string[] }>('list_mods', { path: '/game' });

		expect(result).toEqual({ mods: ['a', 'b'] });
	});

	it('posts to /api/<name> with the JSON-encoded payload', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
		vi.stubGlobal('fetch', fetchMock);

		await call('list_mods', { path: '/game', include_all: true });

		expect(fetchMock).toHaveBeenCalledWith(`${API_BASE_URL}/api/list_mods`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ path: '/game', include_all: true })
		});
	});

	it('sends an empty object body when no payload is given', async () => {
		const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
		vi.stubGlobal('fetch', fetchMock);

		await call('status');

		expect(fetchMock).toHaveBeenCalledWith(
			`${API_BASE_URL}/api/status`,
			expect.objectContaining({ body: JSON.stringify({}) })
		);
	});

	it('rejects with an ApiError built from the error body on a well-formed failure response', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				jsonResponse(409, {
					error: 'Mod conflicts with another mod.',
					code: 'conflict',
					details: sampleDetails
				})
			)
		);

		const promise = call('remove_mod', { path: '/game', name: 'x' });

		await expect(promise).rejects.toBeInstanceOf(ApiError);
		await expect(promise).rejects.toMatchObject({
			message: 'Mod conflicts with another mod.',
			code: 'conflict',
			details: sampleDetails
		});
	});

	it('rejects with an http_<status> code when the error body has no JSON content', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response('not json', { status: 500, headers: { 'content-type': 'text/plain' } })
			)
		);

		await expect(call('status', { path: '/game' })).rejects.toMatchObject({
			code: 'http_500'
		});
	});

	it('rejects with an http_<status> code when the error body is JSON but not shaped like ErrorResponse', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(400, { unexpected: 'shape' })));

		await expect(call('status', { path: '/game' })).rejects.toMatchObject({
			code: 'http_400'
		});
	});

	it('rejects with an http_<status> code when the error body is JSON but missing the code field', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(jsonResponse(422, { error: 'Bad request' }))
		);

		await expect(call('status', { path: '/game' })).rejects.toMatchObject({
			code: 'http_422'
		});
	});

	it('rejects with code "unreachable" and appends the failure cause when fetch itself rejects', async () => {
		vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));

		const promise = call('status', { path: '/game' });

		await expect(promise).rejects.toBeInstanceOf(ApiError);
		await expect(promise).rejects.toMatchObject({
			code: 'unreachable',
			message: `Cannot reach the sptmod server at ${API_BASE_URL}: network down`
		});
	});

	it('stringifies a non-Error rejection cause into the message', async () => {
		vi.stubGlobal('fetch', vi.fn().mockRejectedValue('offline'));

		await expect(call('status', { path: '/game' })).rejects.toMatchObject({
			code: 'unreachable',
			message: `Cannot reach the sptmod server at ${API_BASE_URL}: offline`
		});
	});
});

class FakeEventSource {
	static instances: FakeEventSource[] = [];
	url: string;
	closed = false;
	private listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();

	constructor(url: string) {
		this.url = url;
		FakeEventSource.instances.push(this);
	}

	addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
		if (!this.listeners.has(type)) {
			this.listeners.set(type, new Set());
		}
		this.listeners.get(type)!.add(listener);
	}

	removeEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
		this.listeners.get(type)?.delete(listener);
	}

	close(): void {
		this.closed = true;
	}

	emit(type: string, data: unknown): void {
		const message = new MessageEvent(type, { data: JSON.stringify(data) });
		for (const listener of this.listeners.get(type) ?? []) {
			listener(message);
		}
	}

	listenerCount(type: string): number {
		return this.listeners.get(type)?.size ?? 0;
	}
}

describe('subscribe', () => {
	afterEach(() => {
		FakeEventSource.instances = [];
	});

	it('opens an EventSource against the events endpoint', async () => {
		vi.stubGlobal('EventSource', FakeEventSource);

		await subscribe('op-1', () => {});

		expect(FakeEventSource.instances).toHaveLength(1);
		expect(FakeEventSource.instances[0].url).toBe(`${API_BASE_URL}/api/events`);
	});

	it('invokes the handler only for events whose operation_id matches', async () => {
		vi.stubGlobal('EventSource', FakeEventSource);
		const handler = vi.fn();

		await subscribe('op-1', handler);
		const source = FakeEventSource.instances[0];

		source.emit('message', { operation_id: 'op-2', step: 'x', percent: 10, message: 'other' });
		source.emit('message', { operation_id: 'op-1', step: 'copy', percent: 50, message: 'copying' });

		expect(handler).toHaveBeenCalledTimes(1);
		expect(handler).toHaveBeenCalledWith({
			operation_id: 'op-1',
			step: 'copy',
			percent: 50,
			message: 'copying'
		});
	});

	it('unsubscribe removes the listener and closes the source', async () => {
		vi.stubGlobal('EventSource', FakeEventSource);
		const handler = vi.fn();

		const unsubscribe = await subscribe('op-1', handler);
		const source = FakeEventSource.instances[0];
		expect(source.listenerCount('message')).toBe(1);

		unsubscribe();

		expect(source.listenerCount('message')).toBe(0);
		expect(source.closed).toBe(true);

		source.emit('message', { operation_id: 'op-1', step: 'x', percent: 100, message: 'done' });
		expect(handler).not.toHaveBeenCalled();
	});
});
