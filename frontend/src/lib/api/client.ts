import { call } from './transport';
import type { components } from './types';

type Schemas = components['schemas'];

export type ModInfo = Schemas['ModInfo'];
export type ModFile = Schemas['ModFile'];
export type CommitInfo = Schemas['CommitInfo'];
export type Conflict = Schemas['Conflict'];
export type ProfileResidue = Schemas['ProfileResidue'];
export type CheckResult = Schemas['CheckResult'];
export type InitResult = Schemas['InitResult'];
export type SetSptVersionResponse = Schemas['SetSptVersionResponse'];
export type RemoveResult = Schemas['RemoveResult'];
export type ToggleResult = Schemas['ToggleResult'];
export type StatusReport = Schemas['StatusReport'];
export type ManualMod = Schemas['ManualMod'];
export type SnapshotResult = Schemas['SnapshotResult'];
export type ImportDiff = Schemas['ImportDiff'];
export type ImportEntry = Schemas['ImportEntry'];
export type ExportModsResponse = Schemas['ExportModsResponse'];
export type CatalogEntry = Schemas['CatalogEntry'];
export type CatalogMod = Schemas['CatalogMod'];
export type CatalogVersion = Schemas['CatalogVersion'];
export type SyncCatalogResponse = Schemas['SyncCatalogResponse'];
export type BrowseFilter = Schemas['BrowseFilter'];
export type ExportFormat = Schemas['ExportFormat'];

export type InstallModResponse = Schemas['InstallModResponse'];
export type UpdateModResponse = Schemas['UpdateModResponse'];
export type InstallPlan = Schemas['InstallPlan'];
export type PlanEntry = Schemas['PlanEntry'];
export type UpdatesReport = Schemas['UpdatesReport'];
export type UpdateCandidate = Schemas['UpdateCandidate'];
export type BlockedUpdate = Schemas['BlockedUpdate'];
export type ClearCacheResult = Schemas['ClearCacheResult'];
export type DirectoryListing = Schemas['DirectoryListing'];
export type DirectoryEntry = Schemas['DirectoryEntry'];
export type LocatedDirectory = Schemas['LocatedDirectory'];
export type FavoritesResponse = Schemas['FavoritesResponse'];

export function listDirectories(path: string | null = null): Promise<DirectoryListing> {
	return call<DirectoryListing>('list_directories', {
		path
	} satisfies Schemas['ListDirectoriesRequest']);
}

export function locateDirectory(
	name: string,
	fingerprint: string[] = []
): Promise<LocatedDirectory[]> {
	return call<LocatedDirectory[]>('locate_directory', {
		name,
		fingerprint
	} satisfies Schemas['LocateDirectoryRequest']);
}

export function initGameDir(
	path: string,
	adopt: boolean | string[] = false,
	sptVersion: string | null = null
): Promise<InitResult> {
	return call<InitResult>('init_game_dir', {
		path,
		adopt,
		spt_version: sptVersion
	} satisfies Schemas['InitGameDirRequest']);
}

export function setSptVersion(path: string, version: string): Promise<SetSptVersionResponse> {
	return call<SetSptVersionResponse>('set_spt_version', {
		path,
		version
	} satisfies Schemas['SetSptVersionRequest']);
}

export function listMods(path: string, includeAll: boolean = false): Promise<ModInfo[]> {
	return call<ModInfo[]>('list_mods', {
		path,
		include_all: includeAll
	} satisfies Schemas['ListModsRequest']);
}

export function modFiles(path: string, name: string): Promise<ModFile[]> {
	return call<ModFile[]>('mod_files', { path, name } satisfies Schemas['ModFilesRequest']);
}

export function removeMod(
	path: string,
	name: string,
	force: boolean = false
): Promise<RemoveResult> {
	return call<RemoveResult>('remove_mod', {
		path,
		name,
		force
	} satisfies Schemas['RemoveModRequest']);
}

export function disableMod(path: string, name: string): Promise<ToggleResult> {
	return call<ToggleResult>('disable_mod', { path, name } satisfies Schemas['DisableModRequest']);
}

export function enableMod(path: string, name: string): Promise<ToggleResult> {
	return call<ToggleResult>('enable_mod', { path, name } satisfies Schemas['EnableModRequest']);
}

export function checkProfiles(path: string): Promise<CheckResult> {
	return call<CheckResult>('check_profiles', { path } satisfies Schemas['CheckProfilesRequest']);
}

