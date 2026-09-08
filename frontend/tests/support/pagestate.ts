/**
 * A mutable stand-in for `$app/state`. The mock factory of `vi.mock` is
 * hoisted above every top-level binding, so the URL a test wants has to live
 * in a module the factory imports rather than in the test file itself.
 */

export const pageUrl = { current: new URL('http://localhost/') };

export const pageMock = {
	page: {
		get url(): URL {
			return pageUrl.current;
		}
	}
};

export function setPageUrl(href: string): void {
	pageUrl.current = new URL(href, 'http://localhost');
}
