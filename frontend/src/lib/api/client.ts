import { call } from './transport';
import type { components } from './types';

type Schemas = components['schemas'];

export type ModInfo = Schemas['ModInfo'];
export type ModType = Schemas['ModType'];
export type ModFile = Schemas['ModFile'];
export type CommitInfo = Schemas['CommitInfo'];
export type Conflict = Schemas['Conflict'];
export type ProfileResidue = Schemas['ProfileResidue'];
export type ProfileSummary = Schemas['ProfileSummary'];
export type CheckResult = Schemas['CheckResult'];
export type CleanupPlan = Schemas['CleanupPlan'];
export type CleanupResult = Schemas['CleanupResult'];
export type UntouchedResidue = Schemas['UntouchedResidue'];
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
export type ThumbnailsResponse = Schemas['ThumbnailsResponse'];
export type DirectoryListing = Schemas['DirectoryListing'];
export type DirectoryEntry = Schemas['DirectoryEntry'];
export type LocatedDirectory = Schemas['LocatedDirectory'];
export type FavoritesResponse = Schemas['FavoritesResponse'];
export type ModSet = Schemas['ModSet'];
export type ModSetEntry = Schemas['ModSetEntry'];
export type ModSetOverview = Schemas['ModSetOverview'];
export type ModSetChange = Schemas['ModSetChange'];
export type ModSetPlan = Schemas['ModSetPlan'];
export type ModSetExport = Schemas['ModSetExport'];
export type ModSetMode = Schemas['ModSetMode'];
export type ProfileBinding = Schemas['ProfileBinding'];
export type ApplyModSetResponse = Schemas['ApplyModSetResponse'];
export type LaunchProfileResponse = Schemas['LaunchProfileResponse'];
export type LaunchStatus = Schemas['LaunchStatus'];
export type StopResult = Schemas['StopResult'];
export type SptReleaseInfo = Schemas['SptReleaseInfo'];
export type SptReleaseList = Schemas['SptReleaseList'];
export type SptApplyResult = Schemas['SptApplyResult'];
export type ApplySptReleaseResponse = Schemas['ApplySptReleaseResponse'];
export type FikaSettings = Schemas['FikaSettings'];
export type FikaRole = Schemas['FikaRole'];
export type HostProfile = Schemas['HostProfile'];
export type FikaInstallPlan = Schemas['FikaInstallPlan'];

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
	force: boolean = false,
	cleanProfiles: boolean = false
): Promise<RemoveResult> {
	return call<RemoveResult>('remove_mod', {
		path,
		name,
		force,
		clean_profiles: cleanProfiles
	} satisfies Schemas['RemoveModRequest']);
}

export function disableMod(path: string, name: string): Promise<ToggleResult> {
	return call<ToggleResult>('disable_mod', { path, name } satisfies Schemas['DisableModRequest']);
}

export function enableMod(path: string, name: string): Promise<ToggleResult> {
	return call<ToggleResult>('enable_mod', { path, name } satisfies Schemas['EnableModRequest']);
}

export function listProfiles(path: string): Promise<ProfileSummary[]> {
	return call<ProfileSummary[]>('list_profiles', {
		path
	} satisfies Schemas['ListProfilesRequest']);
}

export function checkProfiles(path: string): Promise<CheckResult> {
	return call<CheckResult>('check_profiles', { path } satisfies Schemas['CheckProfilesRequest']);
}

export function planProfileCleanup(path: string, modName: string): Promise<CleanupPlan> {
	return call<CleanupPlan>('plan_profile_cleanup', {
		path,
		mod_name: modName
	} satisfies Schemas['PlanProfileCleanupRequest']);
}

