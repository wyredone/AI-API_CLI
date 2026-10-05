# Claude CLI OpenRouter Manager

1. Install Windows Python 3.10 or newer with Tcl/Tk (standard installer defaults), and install Claude Code separately: https://code.claude.com/docs/en/setup
2. Extract all files and double-click `launch.bat`.
3. Add a named OpenRouter API key, select it, and click Activate.
4. Choose a project folder. Leave Claude executable blank for auto-detection, or browse to `claude.exe` or `claude.cmd`.
5. Leave Model blank to use Claude's model picker, or enter an OpenRouter model identifier. Claude models are recommended.
6. Click Launch Claude CLI. Inside Claude run `/status`; confirm the base URL is `https://openrouter.ai/api` and authentication uses `ANTHROPIC_AUTH_TOKEN`.
7. If you previously logged in with Claude, run `/logout`, quit that terminal, and launch again from this manager.

## Behavior

- Add, rename, replace, remove, test, activate and deactivate saved keys.
- One active key at a time; launching requires an active key.
- Test key checks authentication using OpenRouter's GET `/api/v1/key`; no model generation is performed.
- API keys use Windows DPAPI encryption tied to your Windows user account.
- Profiles are saved in `%LOCALAPPDATA%\ClaudeOpenRouterManager\profiles.json`. Encrypted profiles cannot be transferred to another Windows account as a key backup.
- Key is passed only in the launched process environment, not command arguments or project files.
- Deactivation and key removal affect future launches; terminals already running retain their key. Removal does not revoke the key on OpenRouter.
- The manager does not change Claude global or project configuration. It checks known user/project settings for conflicting provider variables and stops with file paths and variable names to resolve them. Organization-managed settings may also override routing; always verify `/status`.
- No third-party Python packages are required. No dependency installer is needed.
- Closing the GUI does not terminate Claude terminals.

## Compatibility and validation

Built for Windows. Syntax, environment isolation, settings conflict detection, and persistence logic have been checked in the build environment. Windows DPAPI, visual GUI behavior, and a live Claude/OpenRouter session must be verified on your Windows computer; no real API key was provided for an end-to-end test.

OpenRouter official integration: https://openrouter.ai/docs/guides/coding-agents/claude-code-integration
OpenRouter key authentication: https://openrouter.ai/docs/api/reference/authentication

## Fetch Models update

1. Click **Fetch Models** to load the live public OpenRouter catalog. The active key is used if available; no key is required to browse.
2. Search by name or model ID. Claude models are shown by default; clear that filter to browse other providers. Optionally filter for tool calling.
3. View context size and input/output USD prices per million tokens. These are catalog estimates, not a quote or proof of account access.
4. Select a model and click **Use selected model**, or double-click it. The model is saved and used for future Claude launches.
5. **Browse cached models** works offline after a successful fetch. Fetching failures preserve the previous catalog.
6. **Clear model selection** returns to Claude's model picker on the next launch.

Existing keys and preferences are retained in the same Windows profile location. Extract this update and replace the old app files.

Model listing endpoint: https://openrouter.ai/api/v1/models
Documentation: https://openrouter.ai/docs/quickstart

## Compatibility tests, browser upgrades, and usage

1. **Test selected model** checks OpenRouter's Anthropic Messages endpoint with a text response, a forced harmless tool call, and a tool-result round trip. It makes up to three small requests and asks you to approve potential API charges before starting. No commands or files are accessed. Results are saved per key and model as Passed, Failed or Untested. Replacing a key invalidates its old test results. A passed probe does not guarantee complete Claude CLI compatibility; streaming, reasoning, context limits and Claude's other tool types are not tested.
2. **Model browser** now supports favorites, twenty recent selections, provider filters, free-model and tool-support filters, and sorting by price or context length. Clear Claude only when viewing other providers. Batch-only models remain excluded. Prices are catalog estimates; zero text prices do not exclude other applicable fees.
3. **Usage & spending** reads the active key's daily, weekly, monthly and all-time OpenRouter usage, key spending cap and remaining cap. BYOK usage is displayed separately. Periods are OpenRouter's UTC periods. These are API snapshots, not live per-terminal totals.
4. Account balance is requested separately from the credits endpoint. If access is unavailable (management-key permissions may be required), the app says Unavailable. An unlimited key cap is not an unlimited account balance.
5. Set optional daily/monthly usage or remaining-key-cap alert thresholds, then click Save alerts. Blank or zero disables a threshold. Auto-refresh runs every sixty seconds only while the GUI remains open. Refresh intervals can be longer when a request is still running. Alerts do not block charges or enforce limits. Use the Key spending caps link for OpenRouter's hard caps.
6. The OpenRouter activity link opens the usage dashboard in your browser. Cached usage is marked stale after a refresh failure; last successful model catalog remains available offline.

Run offline regression tests from the project folder with `python -m unittest -v test_app`.

Validation: the offline tests cover catalog filtering/sorting, pricing, complete mocked tool round trips, failure states, credential-change invalidation, API error redaction, and spending alert boundaries. Windows GUI, DPAPI and real OpenRouter model requests require verification on your PC.

Official sources:
- https://openrouter.ai/docs/api/api-reference/anthropic-messages/create-messages
- https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key
- https://openrouter.ai/docs/api/api-reference/credits/get-credits
