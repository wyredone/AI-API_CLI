"""Claude OpenRouter Manager — Python 3.10+, Windows, standard library only."""
# No pip packages are required. Tkinter is supplied by the Windows Python installer.
import base64
import ctypes
from ctypes import wintypes
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import urllib.request
import urllib.error
import uuid
import hashlib
import webbrowser

BASE_URL = 'https://openrouter.ai/api'
OLLAMA_BASE_URL = 'http://localhost:11434'
PROVIDER_OPENROUTER = 'openrouter'
PROVIDER_OLLAMA = 'ollama'
PROVIDER_LABELS = {PROVIDER_OPENROUTER: 'OpenRouter', PROVIDER_OLLAMA: 'Ollama'}
ENV_KEYS = {'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY',
            'ANTHROPIC_MODEL', 'CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL'}


def protect(value, decrypt=False):
    """Windows user-bound DPAPI; secrets never appear in command arguments."""
    if os.name != 'nt':
        raise RuntimeError('This version requires Windows to securely store API keys.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]
    raw = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    func.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = ctypes.string_at(target.data, target.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def is_batch_model(model):
    return 'batch' in model.strip().casefold().split(':')[1:]


def clean_launch_env(parent=None):
    env = dict(os.environ if parent is None else parent)
    for name in list(env):
        if name.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_')) or name in {
                'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_SUBAGENT_MODEL', 'OPENROUTER_API_KEY'}:
            env.pop(name, None)
    return env


def launch_env(key, model, parent=None, provider=PROVIDER_OPENROUTER, base_url=BASE_URL):
    if is_batch_model(model):
        raise ValueError('Batch-only models cannot run an interactive Claude session. Select the model without :batch.')
    env = clean_launch_env(parent)
    if provider == PROVIDER_OLLAMA:
        url = normalize_base_url(base_url or OLLAMA_BASE_URL)
        env.update(ANTHROPIC_BASE_URL=url, ANTHROPIC_AUTH_TOKEN=key or 'ollama',
                   ANTHROPIC_API_KEY='', CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY='1')
    else:
        env.update(ANTHROPIC_BASE_URL=BASE_URL, ANTHROPIC_AUTH_TOKEN=key,
                   ANTHROPIC_API_KEY='', OPENROUTER_API_KEY=key,
                   CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY='1')
    if model.strip():
        env['ANTHROPIC_MODEL'] = model.strip()
    return env


def normalize_base_url(value):
    url = (value or '').strip().rstrip('/')
    if not url:
        raise ValueError('Enter a base URL.')
    if not (url.startswith('http://') or url.startswith('https://')):
        raise ValueError('Base URL must start with http:// or https://')
    if any(c.isspace() for c in url):
        raise ValueError('Base URL cannot contain spaces.')
    return url


def settings_conflicts(project):
    paths = [Path.home() / '.claude/settings.json',
             project / '.claude/settings.json', project / '.claude/settings.local.json']
    conflicts = []
    for path in paths:
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            names = set(data.get('env', {}))
            names = sorted(n for n in names if n in ENV_KEYS or
                           n.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_')) or
                           n == 'CLAUDE_CODE_OAUTH_TOKEN')
            if names or data.get('apiKeyHelper'):
                conflicts.append(str(path) + '\n  ' + ', '.join(names or ['apiKeyHelper']))
    return conflicts


def normalize_models(payload):
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('Invalid model catalog response')
    models = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get('id'), str) and row['id']:
            models[row['id']] = row
    if not models:
        raise ValueError('The model catalog is empty')
    return sorted(models.values(), key=lambda row: row['id'].casefold())



def normalize_ollama_models(payload):
    rows = payload.get('models') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('Invalid Ollama model response')
    models = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get('name'), str) and row['name']:
            name = row['name']
            details = row.get('details') or {}
            models[name] = {'id': name, 'name': name, 'provider': 'ollama',
                            'modified_at': row.get('modified_at'),
                            'size': row.get('size'),
                            'family': details.get('family'),
                            'supported_parameters': []}
    if not models:
        raise ValueError('No local Ollama models were found')
    return sorted(models.values(), key=lambda row: row['id'].casefold())


def catalog_provider(row):
    return row.get('provider') or row['id'].split('/')[0]


