# Coder breaking changes, v2.20 to v2.37

Extracted from the upstream release notes (github.com/coder/coder/releases). `./upgrade plan` prints the entries
between the running version and the bundle's version; the ones marked in `upgrade` as checks are tested automatically.

## v2.20.0

- Enforce agent names be case-insensitive-unique per-workspace (#16614, 3fddfef87) (@ethanndickson)
- Enforce regex for agent names (#16641, 9469b7829) (@deansheather)
  > Underscores and trailing, leading and double hyphens are now blocked in agent names.

## v2.21.0

_No breaking changes listed._

## v2.22.0

- Remove JFrog integration (#17353, c06ef7c1e) (@sreya)
  > Removes displaying XRay scan results in the dashboard. If this change unexpectedly affects your workflow, please open an [issue on GitHub](https://github.com/coder/coder/issues/new?template=1-bug.yaml).
- Use names not IDs for agent SSH key seed (#17258, f6bf6c6ec) (@spikecurtis)
  > Changes the SSH host key seeding to use the owner username, workspace name, and agent name.
  > Early access users of **Coder Desktop** will see their SSH clients complain about host keys changing the first time each workspace is rebuilt with this code.

## v2.23.0

- Fix workspace apps response (#17700, 9fe5b71d3) (@BrunoQuaresma)
  > Adds `omitempty` to multiple fields in a codersdk response type. API consumers that expect those fields to be always present may need to be updated.
- Omit `name`, `avatar_url` and `last_seen_at` from responses when empty (#18005, 94c129c03) (@BrunoQuaresma)
- Update WorkspaceOwnerName to use user.name instead of user.username to reduce UI confusion (#18025, d63417b54) (@BrunoQuaresma)

## v2.24.0

- Change `update` build to stop workspaces before starting (#18425, 49fcffc26) (@johnstcn)
  > ⚠ Modifies the behavior when updating a workspace.
  > Previously, a workspace `update` would triggger a `start` build, re-creating the agent and updating any existing compute resources in-place. This change has workspace updates explicitly trigger a `stop` build with a subsequent `start` build. This was implemented to fix pre-builds and improve consistency. [Read more here](https://github.com/coder/coder/issues/17840).
  >
  > - CLI: Modifies `coder update` to stop the workspace if already running.
  > - Dashboard: Modifies "update" button to always stop the workspace if already running.
- CLI: enforce selection for multiple agents rather than use randomness (#18427, bacdc2888) (@mafredri)
  > This only impacts the flow for our new devcontainer integration, and existing users will not see changes to behavior.

## v2.25.0

- Route connection logs to Connection log instead of Audit log (#18340, 08e17a07f) (@ethanndickson)
  > Connections to workspaces (via SSH, workspace apps, or browser port-forwarding) will no longer create entries in the audit log. Those events will now be included in the '[Connection Log]()'.
  > Please see the 'Connection Log' page in the dashboard, and the Connection Log documentation for details. Those with permission to view the Audit Log will also be able to view the Connection Log. The new Connection Log has the same licensing restrictions as the Audit Log, and requires a Premium Coder deployment.
- Delete old connection events from audit log (#18735, f42de9fe1) (@ethanndickson)
  > With new connection events appearing in the Connection Log, connection events older than 90 days will now be deleted from the Audit Log. If you require this legacy data, we recommend querying it from the REST API or making a backup of the database/these events before upgrading your Coder deployment. Please see the PR for details on what exactly will be deleted.
  > Note: There are currently no plans to delete connection events from the Connection Log.
- Add ability to cancel pending workspace build (#18713, 8202514ce) (@kacpersaw)
  > CancelWorkspaceBuild method in codersdk now accepts an **optional** `request` parameter.
- Use devcontainer ID when rebuilding a devcontainer (#18604, f2d229eed) (@DanielleMaywood)
  > Minor breaking change for workspaces enabled by our devcontainer integration.
  > Allows rebuilding a devcontainer without a valid devcontainer ID.
- CLI: Add CLI support for creating workspace with presets (#18912, b975d6d9b) (@ssncferreira)
  > This breaking change impacts the `coder create` CLI command only for templates which contain presets.
  >
  > It introduces a `--preset` flag to the create command, which modifies the behavior when no preset is explicitly provided:
  > - If the template includes presets and a default preset, the default will be automatically applied. The user will be notified, but not prompted.
  > - If the template includes presets without a default, the user will be prompted to choose a preset.
  >
  > This breaks existing workflows for templates with presets that:
  > - Expect the create command to proceed without applying a preset
  > - Rely on non-interactive scripts or automated workflows, which will now fail or hang due to unexpected prompts

## v2.26.0

- Support empty or default fields when updating templates (#19256, aab2ccdb3) (@rafrdz)
  > Breaking change to the Coder Go SDK. Field types in `codersdk.UpdateTemplateMeta` for `Icon`, `Description`, and `DisplayName` changed from `string` to `*string`. Consumers must pass pointers and handle `nil` checks. Code that assigns/reads plain strings will no longer compile without updates.
- fix(coderd/prometheusmetrics)!: filter deleted wsbuilds to reduce db load (#19197, 1b66495b7) (@mafredri)
  > Breaking change to `coderd_api_workspace_latest_build` Prometheus metric. The `coderd_api_workspace_latest_build` Prometheus metric no longer includes builds belonging to deleted workspaces, as such, this metric will show fewer statuses.

## v2.27.0

- Use client IP when creating connection logs for workspace proxied app accesses (#19788, 6a9b896f5) (@ethanndickson)
  > The presence of the `ip` field on `codersdk.ConnectionLog` cannot be guaranteed, and so the field has been made optional. It may be omitted on API responses.
- Server: Only show task status for current build (#19966, eb55f0ab1) (@johnstcn)
  > Renames the `TaskStateCompleted` constant to `TaskStateComplete` to align with the tense used in `WorkspaceAppStatusStateComplete`, requiring any code referencing the constant to be updated.
- Rename prompt field to input for task creation (#19982, eb7473283) (@DanielleMaywood)
  > Renames the `CreateTaskRequest.Prompt` to `CreateTaskRequest.Input` to align with language used in our CLI and elsewhere in the codebase.

## v2.28.0

- Ensure consistent secret token generation and hashing (#20388, 13ca9ead3) (@Emyrk)
    > This change standardizes how Coder hashes secret tokens by switching OAuth2 tokens from salted userpassword. Hash to the same SHA256 hashing method used for API keys, improving consistency across all secret token types. 
    > This is a breaking change because existing OAuth2 tokens will no longer validate correctly after the update, but the impact is limited to OAuth2 functionality which is only available in dev builds and experimental features.
- Removed TaskAppID field from WorkspaceBuild API response in favor of new task data model (#20583, 7a97ebe03) (@DanielleMaywood)
    > This change removes the task_app_id field from the WorkspaceBuild API response, which was a temporary field that stored the task's workspace app ID directly in the workspace build object. Clients that were consuming this field from the API will need to instead fetch this information from the new dedicated Task data model via the Task API endpoints.
- AI Bridge is now stable - removed experimental flag and moved from `/api/experimental/aibridge/*` to `/api/v2/aibridge/*` (#20544, a119fe2f5) (@dannykopping)
    > Removes the experimental flag for AI Bridge and promotes it to Stable. Update URL routes to /api/v2/aibridge/* for continued use of AI Bridge.

## v2.29.0

- chore!: allow coder MCP tools to not be injected (#20713, 04f809f2d) (@dannykopping)

    > Coder MCP tools were previously automatically injected; this will now need to be explicitly enabled with `CODER_AIBRIDGE_INJECT_CODER_MCP_TOOLS=true`

- feat(cli)!: enable keyring usage by default (#20851, 6238a9927) (@zedkipp)
    
    > In order to move away from storing the CLI session token in plain text file, this change begins using the operating system keyring for CLI session token storage by default on Windows and macOS. This will be a breaking change for any users depending on the session token being stored to or read from disk. Users can opt-out and restore file usage via the --use-keyring=false flag.

- refactor!: remove TaskAppID from codersdk.WorkspaceBuild (#20583, d80b5fc8e) (@DanielleMaywood)

    > The task_app_id field has been removed from the codersdk.WorkspaceBuild struct and API responses. If your code or integrations rely on WorkspaceBuild.task_app_id to identify the task's associated app, you should migrate to using Task.WorkspaceAppID instead (available on the Task object directly). The deprecated ai_task_sidebar_app_id field remains temporarily but also points to the new location.

## v2.30.0

- feat!: Cached Terraform Modules speed up workspace startup (#21398, 60b3fd078) (@Emyrk)

    > Terraform modules are now downloaded once per template version and reused on every workspace start. Modules are fetched and pinned when the template version is created, then cached and reused across all workspace starts. This prevents upstream module changes from breaking workspace restarts and reduces repeated downloads and startup time.

- feat!: implement `AI Bridge` heading to `/deployment/observability` (#20791, ab4366f5c) (@jakehwll)
   
    > The experimental AI Bridge API endpoints `/api/experimental/aibridge/*` have been removed. AI Bridge API was promoted to stable in v2.29.0, and all clients, scripts, or integrations must now use the stable `/api/v2/aibridge/*` routes instead. This follows standard deprecation practice—experimental endpoints are removed once the feature reaches general availability.

- feat!: support PKCE in the oauth2 client's auth/exchange flow (#21215, 8fefd91e4) (@Emyrk)

    > This PR adds PKCE (Proof Key for Code Exchange) support to Coder's OAuth2 client flow when authenticating with external identity providers. Unknown external OAuth providers now default to using PKCE, which will cause authentication failures if the provider doesn't actually support it. To resolve this, set CODER_EXTERNAL_AUTH_<N>_PKCE_METHODS=none in your environment configuration to disable PKCE for incompatible providers.

- fix(agent/agentssh)!: use configured directory for SFTP connections (#21194, 6bea82baf) (@mafredri)

    > If your workspace agent has a custom dir configured in Terraform, SFTP and SCP connections will now land there instead of $HOME. Previously, only SSH and rsync respected this setting, which caused confusing behavior where scp file.txt coder:. and rsync file.txt coder:. would put files in different places. If you have scripts that relied on SFTP/SCP always using $HOME regardless of agent configuration, you may need to use explicit paths instead.

## v2.32.0

- chore!: remove members' ability to read their own AI Bridge interceptions ([#23320](https://github.com/coder/coder/pull/23320))

  > Regular users (non-owners, non-auditors) can no longer read AI Bridge interception data, including their own. Only owners and auditors retain read access. This tightens the RBAC surface to prevent insiders from observing what data is tracked.

- fix(cli)!: `coder groups list -o json` output structure changed ([#22923](https://github.com/coder/coder/pull/22923))

  > The JSON output is now a flat structure matching other `coder list -o json` commands. Previously this command returned empty zero-value structs due to a bug, so no working consumer of the old format could exist.

## v2.33.0

- chore!: remove `api.ts` unnecessary calls (#22168, 4caa52844d) (@jakehwll)
- Dashboard: Require confirmation before executing terminal command from URL (#24650, 66abd8a271) (@Shelnutt2)

## v2.34.0

- Default `CODER_AI_GATEWAY_ENABLED` to true ([#25575](https://github.com/coder/coder/pull/25575))

  > The AI Gateway is now enabled by default on all deployments. Set `CODER_AI_GATEWAY_ENABLED=false` to restore the old behavior.

- Seed AI providers from env on server startup ([#24895](https://github.com/coder/coder/pull/24895))

  > AI provider configuration from environment variables is now automatically seeded into the database on startup, replacing runtime-only provider setup. Existing database-configured providers take precedence. See [AI Gateway: provider configuration moved to the database](#ai-gateway-provider-configuration-moved-to-the-database) below for migration details.

- `patchTemplateMeta` uses optional fields ([#24984](https://github.com/coder/coder/pull/24984))

  > The `UpdateTemplateMeta` request now uses pointer fields so that unset fields are left unchanged. Clients sending zero values will no longer accidentally clear template metadata.

- Persist structured chat errors ([#24919](https://github.com/coder/coder/pull/24919))

  > Chat error messages are now persisted as structured `ChatMessagePart` entries instead of plain text. Existing clients that parse error messages as strings may need to handle the new structured format.

## v2.36.0

- Dynamic client registration disabled by default (#27316)

  > `POST /oauth2/register` (RFC 7591 Dynamic Client Registration) is now controlled by a persistent admin setting, `oauth2_dcr_enabled`, independent of the experiment system. The setting **defaults to disabled**. A new audited `GET`/`PUT /api/v2/oauth2-provider/settings` endpoint lets an owner toggle it live without a restart. When disabled, new registrations are rejected with an RFC 7591-shaped 403, and `registration_endpoint` is omitted from discovery metadata.
  >
  > Migration: Deployments that rely on dynamic client registration must explicitly enable the new setting via the API or UI after upgrading. Existing registered clients are unaffected; only new registrations are gated.

- `login_type=none` deprecated; existing accounts converted to password login (#26851)

  > `login_type=none` (legacy passwordless machine users) is deprecated in favor of premium service accounts. A database migration (`000554`) automatically converts existing non-system `login_type=none` accounts to `login_type=password`. Creation of new `login_type=none` accounts via `POST /users` and `coder users create` (including the deprecated `--disable-login` flag) is now rejected unless a service account is explicitly requested.
  >
  > Migration: Automatic for existing users. Legacy `login_type=none` accounts will be migrated to password login on first startup after upgrading. Review any automation that creates users with `login_type=none` and update it to use service accounts instead.

- `chat_model_configs.provider` column removed (#26877)

  > The `provider` column has been dropped from the `chat_model_configs` table. Provider type is now derived exclusively from the linked `ai_providers` row via `ai_provider_id`. The experimental HTTP/SDK response for model configs drops the `provider` field and makes `ai_provider_id` required; consumers should resolve provider type by joining against the AI providers listing. Telemetry continues to emit `provider` via a JOIN, so BigQuery columns and Nexus dashboards are unaffected.
  >
  > Migration: Update any custom queries or SDK consumers that read `chat_model_configs.provider` directly. Resolve provider type via `ai_provider_id` and the AI providers listing instead.

## v2.37.0

- Coder Agents chats API promoted from `/api/experimental` to `/api/v2` (#28496)

  > The Coder Agents chats API is moving from `/api/experimental` to `/api/v2`. The experimental routes remain available for a one-month migration period. Integrations using endpoints promoted to `/api/v2` must migrate before the compatibility routes are removed in v2.38.
  >
  > Migration: Update any integration that calls the experimental chats API to the `/api/v2` equivalents before v2.38, when the compatibility routes are removed.

- Default-organization chat-model routes removed (#28632)

  > The obsolete default-organization chat-model routes `/api/experimental/chats/models` and `/api/experimental/chats/model-configs` are removed immediately in v2.37.
  >
  > Migration: Use `/api/v2/organizations/{organization}/chats/models` instead.

- OAuth authorization rejects unsupported or over-broad scopes (#28178)

  > OAuth authorization requests now reject unsupported or over-broad scopes with `invalid_scope`. Integrations sending values such as `openid`, `profile`, or `email` may fail.
  >
  > Migration: Review your OAuth integrations and remove unsupported or over-broad scope values so requests are not rejected with `invalid_scope`.

- Dynamic client registration disabled by default (#27316)

  > `POST /oauth2/register` is now controlled by the persistent deployment setting `oauth2_dcr_enabled`, which defaults to disabled. When disabled, Coder omits `registration_endpoint` from discovery metadata and returns an RFC 7591-style `403` for new dynamic client registrations; existing registered clients continue to work.
  >
  > Migration: If your deployment relies on Dynamic Client Registration, explicitly enable it after upgrading through `PUT /api/v2/oauth2-provider/settings`. Existing registered clients are unaffected; only new registrations are gated.

- Agent external auth now resolves by template, not deployment config order (#27854)

  > For hostname-only external auth requests at runtime, such as Git operations through `GIT_ASKPASS`, Coder now resolves the provider from the workspace template's declared `coder_external_auth` entries instead of whichever deployment-configured provider matched last in config order. This is breaking for deployments that relied on provider ordering or stale template declarations, because ambiguous declared matches and declared-but-unconfigured providers now return HTTP 404 instead of silently substituting a different provider's token.
  >
  > Migration: If a template declares several providers that match the same host, give them non-overlapping `CODER_EXTERNAL_AUTH_<N>_REGEX` values or fetch the token with an explicit ID via `coder external-auth access-token <id>`. If a template declares a provider that was renamed or removed, restore that provider or update the template declaration. Declaring the intended provider in templates is recommended but not required.

- `login_type=none` deprecated; existing accounts converted to password login (#26851)

  > Legacy `login_type=none` users are deprecated in favor of service accounts. `POST /users` and `coder users create` now reject `login_type=none` and the deprecated `--disable-login` path unless a service account is explicitly requested, and migration `000554_legacy_none_login_to_password` converts existing non-system, non-service-account `login_type=none` users to `login_type=password` while preserving email addresses and existing API tokens.
  >
  > Migration: Existing affected users are converted automatically on upgrade. Update any automation that creates `login_type=none` users to create service accounts instead, and set a password only if a converted account needs interactive login.

- Coder Tasks disabled and deprecated (#28008)

  > Coder Tasks is disabled and deprecated. If `--hide-ai-tasks` remains in the Coder server command-line arguments, or `client.hideAITasks` remains in YAML configuration files, the Coder server will fail to start with an unknown-option error.
  >
  > Migration: Before upgrading, remove `--hide-ai-tasks` from the Coder server command-line arguments and remove `client.hideAITasks` from YAML configuration files.

- Coder Agents model configurations and overrides are organization-scoped (#27955, #27959, #28440, #28442, #28704)

  > Coder Agents model configurations and overrides are now organization-scoped. Existing model configurations are assigned to the default organization and are not copied to other organizations. Existing deployment-level and personal model overrides are not migrated. API integrations must use the organization-scoped model endpoints; the separate model-availability endpoint has been removed and its availability data is now included in the models response.
  >
  > Migration: Configure a usable default model for each organization that uses Coder Agents, and reselect any deployment-level and personal model overrides. Update API integrations to use the organization-scoped model endpoints and read availability data from the models response.

- MCP server configuration is organization-scoped (#27942)

  > MCP server configuration is now organization-scoped. Existing configurations retain their credentials and move to the default organization; other organizations must configure their own MCP servers.
  >
  > Migration: Configure MCP servers for each organization that needs them, and migrate API consumers from the deployment-scoped experimental routes to the organization-scoped routes.

- Native chat spend limits removed in favor of AI Gateway budgets (#27329)

  > Native chat spend limits are removed in favor of AI Gateway budgets. Limits are not migrated, and deployments without AI Gateway entitlement lose chat-spend enforcement.
  >
  > Migration: Move chat-spend enforcement to AI Gateway budgets. Deployments without AI Gateway entitlement will no longer enforce chat spend.
