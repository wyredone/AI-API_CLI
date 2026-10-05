# Claude CLI API Provider Manager

1. Install Windows Python 3.10 or newer with Tcl/Tk (standard installer defaults), and install Claude Code separately: https://code.claude.com/docs/en/setup
2. Extract all files and double-click `launch.bat`.
3. Choose **openrouter** or **ollama** in the Provider field.
4. For OpenRouter, add a named API key, select it, and click Activate. For Ollama, start Ollama locally and use `http://localhost:11434` unless you run a different local endpoint.
5. Choose a project folder. Leave Claude executable blank for auto-detection, or browse to `claude.exe` or `claude.cmd`.
6. Leave Model blank to use Claude's model picker, or fetch/select a provider model.
7. Click Launch Claude CLI. Inside Claude run `/status`; confirm the base URL and model match the provider you selected.
8. If you previously logged in with Claude, run `/logout`, quit that terminal, and launch again from this manager.

## Provider support

- **OpenRouter** launches Claude CLI with `ANTHROPIC_BASE_URL=https://openrouter.ai/api`, `ANTHROPIC_AUTH_TOKEN`, `OPENROUTER_API_KEY`, and the selected model when one is set.
- **Ollama** fetches local models from `http://localhost:11434/api/tags` and launches Claude CLI with the configured local base URL and selected local model.
- Claude CLI sends Anthropic Messages-style requests. Plain Ollama commonly exposes OpenAI-style endpoints, so direct Ollama may reject Claude CLI requests unless your local URL is an Anthropic-compatible Ollama bridge/proxy. The app still helps you discover local Ollama models and launch Claude against the local endpoint you choose.
- OpenRouter usage, key tests, model compatibility probes, and spending alerts apply only to OpenRouter keys.

## Behavior

- Add, rename, replace, remove, test, activate and deactivate saved OpenRouter keys.
- One OpenRouter key can be active at a time. OpenRouter launches require an active key; Ollama launches do not.
- Test key checks authentication using OpenRouter's GET `/api/v1/key`; no model generation is performed.
- API keys use Windows DPAPI encryption tied to your Windows user account.
- Profiles are saved in `%LOCALAPPDATA%\ClaudeOpenRouterManager\profiles.json`. Encrypted profiles cannot be transferred to another Windows account as a key backup.
- Keys are passed only in the launched process environment, not command arguments or project files.
- Deactivation and key removal affect future launches; terminals already running retain their environment. Removal does not revoke the key on OpenRouter.
- The manager does not change Claude global or project configuration. It checks known user/project settings for conflicting provider variables and stops with file paths and variable names to resolve them. Organization-managed settings may also override routing; always verify `/status`.
- No third-party Python packages are required. No dependency installer is needed.
- Closing the GUI does not terminate Claude terminals.

## Compatibility and validation

Built for Windows. Syntax, environment isolation, settings conflict detection, Ollama model parsing, and persistence logic have been checked in the build environment. Windows DPAPI, visual GUI behavior, and live Claude/OpenRouter/Ollama sessions must be verified on your Windows computer; no real API key or local Ollama service was provided for an end-to-end test.

OpenRouter official integration: https://openrouter.ai/docs/guides/coding-agents/claude-code-integration
OpenRouter key authentication: https://openrouter.ai/docs/api/reference/authentication
Ollama API tags endpoint: https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models

## Fetch Models

1. Click **Fetch Models** to load models for the selected provider.
2. OpenRouter loads the live public OpenRouter catalog. The active key is used if available; no key is required to browse.
3. Ollama loads locally installed models from the configured base URL's `/api/tags` endpoint. Start Ollama first with `ollama serve` if it is not already running.
4. Search by name or model ID. Claude models are shown by default for OpenRouter; Ollama browsing shows local models by default.
5. OpenRouter rows show context size and input/output USD prices per million tokens when available. Ollama rows show local model IDs and do not have OpenRouter pricing.
6. Select a model and click **Use selected model**, or double-click it. The model is saved and used for future Claude launches.
7. **Browse cached models** works offline after a successful fetch. Fetching failures preserve the previous catalog.
8. **Clear model selection** returns to Claude's model picker on the next launch.

Existing keys and preferences are retained in the same Windows profile location. Extract this update and replace the old app files.

## Compatibility tests, browser upgrades, and usage

1. **Test selected model** checks OpenRouter's Anthropic Messages endpoint with a text response, a forced harmless tool call, and a tool-result round trip. It makes up to three small requests and asks you to approve potential API charges before starting. No commands or files are accessed. Results are saved per key and model as Passed, Failed or Untested. Replacing a key invalidates its old test results. A passed probe does not guarantee complete Claude CLI compatibility; streaming, reasoning, context limits and Claude's other tool types are not tested.
2. **Model browser** supports favorites, twenty recent selections, provider filters, free-model and tool-support filters, and sorting by price or context length. Batch-only OpenRouter models remain excluded. Prices are catalog estimates; zero text prices do not exclude other applicable fees.
3. **Usage & spending** reads the active OpenRouter key's daily, weekly, monthly and all-time usage, key spending cap and remaining cap. BYOK usage is displayed separately. Periods are OpenRouter's UTC periods. These are API snapshots, not live per-terminal totals.
4. Account balance is requested separately from the credits endpoint. If access is unavailable (management-key permissions may be required), the app says Unavailable. An unlimited key cap is not an unlimited account balance.
5. Set optional daily/monthly usage or remaining-key-cap alert thresholds, then click Save alerts. Blank or zero disables a threshold. Auto-refresh runs every sixty seconds only while the GUI remains open. Refresh intervals can be longer when a request is still running. Alerts do not block charges or enforce limits. Use the Key spending caps link for OpenRouter's hard caps.
6. The OpenRouter activity link opens the usage dashboard in your browser. Cached usage is marked stale after a refresh failure; last successful model catalog remains available offline.

Run offline regression tests from the project folder with `python -m unittest -v test_app`.

Validation: the offline tests cover catalog filtering/sorting, pricing, complete mocked tool round trips, failure states, credential-change invalidation, API error redaction, spending alert boundaries, Ollama model parsing, Ollama base URL validation, and Ollama launch environment setup. Windows GUI, DPAPI and real provider requests require verification on your PC.

Official sources:
- https://openrouter.ai/docs/api/api-reference/anthropic-messages/create-messages
- https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key
- https://openrouter.ai/docs/api/api-reference/credits/get-credits
- https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models
