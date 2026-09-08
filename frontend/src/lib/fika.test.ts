import { describe, expect, it } from 'vitest';
import { FIKA_ROLES, addressError, canonicalBaseUrl, hostLabel, type FikaRole } from './fika';

/**
 * `fika.ts` turns what a player types into the one address form the launch
 * uses, labels that address for the launch button, and says what is wrong with
 * it. The tests below use the public functions only, with strings a player can
 * actually type.
 */

interface AddressCase {
	name: string;
	address: string;
	expected: string | null;
}

describe('canonicalBaseUrl', () => {
	const acceptedCases: AddressCase[] = [
		{ name: 'an empty address stays empty', address: '', expected: '' },
		{ name: 'a whitespace-only address counts as empty', address: '   \t ', expected: '' },
		{
			name: 'whitespace around the address is dropped',
			address: '  192.168.1.20  ',
			expected: 'https://192.168.1.20:6969'
		},
		{
			name: 'a bare address gets https and the default port',
			address: '192.168.1.20',
			expected: 'https://192.168.1.20:6969'
		},
		{
			name: 'a bare address keeps its own port',
			address: '192.168.1.20:7000',
			expected: 'https://192.168.1.20:7000'
		},
		{
			name: 'an http address keeps http and gets the default port',
			address: 'http://pc.local',
			expected: 'http://pc.local:6969'
		},
		{
			name: 'an https address keeps https',
			address: 'https://pc.local',
			expected: 'https://pc.local:6969'
		},
		{
			name: 'a scheme in mixed case is accepted',
			address: 'HtTps://pc.local',
			expected: 'https://pc.local:6969'
		},
		{
			name: 'an upper-case host is lower-cased',
			address: 'http://PC.Local:7000',
			expected: 'http://pc.local:7000'
		},
		{
			name: 'a bare root path is dropped',
			address: 'https://pc.local/',
			expected: 'https://pc.local:6969'
		},
		{
			name: 'a colon without a port falls back to the default port',
			address: 'pc.local:',
			expected: 'https://pc.local:6969'
		},
		{
			name: 'the lowest valid port is kept',
			address: 'pc.local:1',
			expected: 'https://pc.local:1'
		},
		{
			name: 'the highest valid port is kept',
			address: 'pc.local:65535',
			expected: 'https://pc.local:65535'
		},
		{
			name: 'an explicitly given 443 is kept',
			address: 'https://pc.local:443',
			expected: 'https://pc.local:443'
		},
		{
			name: 'a bracketed IPv6 address without a port gets the default port',
			address: '[fe80::1]',
			expected: 'https://[fe80::1]:6969'
		},
		{
			name: 'a bracketed IPv6 address keeps its port',
			address: '[fe80::1]:7000',
			expected: 'https://[fe80::1]:7000'
		},
		{
			name: 'the bracketed loopback address with a port',
			address: '[::1]:7000',
			expected: 'https://[::1]:7000'
		},
		{
			name: 'a bracketed IPv6 address is lower-cased',
			address: '[FE80::AB]:7000',
			expected: 'https://[fe80::ab]:7000'
		},
		{
			name: 'the design example with scheme, IPv6 host, port and root path',
			address: 'https://[fe80::1]:6969/',
			expected: 'https://[fe80::1]:6969'
		}
	];

	const rejectedCases: AddressCase[] = [
		{ name: 'whitespace inside the address', address: 'pc local', expected: null },
		{
			name: 'whitespace inside an address with a scheme',
			address: 'http://pc local',
			expected: null
		},
		{ name: 'a scheme other than http or https', address: 'ftp://x', expected: null },
		{ name: 'a user name in front of the host', address: 'user@pc.local', expected: null },
		{ name: 'a query', address: 'pc.local?token=1', expected: null },
		{ name: 'a fragment', address: 'pc.local#top', expected: null },
		{ name: 'a path below the root', address: 'https://pc.local/foo', expected: null },
		{ name: 'a root path with something behind it', address: 'https://pc.local//', expected: null },
		{ name: 'a port without a host', address: ':6969', expected: null },
		{ name: 'a scheme without a host', address: 'https://', expected: null },
		{ name: 'port zero', address: 'pc.local:0', expected: null },
		{ name: 'a port above the range', address: 'pc.local:65536', expected: null },
		{ name: 'a port that is not a number', address: 'x:abc', expected: null },
		{
			name: 'a bracketed IPv6 address with a tail that is not a port',
			address: '[fe80::1]x',
			expected: null
		},
		{ name: 'an unterminated bracket', address: '[fe80::1', expected: null },
		{ name: 'an IPv6 address without brackets', address: 'fe80::1', expected: null },
		{ name: 'a host name in brackets', address: '[abc]', expected: null },
		{ name: 'empty brackets', address: '[]', expected: null },
		{ name: 'an IPv4 address in brackets', address: '[1.2.3.4]', expected: null },
		{
			name: 'an IPv4 address in brackets with a port',
			address: '[1.2.3.4]:7000',
			expected: null
		}
	];

	it.each(acceptedCases)('accepts $name', ({ address, expected }) => {
		expect(canonicalBaseUrl(address)).toBe(expected);
	});

	it.each(rejectedCases)('rejects $name', ({ address, expected }) => {
		expect(canonicalBaseUrl(address)).toBe(expected);
	});
});

