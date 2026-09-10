import { getThumbnails } from './api/client';

const BATCH_SIZE = 32;
const MAX_CACHED_BYTES = 64 * 1024 * 1024;
const COALESCE_MS = 50;

interface Waiter {
	modId: number;
	resolve: (dataUrl: string | null) => void;
}

const cache = new Map<string, string | null>();
const pending = new Map<string, Promise<string | null>>();
const waiting = new Map<string, Waiter>();
let cachedBytes = 0;
let running = false;

export function loadThumbnail(modId: number, url: string): Promise<string | null> {
	const cached = cache.get(url);
	if (cached !== undefined) {
		remember(url, cached);
		return Promise.resolve(cached);
	}
	const inflight = pending.get(url);
	if (inflight !== undefined) {
		return inflight;
	}
	const promise = new Promise<string | null>((resolve) => waiting.set(url, { modId, resolve }));
	pending.set(url, promise);
	if (!running) {
		running = true;
		setTimeout(() => void flush(), COALESCE_MS);
	}
	return promise;
}

async function flush(): Promise<void> {
	try {
		while (waiting.size > 0) {
			await load([...waiting].slice(0, BATCH_SIZE));
		}
	} finally {
		running = false;
	}
}

async function load(batch: [string, Waiter][]): Promise<void> {
	let loaded: Record<string, string | null> | null = null;
	try {
		loaded = (await getThumbnails(batch.map(([, waiter]) => waiter.modId))).thumbnails;
	} catch {
		loaded = null;
	}
	for (const [url, waiter] of batch) {
		const dataUrl = loaded === null ? null : (loaded[String(waiter.modId)] ?? null);
		waiting.delete(url);
		pending.delete(url);
		if (loaded !== null) {
			remember(url, dataUrl);
		}
		waiter.resolve(dataUrl);
	}
}

function remember(url: string, dataUrl: string | null): void {
	forget(url);
	cache.set(url, dataUrl);
	cachedBytes += dataUrl?.length ?? 0;
	for (const oldest of cache.keys()) {
		if (cachedBytes <= MAX_CACHED_BYTES) {
			return;
		}
		forget(oldest);
	}
}

function forget(url: string): void {
	cachedBytes -= cache.get(url)?.length ?? 0;
	cache.delete(url);
}