def numeric(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def filter_models(models, search='', claude_only=True, tools_only=False,
                  provider='All', free_only=False, favorites=None,
                  view='All', recent=None, sort='Model ID'):
    query = search.strip().casefold()
    favorites = set(favorites or [])
    recent = list(recent or [])
    rows = [row for row in models
            if not is_batch_model(row['id'])
            and (not claude_only or row['id'].startswith('anthropic/claude-'))
            and (not tools_only or 'tools' in (row.get('supported_parameters') or []))
            and (provider == 'All' or catalog_provider(row) == provider)
            and (not free_only or (numeric((row.get('pricing') or {}).get('prompt')) == 0
                 and numeric((row.get('pricing') or {}).get('completion')) == 0
                 and numeric((row.get('pricing') or {}).get('request', '0')) == 0))
            and (view != 'Favorites' or row['id'] in favorites)
            and (view != 'Recent' or row['id'] in recent)
            and (not query or query in (row['id'] + ' ' + str(row.get('name', ''))).casefold())]
    def price_key(row, field):
        price = numeric((row.get('pricing') or {}).get(field))
        return price if price is not None and price >= 0 else Decimal('Infinity')
    if sort == 'Input price: low to high':
        rows.sort(key=lambda row: (price_key(row, 'prompt'), row['id']))
    elif sort == 'Output price: low to high':
        rows.sort(key=lambda row: (price_key(row, 'completion'), row['id']))
    elif sort == 'Context: high to low':
        rows.sort(key=lambda row: (-(numeric(row.get('context_length')) or 0), row['id']))
    elif sort == 'Name':
        rows.sort(key=lambda row: str(row.get('name', row['id'])).casefold())
    elif view == 'Recent':
        rows.sort(key=lambda row: recent.index(row['id']))
    else:
        rows.sort(key=lambda row: row['id'].casefold())
    return rows


def api_request(token, path, body=None):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'anthropic-version': '2023-06-01'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(BASE_URL + '/v1/' + path, headers=headers,
        data=json.dumps(body).encode('utf-8') if body is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read())
        if not isinstance(result, dict) or result.get('error'):
            raise ValueError('Invalid API response')
        return result
    except urllib.error.HTTPError as exc:
        descriptions = {401: 'Invalid or expired key', 402: 'Credit or spending limit reached',
                        403: 'Access denied', 404: 'Model or endpoint unavailable',
                        429: 'Rate limited'}
        raise RuntimeError(f'HTTP {exc.code}: ' + descriptions.get(exc.code, 'Request rejected')) from None
    except Exception:
        raise RuntimeError('Network error or invalid API response') from None


def probe_model(token, model):
    if not model or is_batch_model(model):
        raise ValueError('Select an interactive model first.')
    timestamp = datetime.now().astimezone().isoformat(timespec='seconds')
    result = {'status': 'Failed', 'response': 'Untested', 'tools': 'Untested',
              'round_trip': 'Untested', 'checked_at': timestamp, 'detail': ''}
    stage = 'response'
    try:
        plain = api_request(token, 'messages', {'model': model, 'max_tokens': 256,
            'messages': [{'role': 'user', 'content': 'Reply with only CHECK_OK.'}]})
        blocks = plain.get('content', [])
        if not isinstance(blocks, list) or not any(b.get('type') == 'text' and b.get('text', '').strip()
                                                  for b in blocks if isinstance(b, dict)):
            raise ValueError('No text response received.')
        result['response'] = 'Passed'
        stage = 'tools'
        nonce = uuid.uuid4().hex[:12]
        user = {'role': 'user', 'content': 'Call compatibility_ping with token ' + nonce + '.'}
        tool = {'name': 'compatibility_ping', 'description': 'A harmless compatibility check; no commands or file access.',
                'input_schema': {'type': 'object', 'properties': {'token': {'type': 'string', 'enum': [nonce]}},
                                 'required': ['token'], 'additionalProperties': False}}
        called = api_request(token, 'messages', {'model': model, 'max_tokens': 256,
            'messages': [user], 'tools': [tool],
            'tool_choice': {'type': 'tool', 'name': 'compatibility_ping'}})
        content = called.get('content', [])
        uses = [b for b in content if isinstance(b, dict) and b.get('type') == 'tool_use'
                and b.get('name') == 'compatibility_ping' and b.get('id')
                and b.get('input') == {'token': nonce}] if isinstance(content, list) else []
        if len(uses) != 1:
            result['tools'] = 'Failed'
            raise ValueError('Expected tool call and arguments were not returned.')
        result['tools'] = 'Passed'
        stage = 'round_trip'
        finish = api_request(token, 'messages', {'model': model, 'max_tokens': 256,
            'tools': [tool], 'tool_choice': {'type': 'none'}, 'messages': [user,
                {'role': 'assistant', 'content': content},
                {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': uses[0]['id'], 'content': 'PROBE_OK_' + nonce},
                    {'type': 'text', 'text': 'Reply with the exact PROBE_OK token returned by the tool.'}]}]})
        text = ' '.join(b.get('text', '') for b in finish.get('content', [])
                        if isinstance(b, dict) and b.get('type') == 'text')
        if 'PROBE_OK_' + nonce not in text:
            result['round_trip'] = 'Failed'
            raise ValueError('Tool result was not acknowledged correctly.')
        result.update(status='Passed', round_trip='Passed',
                      detail='Anthropic Messages response, forced tool call and tool-result round trip passed. Full Claude CLI compatibility is not guaranteed.')
    except Exception as exc:
        result[stage] = 'Failed'
        result['detail'] = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else 'Probe failed.'
    return result


def spending_alerts(key_info, limits):
    alerts = []
    for field, threshold, label, lower in [
        ('usage_daily', 'daily', 'UTC daily key usage', False),
        ('usage_monthly', 'monthly', 'UTC monthly key usage', False),
        ('limit_remaining', 'remaining', 'Key cap remaining', True)]:
        value, limit = numeric(key_info.get(field)), numeric(limits.get(threshold))
        if value is not None and limit is not None and limit > 0 and ((value <= limit) if lower else (value >= limit)):
            alerts.append(f'{label}: ${value:,.2f} (alert threshold ${limit:,.2f})')
    return alerts


def money(value, absent='Unavailable'):
    parsed = numeric(value)
    return f'${parsed:,.2f}' if parsed is not None else absent


def model_price(value):
    try:
        price = Decimal(str(value)) * 1000000
        if not price.is_finite() or price < 0:
            return 'N/A'
        return f'${price:,.2f}'
    except (InvalidOperation, ValueError, TypeError):
        return 'N/A'