export function cleanProfile(
	path: string,
	modName: string,
	profiles: string[] | null = null
): Promise<CleanupResult> {
	return call<CleanupResult>('clean_profile', {
		path,
		mod_name: modName,
		profiles
	} satisfies Schemas['CleanProfileRequest']);
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
	compatibleOnly: boolean = false,
	sptVersion?: string
): Promise<CatalogEntry[]> {
	return call<CatalogEntry[]>('browse_catalog', {
		path,
		filter,
		compatible_only: compatibleOnly,
		spt_version: sptVersion
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

export function getThumbnails(modIds: number[]): Promise<ThumbnailsResponse> {
	return call<ThumbnailsResponse>('get_thumbnails', {
		mod_ids: modIds
	} satisfies Schemas['GetThumbnailsRequest']);
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

export function listModSets(path: string): Promise<ModSetOverview> {
	return call<ModSetOverview>('list_mod_sets', { path } satisfies Schemas['ListModSetsRequest']);
}

export function saveModSet(
	path: string,
	name: string,
	entries: ModSetEntry[] | null = null
): Promise<ModSet> {
	return call<ModSet>('save_mod_set', {
		path,
		name,
		entries
	} satisfies Schemas['SaveModSetRequest']);
}

export function deleteModSet(path: string, name: string): Promise<ModSetOverview> {
	return call<ModSetOverview>('delete_mod_set', {
		path,
		name
	} satisfies Schemas['DeleteModSetRequest']);
}

export function renameModSet(path: string, name: string, newName: string): Promise<ModSet> {
	return call<ModSet>('rename_mod_set', {
		path,
		name,
		new_name: newName
	} satisfies Schemas['RenameModSetRequest']);
}

export function exportModSet(path: string, name: string): Promise<ModSetExport> {
	return call<ModSetExport>('export_mod_set', {
		path,
		name
	} satisfies Schemas['ExportModSetRequest']);
}

export function importModSet(
	path: string,
	source: { setFile?: string; content?: string },
	name: string | null = null
): Promise<ModSet> {
	return call<ModSet>('import_mod_set', {
		path,
		set_file: source.setFile ?? null,
		content: source.content ?? null,
		name
	} satisfies Schemas['ImportModSetRequest']);
}

export function bindProfileSet(
	path: string,
	profileId: string,
	setName: string | null = null
): Promise<ModSetOverview> {
	return call<ModSetOverview>('bind_profile_set', {
		path,
		profile_id: profileId,
		set_name: setName
	} satisfies Schemas['BindProfileSetRequest']);
}

export function planModSet(path: string, name: string): Promise<ModSetPlan> {
	return call<ModSetPlan>('plan_mod_set', { path, name } satisfies Schemas['PlanModSetRequest']);
}

export function applyModSet(
	path: string,
	name: string,
	ignoreMissing: boolean = false,
	operationId: string | null = null
): Promise<ApplyModSetResponse> {
	return call<ApplyModSetResponse>('apply_mod_set', {
		path,
		name,
		ignore_missing: ignoreMissing,
		operation_id: operationId
	} satisfies Schemas['ApplyModSetRequest']);
}

export function launchProfile(
	path: string,
	profileId: string,
	stopServerOnExit: boolean = true,
	operationId: string | null = null,
	modSet: ModSetMode = 'apply'
): Promise<LaunchProfileResponse> {
	return call<LaunchProfileResponse>('launch_profile', {
		path,
		profile_id: profileId,
		stop_server_on_exit: stopServerOnExit,
		mod_set: modSet,
		operation_id: operationId
	} satisfies Schemas['LaunchProfileRequest']);
}

export function launchStatus(path: string): Promise<LaunchStatus> {
	return call<LaunchStatus>('launch_status', { path } satisfies Schemas['LaunchStatusRequest']);
}

export function stopGame(path: string): Promise<StopResult> {
	return call<StopResult>('stop_game', { path } satisfies Schemas['StopGameRequest']);
}

export function listSptReleases(path: string): Promise<SptReleaseList> {
	return call<SptReleaseList>('list_spt_releases', {
		path
	} satisfies Schemas['ListSptReleasesRequest']);
}

export function applySptRelease(
	path: string,
	version: string,
	operationId: string | null = null
): Promise<ApplySptReleaseResponse> {
	return call<ApplySptReleaseResponse>('apply_spt_release', {
		path,
		version,
		operation_id: operationId
	} satisfies Schemas['ApplySptReleaseRequest']);
}

export function revertSptUpdate(path: string): Promise<SptApplyResult> {
	return call<SptApplyResult>('revert_spt_update', {
		path
	} satisfies Schemas['RevertSptUpdateRequest']);
}

export function getFikaSettings(path: string): Promise<FikaSettings> {
	return call<FikaSettings>('get_fika_settings', {
		path
	} satisfies Schemas['GetFikaSettingsRequest']);
}

export function setFikaSettings(
	path: string,
	role: FikaRole,
	hostAddress: string = ''
): Promise<FikaSettings> {
	return call<FikaSettings>('set_fika_settings', {
		path,
		role,
		host_address: hostAddress
	} satisfies Schemas['SetFikaSettingsRequest']);
}

export function listHostProfiles(path: string): Promise<HostProfile[]> {
	return call<HostProfile[]>('list_host_profiles', {
		path
	} satisfies Schemas['ListHostProfilesRequest']);
}

export function planFikaInstall(path: string): Promise<FikaInstallPlan> {
	return call<FikaInstallPlan>('plan_fika_install', {
		path
	} satisfies Schemas['PlanFikaInstallRequest']);
}
