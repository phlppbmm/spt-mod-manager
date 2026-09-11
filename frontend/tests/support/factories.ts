import type {
	CatalogEntry,
	CatalogMod,
	CatalogVersion,
	CommitInfo,
	LaunchStatus,
	LocalSourceInfo,
	ModInfo,
	OfferedMod,
	ModSet,
	ProfileSummary,
	SptReleaseList
} from '$lib/api/client';

export function catalogMod(overrides: Partial<CatalogMod> = {}): CatalogMod {
	return {
		id: 1,
		hub_id: null,
		guid: null,
		name: 'Example Mod',
		slug: 'example-mod',
		teaser: 'An example mod.',
		thumbnail: null,
		author: 'Example Author',
		downloads: 100,
		detail_url: 'https://sp-mod.com/mod/1/example-mod',
		published_at: '2026-01-01T00:00:00Z',
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-02T00:00:00Z',
		favourites_count: 0,
		endorsements_count: 0,
		featured: false,
		contains_ads: false,
		contains_ai_content: false,
		category: 'Gameplay',
		license_name: null,
		license_link: null,
		fika_compatibility: null,
		source_links: [],
		first_seen_at: null,
		...overrides
	};
}

export function catalogVersion(overrides: Partial<CatalogVersion> = {}): CatalogVersion {
	return {
		id: 10,
		mod_id: 1,
		version: '1.0.0',
		link: 'https://sp-mod.com/mod/1/example-mod/download',
		content_length: 1024,
		spt_version_constraint: '~4.1.0',
		downloads: 100,
		published_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-02T00:00:00Z',
		fika_compatibility: null,
		...overrides
	};
}

/**
 * `latest_version` defaults to a version because `isCompatible` reads it and the
 * default filter carries the `compatible` chip: a null default hides the entry.
 */
export function catalogEntry(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
	return {
		mod: catalogMod(),
		state: 'available',
		installed_version: null,
		latest_version: catalogVersion(),
		local: false,
		...overrides
	};
}

export function modInfo(overrides: Partial<ModInfo> = {}): ModInfo {
	return {
		name: 'Example Mod',
		version: '1.0.0',
		mod_type: 'server',
		author: 'Example Author',
		spt_range: '~4.1.0',
		state: 'installed',
		commit: '0123456789abcdef0123456789abcdef01234567',
		date: '2026-01-01T00:00:00Z',
		forge_id: '1',
		forge_version_id: '10',
		guid: 'com.example.mod',
		source: 'forge',
		...overrides
	};
}

export function launchStatus(overrides: Partial<LaunchStatus> = {}): LaunchStatus {
	return {
		server_running: false,
		client_running: false,
		server_pid: 0,
		client_pid: 0,
		profile_id: '',
		server_stoppable: false,
		client_stoppable: false,
		...overrides
	};
}

export function profileSummary(overrides: Partial<ProfileSummary> = {}): ProfileSummary {
	return {
		file: 'aaaaaaaaaaaaaaaaaaaaaaaa.json',
		profile_id: 'aaaaaaaaaaaaaaaaaaaaaaaa',
		nickname: 'Example',
		level: 1,
		side: 'Usec',
		modified: '2026-01-01T00:00:00Z',
		readable: true,
		error: '',
		...overrides
	};
}

export function commitInfo(overrides: Partial<CommitInfo> = {}): CommitInfo {
	return {
		hash: '0123456789abcdef0123456789abcdef01234567',
		subject: 'Install Example Mod 1.0.0',
		date: '2026-01-01T00:00:00Z',
		trailers: {},
		...overrides
	};
}

export function sptReleaseList(overrides: Partial<SptReleaseList> = {}): SptReleaseList {
	return {
		current_version: '4.1.0',
		client_build: '41000',
		releases: [],
		latest: '4.1.0',
		source_ok: true,
		notice: '',
		...overrides
	};
}

export function modSet(overrides: Partial<ModSet> = {}): ModSet {
	return {
		name: 'Example Set',
		created_at: '2026-01-01T00:00:00Z',
		entries: [],
		...overrides
	};
}

export function localSource(overrides: Partial<LocalSourceInfo> = {}): LocalSourceInfo {
	return {
		folder: '/home/me/projects/MyMod',
		added_at: '2026-09-11T12:00:00Z',
		mods: [],
		installed: [],
		...overrides
	};
}

export function offeredMod(overrides: Partial<OfferedMod> = {}): OfferedMod {
	return {
		name: 'MyMod',
		version: '1.0.0',
		archive: '/home/me/projects/MyMod/build/MyMod-1.0.0.zip',
		built_at: '2026-09-10T12:00:00Z',
		mod: null,
		...overrides
	};
}
