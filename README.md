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