describe('hostLabel', () => {
	const cases: Array<{ name: string; baseUrl: string; expected: string }> = [
		{ name: 'an empty base URL has no label', baseUrl: '', expected: '' },
		{
			name: 'the default port is hidden behind https',
			baseUrl: 'https://pc.local:6969',
			expected: 'pc.local'
		},
		{
			name: 'the default port is hidden behind http',
			baseUrl: 'http://pc.local:6969',
			expected: 'pc.local'
		},
		{
			name: 'another port stays visible',
			baseUrl: 'https://pc.local:7000',
			expected: 'pc.local:7000'
		},
		{
			name: 'a bracketed IPv6 host loses only the default port',
			baseUrl: 'https://[fe80::1]:6969',
			expected: '[fe80::1]'
		},
		{
			name: 'a bracketed IPv6 host keeps another port',
			baseUrl: 'https://[fe80::1]:7000',
			expected: '[fe80::1]:7000'
		}
	];

	it.each(cases)('$name', ({ baseUrl, expected }) => {
		expect(hostLabel(baseUrl)).toBe(expected);
	});
});

describe('addressError', () => {
	const CLIENT_MISSING = 'A Fika client needs the address of the host.';
	const UNUSABLE =
		'This address cannot be used. Enter something like 192.168.1.20 or 192.168.1.20:7000.';

	const cases: Array<{ name: string; role: FikaRole; address: string; expected: string }> = [
		{
			name: 'without Fika even an unusable address is fine',
			role: 'none',
			address: 'ftp://x',
			expected: ''
		},
		{ name: 'without Fika an empty address is fine', role: 'none', address: '', expected: '' },
		{ name: 'a host may leave the address empty', role: 'host', address: '', expected: '' },
		{ name: 'a host may leave the address blank', role: 'host', address: '   ', expected: '' },
		{
			name: 'a client without an address is told to enter one',
			role: 'client',
			address: '',
			expected: CLIENT_MISSING
		},
		{
			name: 'a blank address counts as missing for a client',
			role: 'client',
			address: '   ',
			expected: CLIENT_MISSING
		},
		{
			name: 'a host with an unusable address is told so',
			role: 'host',
			address: 'ftp://x',
			expected: UNUSABLE
		},
		{
			name: 'a client with an unusable address is told so',
			role: 'client',
			address: 'pc.local/foo',
			expected: UNUSABLE
		},
		{
			name: 'a host with a usable address has no error',
			role: 'host',
			address: '192.168.1.20:7000',
			expected: ''
		},
		{
			name: 'a client with a usable address has no error',
			role: 'client',
			address: '192.168.1.20:7000',
			expected: ''
		}
	];

	it.each(cases)('$name', ({ role, address, expected }) => {
		expect(addressError(role, address)).toBe(expected);
	});
});

describe('FIKA_ROLES', () => {
	it('offers exactly the three roles in the order they are shown', () => {
		expect(FIKA_ROLES.map((option) => option.id)).toEqual(['none', 'host', 'client']);
	});

	it.each(FIKA_ROLES)('the option $id has a label and a hint', (option) => {
		expect(option.label.length).toBeGreaterThan(0);
		expect(option.hint.length).toBeGreaterThan(0);
	});
});