export function history(path: string, name: string | null = null): Promise<CommitInfo[]> {
	return call<CommitInfo[]>('history', { path, name } satisfies Schemas['HistoryRequest']);
}

export function status(path: string): Promise<StatusReport> {
	return call<StatusReport>('status', { path } satisfies Schemas['StatusRequest']);
}

export function snapshot(path: string, message: string | null = null): Promise<SnapshotResult> {
	return call<SnapshotResult>('snapshot', { path, message } satisfies Schemas['SnapshotRequest']);
}

export function syncCatalog(
	path: string,
	full: boolean = false,
	operationId: string | null = null
): Promise<SyncCatalogResponse> {
	return call<SyncCatalogResponse>('sync_catalog', {
		path,
		full,
		operation_id: operationId
	} satisfies Schemas['SyncCatalogRequest']);
}

export function searchCatalog(
	path: string,
	query: string,
	compatibleOnly: boolean = false
): Promise<CatalogEntry[]> {
	return call<CatalogEntry[]>('search_catalog', {
		path,
		query,
		compatible_only: compatibleOnly
	} satisfies Schemas['SearchCatalogRequest']);
}

export function browseCatalog(
	path: string,
	filter: BrowseFilter = 'all',
	compatibleOnly: boolean = false
): Promise<CatalogEntry[]> {
	return call<CatalogEntry[]>('browse_catalog', {
		path,
		filter,
		compatible_only: compatibleOnly
	} satisfies Schemas['BrowseCatalogRequest']);
}

export function exportMods(
	path: string,
	format: ExportFormat = 'text',
	clientOnly: boolean = false
): Promise<ExportModsResponse> {
	return call<ExportModsResponse>('export_mods', {
		path,
		format,
		client_only: clientOnly
	} satisfies Schemas['ExportModsRequest']);
}

export function planModInstall(
	path: string,
	modIdOrName: number | string,
	version: string | null = null
): Promise<InstallPlan> {
	return call<InstallPlan>('plan_mod_install', {
		path,
		mod_id_or_name: modIdOrName,
		version
	} satisfies Schemas['PlanModInstallRequest']);
}

export function installMod(
	path: string,
	modIdOrName: number | string,
	version: string | null = null,
	force: boolean = false,
	operationId: string | null = null
): Promise<InstallModResponse> {
	return call<InstallModResponse>('install_mod', {
		path,
		mod_id_or_name: modIdOrName,
		version,
		force,
		operation_id: operationId
	} satisfies Schemas['InstallModRequest']);
}

export function updateMod(
	path: string,
	name: string,
	force: boolean = false,
	operationId: string | null = null
): Promise<UpdateModResponse> {
	return call<UpdateModResponse>('update_mod', {
		path,
		name,
		force,
		operation_id: operationId
	} satisfies Schemas['UpdateModRequest']);
}

export function checkUpdates(path: string, names: string[] = []): Promise<UpdatesReport> {
	// An empty list would mean "check nothing"; omitting it checks every mod.
	return call<UpdatesReport>('check_updates', {
		path,
		names: names.length > 0 ? names : null
	} satisfies Schemas['CheckUpdatesRequest']);
}

export type ModDetails = Schemas['ModDetails'];

export function getModDetails(modId: number): Promise<ModDetails> {
	return call<ModDetails>('get_mod_details', {
		mod_id: modId
	} satisfies Schemas['GetModDetailsRequest']);
}

export function clearCatalogCache(
	request: { downloads?: boolean } = {}
): Promise<ClearCacheResult> {
	return call<ClearCacheResult>('clear_catalog_cache', {
		downloads: request.downloads ?? false
	} satisfies Schemas['ClearCatalogCacheRequest']);
}

export function listFavorites(): Promise<FavoritesResponse> {
	return call<FavoritesResponse>('list_favorites', {} satisfies Schemas['ListFavoritesRequest']);
}

export function setFavorite(modId: number, favorite: boolean): Promise<FavoritesResponse> {
	return call<FavoritesResponse>('set_favorite', {
		mod_id: modId,
		favorite
	} satisfies Schemas['SetFavoriteRequest']);
}

export function importList(
	path: string,
	source: { listFile?: string; content?: string },
	apply: boolean = false
): Promise<ImportDiff> {
	return call<ImportDiff>('import_list', {
		path,
		list_file: source.listFile ?? null,
		content: source.content ?? null,
		apply
	} satisfies Schemas['ImportListRequest']);
}
