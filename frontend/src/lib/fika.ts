export type FikaRole = 'none' | 'host' | 'client';

export interface FikaRoleOption {
	id: FikaRole;
	label: string;
	hint: string;
}

const DEFAULT_PORT = 6969;
const DEFAULT_PORT_SUFFIX = `:${DEFAULT_PORT}`;

export const FIKA_ROLES: FikaRoleOption[] = [
	{ id: 'none', label: 'No Fika', hint: 'Play alone on the server of this machine.' },
	{ id: 'host', label: 'I host', hint: 'Your machine runs the server the other players join.' },
	{
		id: 'client',
		label: 'I join a host',
		hint: 'You play on another player’s server and need their address.'
	}
];

/**
 * The address as the one form the launch uses, or null when it cannot be used.
 *
 * 1. Whitespace around the address is dropped; an empty address yields `''`.
 * 2. An address with a scheme keeps it when it is `http` or `https` (any case);
 *    anything else is unusable. Without a scheme, `https` is assumed.
 * 3. The rest is unusable with an empty host, with a user name, a query or a
 *    fragment, with a path other than `''` or `'/'`, with a port outside
 *    1-65535, or with whitespace anywhere inside. Brackets hold an IPv6
 *    address and nothing else.
 * 4. The result is `scheme://host:port` with a lower-case host, brackets kept
 *    around an IPv6 address, and port 6969 when none was given.
 *
 * `192.168.1.20` becomes `https://192.168.1.20:6969`, `192.168.1.20:7000`
 * becomes `https://192.168.1.20:7000`, `http://pc.local` becomes
 * `http://pc.local:6969`, `https://[fe80::1]:6969/` becomes
 * `https://[fe80::1]:6969`.
 */
export function canonicalBaseUrl(address: string): string | null {
	const text = address.trim();
	if (text === '') {
		return '';
	}
	if (/\s/.test(text)) {
		return null;
	}
	const mark = text.indexOf('://');
	let scheme = 'https';
	let rest = text;
	if (mark >= 0) {
		scheme = text.slice(0, mark).toLowerCase();
		rest = text.slice(mark + 3);
		if (scheme !== 'http' && scheme !== 'https') {
			return null;
		}
	}
	if (rest.includes('@') || rest.includes('?') || rest.includes('#')) {
		return null;
	}
	const slash = rest.indexOf('/');
	if (slash >= 0) {
		if (rest.slice(slash) !== '/') {
			return null;
		}
		rest = rest.slice(0, slash);
	}
	return authority(scheme, rest);
}

/** The host of a base URL as it is shown: `pc.local`, or `pc.local:7000` off the default port. */
export function hostLabel(baseUrl: string): string {
	const rest = baseUrl.replace(/^https?:\/\//, '');
	return rest.endsWith(DEFAULT_PORT_SUFFIX) ? rest.slice(0, -DEFAULT_PORT_SUFFIX.length) : rest;
}

/** What is wrong with the address for this role, empty when the role can be saved. */
export function addressError(role: FikaRole, address: string): string {
	if (role === 'none') {
		return '';
	}
	const base = canonicalBaseUrl(address);
	if (base === '') {
		return role === 'client' ? 'A Fika client needs the address of the host.' : '';
	}
	return base === null
		? 'This address cannot be used. Enter something like 192.168.1.20 or 192.168.1.20:7000.'
		: '';
}

function authority(scheme: string, text: string): string | null {
	const split = hostAndPort(text);
	if (split === null) {
		return null;
	}
	const [host, portText] = split;
	if (host === '') {
		return null;
	}
	if (portText === '') {
		return `${scheme}://${host.toLowerCase()}${DEFAULT_PORT_SUFFIX}`;
	}
	if (!/^[0-9]+$/.test(portText)) {
		return null;
	}
	const port = Number(portText);
	return port < 1 || port > 65535 ? null : `${scheme}://${host.toLowerCase()}:${port}`;
}

function hostAndPort(text: string): [string, string] | null {
	if (text.startsWith('[')) {
		const end = text.indexOf(']');
		if (end < 0 || !isIpv6(text.slice(1, end))) {
			return null;
		}
		const tail = text.slice(end + 1);
		if (tail === '') {
			return [text, ''];
		}
		return tail.startsWith(':') ? [text.slice(0, end + 1), tail.slice(1)] : null;
	}
	const colon = text.indexOf(':');
	return colon < 0 ? [text, ''] : [text.slice(0, colon), text.slice(colon + 1)];
}

function isIpv6(host: string): boolean {
	return host.includes(':') && /^[0-9a-f:.]+$/i.test(host);
}
