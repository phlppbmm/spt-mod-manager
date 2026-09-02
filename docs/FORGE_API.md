# Forge API v0 — verified reference

Verified 2026-09-02 from the API's generating source (github.com/sp-tarkov/forge, main branch:
routes/api_v0.php, app/Http/Controllers/Api/V0/*, config/api.php). The live docs at
forge.sp-tarkov.com/docs were down (HTTP 525) at verification time. This file supersedes the
provisional endpoint/field names in docs/HANDOFF.md §3a. Re-verify against the live docs when
they come back.

## Key deviations from HANDOFF §3a

- **No authentication.** The v0 API is read-only and open. `POST /auth/login`, token creation,
  and Bearer auth do not exist (removed from the platform). Everything in the handoff about
  login flows, keyring token storage, and the frontend login step is obsolete.
- **Rate limit:** per-IP throttle, 300 requests/min (origin default; Cloudflare edge may trip
  earlier). `GET /ping` is outside the throttle group. Not the handoff's 40/10s + 200/60s —
  still throttle client-side conservatively and back off on 429.
- Mod detail is singular: `/mod/{modId}`, not `/mods/{modId}`.

## Endpoints (all GET, base `https://forge.sp-tarkov.com/api/v0`)

- `/ping`
- `/mods` — catalog listing (paginated, searchable)
- `/mod/{modId}` — detail (adds `description`, `custom_ai_disclosure`)
- `/mod/{modId}/versions` — versions (paginated)
- `/mod/{modId}/versions/{versionId}/file-tree` — `{verified_at, file_count, truncated, files:[...]}` or 404
- `/mods/updates` — update check for installed mods
- `/mods/dependencies` — dependency resolution
- Also available: `/addons*`, `/mod-categories*`, `/spt/versions`; unversioned health `/up`

## Response envelope and errors

- Success: `{"success": true, "data": ...}` (+ `links`/`meta` on paginated lists).
- Error: `{"success": false, "code": "<CODE>", "message": "<text>"}`; codes seen:
  `VALIDATION_FAILED` (400), `NOT_FOUND` (404). 429 from throttle middleware (body shape undocumented).
- Pagination: `page`, `per_page` (default 12, server clamps to max 50). Envelope
  `links {first,last,prev,next}`, `meta {current_page,from,last_page,path,per_page,to,total}`;
  `meta.total` may lag up to 60s (cached).

## GET /mods — parameters

- `query` — Meilisearch full-text (name/slug/description)
- `fields` — sparse fieldset (comma-separated)
- `include` — `versions` (latest 6 only: `{id,version,spt_version_constraint,downloads,published_at}`),
  `license` (`{id,name,short_name}`), `category` (`{id,name,slug,color_class}`), `source_code_links`.
  `owner` and `additional_authors` are always included.
- Filters: `filter[id]`, `filter[hub_id]`, `filter[guid]`, `filter[name]`, `filter[slug]`,
  `filter[teaser]`, `filter[featured]`, `filter[contains_ads]`, `filter[contains_ai_content]`,
  `filter[category_id]`, `filter[category_slug]`, `filter[created_between]`,
  `filter[updated_between]`, `filter[published_between]` (each `YYYY-MM-DD,YYYY-MM-DD`),
  `filter[spt_version]` (SemVer constraint, e.g. `^3.8.0`), `filter[fika_compatibility]`,
  `filter[include_legacy]`
- `sort`: `name`, `downloads`, `favourites_count`, `featured`, `created_at`, `updated_at`,
  `published_at`; `-` prefix = DESC, comma-separated.
- **Delta sync**: `filter[updated_between]` + `sort=-updated_at` (date-granular only).

## Mod object

`id`, `hub_id`, `guid`, `name`, `slug`, `teaser`, `thumbnail`, `downloads`, `favourites_count`,
`owner {id,name,profile_photo_url,cover_photo_url}`, `additional_authors` (same shape),
`source_code_links [{url,label}]`, `detail_url`, `fika_compatibility` (bool),
`featured`, `contains_ads`, `contains_ai_content`, `shows_profile_binding_notice`,
`published_at`, `created_at`, `updated_at` (ISO-8601 `...Z`).

## Version object (GET /mod/{modId}/versions)

`id`, `hub_id`, `version`, `description`, `link` (download URL), `content_length` (bytes,
**may be null** for old versions), `spt_version_constraint` (e.g. `^3.8.0`), `downloads`,
`fika_compatibility` (`compatible`|`incompatible`|`unknown`), `published_at`, `created_at`,
`updated_at`.
Params: `fields`, `sort`, `page`, `per_page`, `include` = `dependencies` | `virus_total_links`;
filters: `id`, `hub_id`, `version` (SemVer constraint), `description`, `published_between`,
`created_between`, `updated_between`, `spt_version`, `fika_compatibility`.
Embedded dependency: `{id, mod_id, mod_guid, mod_name, version_constraint, is_optional}`
(immediate deps only).

## GET /mods/updates and /mods/dependencies

Common query params (both required):
- `mods` — comma-separated `identifier:version` pairs; identifier = numeric mod id or GUID
  (e.g. `5:1.2.0,com.example.mod:2.0.5`). Arrays (`mods[]=`) are a 400.
- `spt_version` — must exactly match a published SPT version (e.g. `3.11.5`).

`/mods/updates` → `data`:
- `spt_version`
- `updates`: `[{current_version {id,mod_id,guid,name,slug,version},
  recommended_version {id,version,link,content_length,fika_compatibility,spt_versions:[...]},
  update_reason}]`
- `blocked_updates`: `[{current_version, latest_version {id,version,spt_versions}, block_reason,
  blocking_mods [{mod_id,mod_guid,mod_name,current_version,constraint,incompatible_with}]}]`
- `up_to_date`: `[{id,mod_id,guid,name,version,spt_versions}]`
- `incompatible_with_spt`: `[{id,mod_id,guid,name,version,reason,latest_compatible_version}]`
- Prerelease-aware: stable installs never get prerelease recommendations.

`/mods/dependencies` → `data`: object keyed by each queried `identifier:version` pair verbatim
(unresolvable pairs omitted; no deps = `[]`). Node:
`{id, guid, name, slug, latest_compatible_version {id,version,link,content_length,fika_compatibility} | null,
conflict: bool, dependencies: [recursive]}`.
Version selection is global across the request; `conflict: true` when no single version
satisfies all queried mods.

## Misc

- Cacheable GETs carry public Cache-Control headers (CDN-cached).