class Manager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Claude CLI • API Provider Manager')
        self.geometry('1000x850')
        self.minsize(800, 580)
        self.store = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'ClaudeOpenRouterManager/profiles.json'
        self.data = {'version': 1, 'keys': [], 'active': None, 'project': str(Path.home()),
                     'cli': '', 'model': '', 'provider': PROVIDER_OPENROUTER, 'ollama_base_url': OLLAMA_BASE_URL,
                     'favorites': [], 'recent_models': [], 'model_tests': {},
                     'alert_limits': {'daily': '', 'monthly': '', 'remaining': ''}, 'auto_usage': False}
        try:
            if self.store.exists():
                loaded = json.loads(self.store.read_text(encoding='utf-8'))
                if loaded.get('version') != 1 or not isinstance(loaded.get('keys'), list):
                    raise ValueError('Unsupported or invalid profile file')
                self.data.update(loaded)
        except Exception as exc:
            messagebox.showerror('Cannot load profiles', f'{exc}\nThe original file will not be overwritten.')
            self.destroy()
            raise SystemExit(1)
        self.events = queue.Queue()
        self.testing_model = False
        self.usage_busy = False
        self.usage_window = None
        self.model_browser_refresh = None
        self.usage_by_key = {}
        self.model_test_text = tk.StringVar(value='Model compatibility: Untested')
        self.usage_text = tk.StringVar(value='Usage not refreshed.')
        self.usage_details = tk.StringVar(value='Activate an OpenRouter key and refresh usage.')
        self.auto_usage = tk.BooleanVar(value=self.data['auto_usage'])
        self.alert_vars = {name: tk.StringVar(value=self.data['alert_limits'].get(name, ''))
                           for name in ('daily', 'monthly', 'remaining')}
        self.alert_notified = set()
        self.models = []
        self.models_fetched = ''
        self.model_window = None
        self.fetching_models = False
        self.cache_path = self.store.with_name('models_cache.json')
        try:
            cached = json.loads(self.cache_path.read_text(encoding='utf-8'))
            self.models = normalize_models(cached)
            self.models_fetched = cached.get('fetched_at', 'Unknown')
        except (OSError, ValueError, TypeError):
            pass
        self.project = tk.StringVar(value=self.data['project'])
        self.cli = tk.StringVar(value=self.data['cli'])
        self.model = tk.StringVar(value=self.data['model'])
        self.provider = tk.StringVar(value=self.data.get('provider', PROVIDER_OPENROUTER))
        self.ollama_base_url = tk.StringVar(value=self.data.get('ollama_base_url', OLLAMA_BASE_URL))
        self.status = tk.StringVar(value='Ready. Activate a key, select a project, then launch.')
        self.active_text = tk.StringVar()
        self.build()
        self.refresh()
        self.after(100, self.poll)
        self.model.trace_add('write', lambda *_: self.update_test_label())
        self.provider.trace_add('write', lambda *_: self.refresh())
        self.after(60000, self.auto_refresh_usage)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def build(self):
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Claude CLI / API Provider Manager', font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='OpenRouter keys • Ollama local models • Project launcher • No external Python packages').pack(anchor='w', pady=(2, 15))
        self.tree = ttk.Treeview(outer, columns=('name', 'provider', 'key', 'state'), show='headings', height=8)
        for column, title, width in [('name', 'Key name', 280), ('provider', 'Provider', 110), ('key', 'Masked key', 220), ('state', 'Status', 150)]:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width)
        self.tree.pack(fill='both', expand=True)
        bar = ttk.Frame(outer)
        bar.pack(fill='x', pady=10)
        for label, command in [('Add key', self.add), ('Edit key', self.edit), ('Remove key', self.remove),
                               ('Activate', self.activate), ('Deactivate', self.deactivate), ('Test key', self.test)]:
            ttk.Button(bar, text=label, command=lambda c=command: self.safe(c)).pack(side='left', padx=(0, 7))
        ttk.Label(outer, textvariable=self.active_text, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(0, 10))
        form = ttk.Frame(outer)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text='Provider').grid(row=0, column=0, sticky='w', pady=5, padx=(0, 12))
        ttk.Combobox(form, textvariable=self.provider, values=[PROVIDER_OPENROUTER, PROVIDER_OLLAMA], state='readonly', width=20).grid(row=0, column=1, sticky='w', pady=5)
        for row, (label, variable, browse) in enumerate([
            ('Project folder', self.project, self.browse_project),
            ('Claude executable', self.cli, self.browse_cli),
            ('Provider base URL', self.ollama_base_url, None),
            ('Model (optional)', self.model, None)]):
            gui_row = row + 1
            ttk.Label(form, text=label).grid(row=gui_row, column=0, sticky='w', pady=5, padx=(0, 12))
            ttk.Entry(form, textvariable=variable).grid(row=gui_row, column=1, sticky='ew', pady=5)
            if browse:
                ttk.Button(form, text='Browse…', command=browse).grid(row=gui_row, column=2, padx=(8, 0))
        ttk.Label(outer, text='Use OpenRouter for API keys and catalog/usage. Use Ollama for local model discovery; Claude CLI requires the configured base URL to accept Anthropic Messages requests.').pack(anchor='w', pady=5)
        model_actions = ttk.Frame(outer)
        model_actions.pack(fill='x', pady=4)
        self.fetch_button = ttk.Button(model_actions, text='Fetch Models', command=lambda: self.safe(self.fetch_models))
        self.fetch_button.pack(side='left')
        ttk.Button(model_actions, text='Browse cached models', command=lambda: self.safe(self.show_models)).pack(side='left', padx=8)
        ttk.Button(model_actions, text='Clear model selection', command=lambda: self.model.set('')).pack(side='left')
        test_actions = ttk.Frame(outer)
        test_actions.pack(fill='x', pady=4)
        self.test_model_button = ttk.Button(test_actions, text='Test selected model', command=lambda: self.safe(self.test_model))
        self.test_model_button.pack(side='left')
        ttk.Label(test_actions, textvariable=self.model_test_text).pack(side='left', padx=10)
        usage = ttk.Frame(outer)
        usage.pack(fill='x', pady=4)
        ttk.Button(usage, text='OpenRouter usage & spending', command=lambda: self.safe(self.show_usage)).pack(side='left')
        ttk.Label(usage, textvariable=self.usage_text, wraplength=700).pack(side='left', padx=10)
        actions = ttk.Frame(outer)
        actions.pack(fill='x', pady=10)
        ttk.Button(actions, text='Launch Claude CLI', command=lambda: self.safe(self.launch)).pack(side='left')
        ttk.Button(actions, text='Save preferences', command=lambda: self.safe(self.save)).pack(side='left', padx=8)
        ttk.Button(actions, text='Help', command=self.help).pack(side='right')
        ttk.Label(outer, text='Deactivation affects future launches. Existing Claude terminals keep their current key.', wraplength=850).pack(anchor='w')
        ttk.Label(outer, textvariable=self.status, wraplength=850, foreground='#175b89').pack(anchor='w', pady=(12, 0))

    def safe(self, command):
        try:
            command()
        except Exception as exc:
            # Never display remote response bodies or decrypted credentials.
            messagebox.showerror('Action failed', str(exc))

    def save(self):
        limits = {name: var.get().strip() for name, var in self.alert_vars.items()}
        for value in limits.values():
            if value and (numeric(value) is None or numeric(value) < 0):
                raise ValueError('Spending thresholds must be nonnegative numbers, or blank to disable.')
        self.data.update(project=self.project.get(), cli=self.cli.get(), model=self.model.get(),
                         provider=self.provider.get(), ollama_base_url=self.ollama_base_url.get().strip() or OLLAMA_BASE_URL,
                         alert_limits=limits, auto_usage=self.auto_usage.get())
        atomic_json(self.store, self.data)
        self.status.set('Preferences saved.')

    def refresh(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for item in self.data['keys']:
            self.tree.insert('', 'end', iid=item['id'], values=(item['name'], PROVIDER_LABELS.get(item.get('provider', PROVIDER_OPENROUTER), item.get('provider', '')), item['mask'],
                             'ACTIVE' if item['id'] == self.data['active'] else 'Inactive'))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        active = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        self.active_text.set('Active OpenRouter key: ' + (active['name'] if active else 'None') + ' • Launch provider: ' + PROVIDER_LABELS.get(self.provider.get(), self.provider.get()))
        self.update_test_label()
        self.render_usage()
        if self.model_browser_refresh:
            self.model_browser_refresh()

    def selected(self):
        selection = self.tree.selection()
        if not selection:
            raise ValueError('Select a key first.')
        return next(x for x in self.data['keys'] if x['id'] == selection[0])

    def dialog(self, item=None):
        dialog = tk.Toplevel(self)
        dialog.title('Edit API key' if item else 'Add OpenRouter API key')
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill='both', expand=True)
        name = tk.StringVar(value=item['name'] if item else '')
        key = tk.StringVar()
        ttk.Label(frame, text='Key name').pack(anchor='w')
        name_entry = ttk.Entry(frame, textvariable=name, width=55)
        name_entry.pack(fill='x', pady=(3, 12))
        ttk.Label(frame, text='OpenRouter API key' + (' (leave blank to keep existing key)' if item else '')).pack(anchor='w')
        ttk.Entry(frame, textvariable=key, show='•', width=55).pack(fill='x', pady=(3, 12))
        ttk.Label(frame, text='Stored using Windows encryption for your Windows account.').pack(anchor='w')
        def accept():
            try:
                title, token = name.get().strip(), key.get().strip()
                if not title:
                    raise ValueError('Enter a key name.')
                if any(x['name'].casefold() == title.casefold() and x is not item for x in self.data['keys']):
                    raise ValueError('That key name already exists.')
                if token and (not token.startswith('sk-or-') or any(c.isspace() for c in token)):
                    raise ValueError('Enter a valid OpenRouter key beginning with sk-or-.')
                if not item and not token:
                    raise ValueError('Enter an API key.')
                record = dict(item) if item else {'id': str(uuid.uuid4())}
                record['name'] = title
                record['provider'] = PROVIDER_OPENROUTER
                if token:
                    record.update(secret=protect(token), mask='sk-or-…' + token[-4:])
                old = json.loads(json.dumps(self.data))
                if item:
                    self.data['keys'][self.data['keys'].index(item)] = record
                else:
                    self.data['keys'].append(record)
                try:
                    self.save()
                except Exception:
                    self.data = old
                    raise
                key.set('')
                dialog.destroy()
                self.refresh()
            except Exception as exc:
                messagebox.showerror('Cannot save key', str(exc), parent=dialog)
        ttk.Button(frame, text='Save key', command=accept).pack(anchor='e', pady=(15, 0))
        name_entry.focus_set()

    def add(self):
        self.dialog()

    def edit(self):
        self.dialog(self.selected())

    def remove(self):
        item = self.selected()
        if messagebox.askyesno('Remove saved key', f'Remove "{item["name"]}" from this manager?\nThis does not revoke the key at OpenRouter.'):
            self.data['keys'].remove(item)
            self.data['model_tests'].pop(item['id'], None)
            self.usage_by_key.pop(item['id'], None)
            if self.data['active'] == item['id']:
                self.data['active'] = None
            self.save()
            self.refresh()

    def activate(self):
        item = self.selected()
        protect(item['secret'], decrypt=True)
        self.data['active'] = item['id']
        self.save()
        self.refresh()
        self.status.set('OpenRouter key activated for future OpenRouter launches.')

    def deactivate(self):
        self.data['active'] = None
        self.save()
        self.refresh()
        self.status.set('OpenRouter key deactivated. Ollama launches do not require an OpenRouter key.')

    def test(self):
        item = self.selected()
        token = protect(item['secret'], decrypt=True)
        self.status.set('Testing key with OpenRouter…')
        def worker():
            try:
                request = urllib.request.Request(BASE_URL + '/v1/key', headers={'Authorization': 'Bearer ' + token})
                with urllib.request.urlopen(request, timeout=20) as response:
                    json.loads(response.read())
                result = 'Key authentication succeeded. Model access and available credit are checked by Claude when used.'
            except urllib.error.HTTPError as exc:
                result = f'Key test failed: HTTP {exc.code}. Check key validity and OpenRouter account status.'
            except Exception:
                result = 'Key test failed: connection or response error. Check your internet connection.'
            self.events.put(result)
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if isinstance(event, tuple):
                    if event[0] in {'probe', 'usage', 'usage_error'}:
                        self.handle_feature_event(event)
                        continue
                    self.fetching_models = False
                    self.fetch_button.configure(state='normal')
                    if event[0] == 'models':
                        self.models, self.models_fetched = event[1], event[2]
                        try:
                            atomic_json(self.cache_path, {'data': self.models, 'fetched_at': self.models_fetched})
                            self.status.set(f'Fetched {len(self.models):,} models. Choose one from the model browser.')
                        except OSError:
                            self.status.set('Models fetched; offline cache could not be saved.')
                        if self.model_window is not None and self.model_window.winfo_exists():
                            self.model_window.destroy()
                        self.show_models()
                    else:
                        self.status.set(event[1] + ' Previously cached models are still available.')
                else:
                    self.status.set(event)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def fetch_models(self):
        if self.fetching_models:
            return
        provider = self.provider.get()
        self.fetching_models = True
        self.fetch_button.configure(state='disabled')
        self.status.set('Fetching ' + PROVIDER_LABELS.get(provider, provider) + ' models…')
        if provider == PROVIDER_OLLAMA:
            base_url = normalize_base_url(self.ollama_base_url.get() or OLLAMA_BASE_URL)
            def worker():
                try:
                    request = urllib.request.Request(base_url + '/api/tags', headers={'Accept': 'application/json'})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        models = normalize_ollama_models(json.loads(response.read()))
                    self.events.put(('models', models, datetime.now().astimezone().strftime('%m-%d-%y %I:%M %p %Z')))
                except urllib.error.HTTPError as exc:
                    self.events.put(('error', f'Ollama model fetch failed: HTTP {exc.code}. Check the local Ollama service.'))
                except Exception:
                    self.events.put(('error', 'Ollama model fetch failed. Start Ollama and verify the base URL.'))
            threading.Thread(target=worker, daemon=True).start()
            return
        # The OpenRouter catalog is public; use the active key if one is selected.
        active = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        token = protect(active['secret'], decrypt=True) if active else None
        def worker():
            try:
                headers = {'Accept': 'application/json'}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                request = urllib.request.Request(BASE_URL + '/v1/models', headers=headers)
                with urllib.request.urlopen(request, timeout=25) as response:
                    models = normalize_models(json.loads(response.read()))
                self.events.put(('models', models, datetime.now().astimezone().strftime('%m-%d-%y %I:%M %p %Z')))
            except urllib.error.HTTPError as exc:
                self.events.put(('error', f'Model fetch failed: HTTP {exc.code}. Check the active key or try without an active key.'))
            except Exception:
                self.events.put(('error', 'Model fetch failed: network or catalog response error.'))
        threading.Thread(target=worker, daemon=True).start()

    def active_key(self):
        item = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        if not item:
            raise ValueError('Activate an OpenRouter API key first.')
        return item

    def credential_revision(self, item):
        return hashlib.sha256(item['secret'].encode()).hexdigest()

    def test_result(self, model):
        item = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        if not item:
            return None
        result = self.data['model_tests'].get(item['id'], {}).get(model)
        if result and result.get('revision') == self.credential_revision(item):
            return result
        return None

    def update_test_label(self):
        result = self.test_result(self.model.get().strip())
        text = 'Model compatibility: ' + (result['status'] if result else 'Untested')
        if result:
            text += ' • Checked ' + result.get('checked_at', '')
        self.model_test_text.set(text)

    def test_model(self, model=None):
        if self.testing_model:
            return
        model = model or self.model.get().strip()
        if not model or is_batch_model(model):
            raise ValueError('Select an interactive model first.')
        if self.provider.get() == PROVIDER_OLLAMA:
            raise ValueError('Model compatibility probes currently use OpenRouter. For Ollama, test by launching Claude CLI against an Anthropic-compatible local endpoint.')
        item = self.active_key()
        if not messagebox.askyesno('Run paid compatibility test?',
                'This sends up to three small model requests through OpenRouter and may incur API charges. '
                'It tests text response, tool calling and tool-result handling, without running commands.\n\n'
                'Model: ' + model + '\nProceed?'):
            return
        token = protect(item['secret'], decrypt=True)
        key_id, revision = item['id'], self.credential_revision(item)
        self.testing_model = True
        self.test_model_button.configure(state='disabled')
        self.status.set('Testing model response and tool handling…')
        def worker():
            result = probe_model(token, model)
            result['revision'] = revision
            self.events.put(('probe', key_id, model, result))
        threading.Thread(target=worker, daemon=True).start()

    def handle_feature_event(self, event):
        if event[0] == 'probe':
            self.testing_model = False
            self.test_model_button.configure(state='normal')
            _, key_id, model, result = event
            item = next((x for x in self.data['keys'] if x['id'] == key_id), None)
            if item is None or self.credential_revision(item) != result['revision']:
                self.status.set('Test result discarded because its key was removed or changed.')
                return
            self.data['model_tests'].setdefault(key_id, {})[model] = result
            try:
                self.save()
            except Exception:
                self.status.set('Test completed but its result could not be saved.')
            self.update_test_label()
            if self.model_browser_refresh:
                self.model_browser_refresh()
            messagebox.showinfo('Model probe: ' + result['status'],
                model + '\n\nText response: ' + result['response'] + '\nTool calling: ' + result['tools'] +
                '\nTool-result handling: ' + result['round_trip'] + '\n\n' + result['detail'] +
                '\n\nChecked: ' + result['checked_at'])
        elif event[0] in {'usage', 'usage_error'}:
            self.usage_busy = False
            if event[0] == 'usage_error':
                active = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
                if active and event[1] == active['id'] and event[2] == self.credential_revision(active):
                    previous = self.usage_by_key.get(event[1])
                    if previous:
                        previous['stale'] = True
                        self.render_usage()
                    self.usage_text.set('Usage refresh failed; any displayed snapshot is stale.')
                    self.status.set(event[3])
                return
            _, key_id, revision, snapshot = event
            item = next((x for x in self.data['keys'] if x['id'] == key_id), None)
            if item is None or self.credential_revision(item) != revision:
                return
            snapshot['revision'] = revision
            self.usage_by_key[key_id] = snapshot
            self.render_usage()
            if key_id == self.data['active']:
                alerts = spending_alerts(snapshot['key'], {k: v.get() for k, v in self.alert_vars.items()})
                new_alerts = []
                for alert in alerts:
                    category = alert.split(':')[0]
                    signature = (key_id, revision, snapshot['checked_at'][:10], category)
                    if signature not in self.alert_notified:
                        self.alert_notified.add(signature)
                        new_alerts.append(alert)
                if new_alerts:
                    messagebox.showwarning('Spending alert', '\n'.join(new_alerts) +
                        '\n\nThese are local alerts; they do not stop spending. Configure hard key caps on OpenRouter.')

    def refresh_usage(self):
        if self.usage_busy:
            return
        item = self.active_key()
        token = protect(item['secret'], decrypt=True)
        key_id, revision = item['id'], self.credential_revision(item)
        self.usage_busy = True
        self.usage_text.set('Refreshing usage…')
        def worker():
            try:
                key = api_request(token, 'key').get('data')
                if not isinstance(key, dict):
                    raise ValueError('Key usage response is unavailable.')
                balance = None
                credit_note = 'Account balance unavailable: account credits may require a management key.'
                try:
                    credit = api_request(token, 'credits').get('data', {})
                    total, used = numeric(credit.get('total_credits')), numeric(credit.get('total_usage'))
                    if total is not None and used is not None:
                        balance = str(total - used)
                        credit_note = 'Account balance reported from purchased credits minus account usage.'
                except Exception:
                    pass
                self.events.put(('usage', key_id, revision, {'key': key, 'balance': balance,
                    'credit_note': credit_note, 'checked_at': datetime.now().astimezone().isoformat(timespec='seconds')}))
            except Exception as exc:
                self.events.put(('usage_error', key_id, revision, str(exc) if isinstance(exc, (RuntimeError, ValueError)) else 'Usage refresh failed.'))
        threading.Thread(target=worker, daemon=True).start()

    def auto_refresh_usage(self):
        if self.auto_usage.get() and self.data['active'] and not self.usage_busy:
            self.safe(self.refresh_usage)
        self.after(60000, self.auto_refresh_usage)

    def render_usage(self):
        item = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        snapshot = self.usage_by_key.get(item['id']) if item else None
        if not snapshot or snapshot.get('revision') != self.credential_revision(item):
            self.usage_text.set('Usage not refreshed for the active key.')
            self.usage_details.set('Activate an OpenRouter key and click Refresh usage.')
            return
        key = snapshot['key']
        remaining = money(key.get('limit_remaining'), 'No key cap' if key.get('limit') is None else 'Unavailable')
        self.usage_text.set('Daily: ' + money(key.get('usage_daily')) + ' • Monthly: ' + money(key.get('usage_monthly')) + ' • Key cap remaining: ' + remaining)
        lines = ['Key: ' + item['name'], 'Snapshot: ' + snapshot['checked_at'] + (' (STALE: latest refresh failed)' if snapshot.get('stale') else ''),
                 '', 'OpenRouter-reported key usage (USD):',
                 'Current UTC day: ' + money(key.get('usage_daily')),
                 'Current UTC week: ' + money(key.get('usage_weekly')),
                 'Current UTC month: ' + money(key.get('usage_monthly')),
                 'All time: ' + money(key.get('usage')),
                 'BYOK all time (separate): ' + money(key.get('byok_usage')),
                 '', 'Key spending cap: ' + money(key.get('limit'), 'No cap configured'),
                 'Key cap remaining: ' + remaining,
                 'Cap reset: ' + str(key.get('limit_reset') or 'No reset'),
                 'BYOK included in cap: ' + ('Yes' if key.get('include_byok_in_limit') else 'No'),
                 '', 'Account balance: ' + money(snapshot['balance']), snapshot['credit_note'],
                 '', 'Usage is a fetched snapshot, not a live Claude session total. Periods are defined by OpenRouter in UTC.']
        alerts = spending_alerts(key, {k: v.get() for k, v in self.alert_vars.items()})
        if alerts:
            lines += ['', 'ALERTS:'] + alerts
        self.usage_details.set('\n'.join(lines))

    def show_usage(self):
        if self.usage_window is not None and self.usage_window.winfo_exists():
            self.usage_window.lift()
            return
        window = self.usage_window = tk.Toplevel(self)
        window.title('OpenRouter usage and spending')
        window.geometry('850x700')
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, textvariable=self.usage_details, justify='left', wraplength=800).pack(anchor='w')
        limits = ttk.LabelFrame(frame, text='Local alert thresholds in USD (blank or 0 disables)', padding=10)
        limits.pack(fill='x', pady=12)
        for i, (key, label) in enumerate([('daily', 'Daily usage reaches'), ('monthly', 'Monthly usage reaches'),
                                         ('remaining', 'Key cap remaining below')]):
            ttk.Label(limits, text=label).grid(row=i, column=0, sticky='w', pady=3)
            ttk.Entry(limits, textvariable=self.alert_vars[key], width=15).grid(row=i, column=1, padx=12)
        ttk.Checkbutton(frame, text='Auto-refresh active key every 60 seconds while this app is open', variable=self.auto_usage).pack(anchor='w')
        ttk.Label(frame, text='Alerts do not enforce spending limits. Set hard limits in OpenRouter key settings.', wraplength=800).pack(anchor='w', pady=8)
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='Refresh usage', command=lambda: self.safe(self.refresh_usage)).pack(side='left')
        ttk.Button(actions, text='Save alerts', command=lambda: self.safe(self.save)).pack(side='left', padx=8)
        ttk.Button(actions, text='OpenRouter activity', command=lambda: webbrowser.open('https://openrouter.ai/activity')).pack(side='left', padx=8)
        ttk.Button(actions, text='Key spending caps', command=lambda: webbrowser.open('https://openrouter.ai/settings/keys')).pack(side='left')
        self.render_usage()
        if self.data['active']:
            self.safe(self.refresh_usage)

    def show_models(self):
        if not self.models:
            messagebox.showinfo('No model catalog', 'Click Fetch Models first.')
            return
        if self.model_window is not None and self.model_window.winfo_exists():
            self.model_window.lift()
            return
        window = self.model_window = tk.Toplevel(self)
        window.title(PROVIDER_LABELS.get(self.provider.get(), self.provider.get()) + ' model browser')
        window.geometry('1250x650')
        window.minsize(950, 500)
        frame = ttk.Frame(window, padding=15)
        frame.pack(fill='both', expand=True)
        search = tk.StringVar()
        claude_only = tk.BooleanVar(value=self.provider.get() != PROVIDER_OLLAMA)
        tools_only = tk.BooleanVar(value=False)
        free_only = tk.BooleanVar(value=False)
        provider = tk.StringVar(value='All')
        view = tk.StringVar(value='All')
        sort = tk.StringVar(value='Model ID')
        count = tk.StringVar()
        filters = ttk.Frame(frame)
        filters.pack(fill='x', pady=(0, 8))
        ttk.Label(filters, text='Search').pack(side='left')
        ttk.Entry(filters, textvariable=search, width=35).pack(side='left', padx=8)
        for label, var in [('Claude only', claude_only), ('Tool calling', tools_only), ('Free only', free_only)]:
            ttk.Checkbutton(filters, text=label, variable=var).pack(side='left', padx=8)
        second = ttk.Frame(frame)
        second.pack(fill='x', pady=(0, 10))
        for label, var, values, width in [
            ('Provider', provider, ['All'] + sorted({catalog_provider(r) for r in self.models}), 18),
            ('View', view, ['All', 'Favorites', 'Recent'], 14),
            ('Sort', sort, ['Model ID', 'Name', 'Input price: low to high', 'Output price: low to high', 'Context: high to low'], 28)]:
            ttk.Label(second, text=label).pack(side='left', padx=(0, 5))
            ttk.Combobox(second, textvariable=var, values=values, state='readonly', width=width).pack(side='left', padx=(0, 15))
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill='both', expand=True)
        table = ttk.Treeview(table_frame, columns=('favorite', 'id', 'name', 'context', 'input', 'output', 'tools', 'test'), show='headings')
        for col, title, width in [('favorite', 'Fav', 45), ('id', 'Model ID', 280), ('name', 'Name', 230),
                ('context', 'Context', 90), ('input', 'Input / 1M', 95), ('output', 'Output / 1M', 95),
                ('tools', 'Tools', 55), ('test', 'API probe', 90)]:
            table.heading(col, text=title)
            table.column(col, width=width, minwidth=40)
        vertical = ttk.Scrollbar(table_frame, orient='vertical', command=table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=table.xview)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        def update(*_):
            if not window.winfo_exists():
                return
            old = table.selection()
            rows = filter_models(self.models, search.get(), claude_only.get(), tools_only.get(),
                                 provider.get(), free_only.get(), self.data['favorites'], view.get(),
                                 self.data['recent_models'], sort.get())
            table.delete(*table.get_children())
            for row in rows:
                pricing = row.get('pricing') or {}
                context = row.get('context_length')
                test = self.test_result(row['id'])
                table.insert('', 'end', iid=row['id'], values=(
                    '★' if row['id'] in self.data['favorites'] else '', row['id'], row.get('name', ''),
                    f'{context:,}' if isinstance(context, int) else 'N/A',
                    model_price(pricing.get('prompt')), model_price(pricing.get('completion')),
                    'Yes' if 'tools' in (row.get('supported_parameters') or []) else 'No',
                    test['status'] if test else 'Untested'))
            if old and table.exists(old[0]):
                table.selection_set(old[0])
            count.set(f'{len(rows):,} shown / {len(self.models):,} fetched • Catalog: {self.models_fetched}')
        self.model_browser_refresh = update
        def selected():
            rows = table.selection()
            if not rows:
                raise ValueError('Select a model from the list first.')
            return rows[0]
        def favorite():
            model = selected()
            favorites = self.data['favorites']
            if model in favorites:
                favorites.remove(model)
            else:
                favorites.append(model)
            self.save()
            update()
        def choose(_event=None):
            try:
                model = selected()
                if self.provider.get() != PROVIDER_OLLAMA and not model.startswith('anthropic/claude-') and not messagebox.askyesno('Model compatibility',
                    'Other providers may not fully work with Claude Code. Select this model anyway?', parent=window):
                    return
                self.model.set(model)
                recent = self.data['recent_models']
                self.data['recent_models'] = [model] + [r for r in recent if r != model][:19]
                self.save()
                self.status.set('Selected model: ' + model + '. Used for future launches.')
                window.destroy()
                self.model_browser_refresh = None
            except Exception as exc:
                messagebox.showerror('Model selection failed', str(exc), parent=window)
        def close_browser():
            self.model_browser_refresh = None
            window.destroy()
        window.protocol('WM_DELETE_WINDOW', close_browser)
        for variable in (search, claude_only, tools_only, free_only, provider, view, sort):
            variable.trace_add('write', update)
        table.bind('<Double-1>', choose)
        table.bind('<Return>', choose)
        ttk.Label(frame, textvariable=count).pack(anchor='w', pady=8)
        ttk.Label(frame, text='OpenRouter prices are catalog estimates. Ollama rows are local models with no OpenRouter price. API probe results apply to OpenRouter active keys. Claude CLI with Ollama needs an Anthropic-compatible local endpoint.', wraplength=1200).pack(anchor='w')
        bottom = ttk.Frame(frame)
        bottom.pack(fill='x', pady=10)
        ttk.Button(bottom, text='Use selected model', command=choose).pack(side='left')
        ttk.Button(bottom, text='Toggle favorite', command=lambda: self.safe(favorite)).pack(side='left', padx=8)
        ttk.Button(bottom, text='Test model', command=lambda: self.safe(lambda: self.test_model(selected()))).pack(side='left')
        ttk.Button(bottom, text='Close', command=close_browser).pack(side='right')
        update()

    def browse_project(self):
        result = filedialog.askdirectory(initialdir=self.project.get() if Path(self.project.get()).is_dir() else str(Path.home()))
        if result:
            self.project.set(result)

    def browse_cli(self):
        result = filedialog.askopenfilename(title='Select claude.exe or claude.cmd', filetypes=[('Claude executable', '*.exe *.cmd *.bat'), ('All files', '*')])
        if result:
            self.cli.set(result)

    def launch(self):
        if is_batch_model(self.model.get()):
            raise ValueError('The saved model is batch-only. Fetch Models and select its version without :batch before launching.')
        provider = self.provider.get()
        item = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        if provider == PROVIDER_OPENROUTER and not item:
            raise ValueError('Activate an OpenRouter API key before launching.')
        project = Path(self.project.get()).expanduser().resolve()
        if not project.is_dir():
            raise ValueError('Select an existing project folder.')
        conflicts = settings_conflicts(project)
        if conflicts:
            raise ValueError('Existing Claude settings can override the selected key. Remove the listed provider settings first; keep unrelated settings.\n\n' + '\n\n'.join(conflicts))
        binary = self.cli.get().strip() or shutil.which('claude.exe') or shutil.which('claude')
        if not binary:
            candidate = Path.home() / '.local/bin/claude.exe'
            if candidate.is_file():
                binary = str(candidate)
        if not binary or not Path(binary).is_file():
            raise ValueError('Claude CLI was not found. Install Claude Code, or browse to claude.exe / claude.cmd.')
        binary = str(Path(binary).resolve())
        if os.name != 'nt':
            raise ValueError('This launcher requires Windows.')
        if any(c in binary for c in '\"&|<>^%\r\n!'):
            raise ValueError('Executable path contains shell characters. Install Claude in a simple path.')
        if Path(binary).suffix.lower() not in {'.exe', '.cmd', '.bat'}:
            raise ValueError('Choose a Windows executable, CMD, or BAT file.')
        token = protect(item['secret'], decrypt=True) if item else ''
        env = launch_env(token, self.model.get(), provider=provider, base_url=self.ollama_base_url.get())
        self.save()
        if Path(binary).suffix.lower() == '.exe':
            subprocess.Popen([binary], cwd=str(project), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            command = '"' + binary + '"'
            subprocess.Popen([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/k', command],
                             cwd=str(project), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        chosen = self.model.get().strip()
        if chosen:
            self.data['recent_models'] = [chosen] + [r for r in self.data['recent_models'] if r != chosen][:19]
            self.save()
        self.status.set('Claude launched. Run /status inside its terminal to verify provider routing.')

    def help(self):
        messagebox.showinfo('Help', '1. Install Python 3.10+ and Claude Code on Windows.\n'
            '2. Add your OpenRouter key and activate it.\n3. Select your project folder.\n'
            '4. Launch Claude CLI and run /status.\n\n'
            'If a cached Claude login conflicts, run /logout in Claude, quit, and relaunch here.\n'
            'Use /model to choose models. Claude models are recommended.\n\n'
            'Activation only controls terminals launched by this app. It does not modify global '
            'environment variables or Claude settings. Existing terminals retain their key.\n'
            'Removing a key deletes its saved entry; revoke it on OpenRouter to invalidate it.\n'
            'Saved profiles: ' + str(self.store))

    def close(self):
        try:
            self.save()
        except Exception as exc:
            messagebox.showerror('Preferences not saved', str(exc))
            return
        self.destroy()


if __name__ == '__main__':
    if os.name != 'nt':
        raise SystemExit('This app requires Windows for encrypted key storage and terminal launching.')
    Manager().mainloop()
