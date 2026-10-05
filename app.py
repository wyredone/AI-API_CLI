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

BASE_URL = 'https://openrouter.ai/api'
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


def launch_env(key, model, parent=None):
    env = dict(os.environ if parent is None else parent)
    for name in list(env):
        if name.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_')) or name in {
                'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_SUBAGENT_MODEL'}:
            env.pop(name, None)
    env.update(ANTHROPIC_BASE_URL=BASE_URL, ANTHROPIC_AUTH_TOKEN=key,
               ANTHROPIC_API_KEY='', OPENROUTER_API_KEY=key,
               CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY='1')
    if model.strip():
        env['ANTHROPIC_MODEL'] = model.strip()
    return env


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


def filter_models(models, search='', claude_only=True, tools_only=False):
    query = search.strip().casefold()
    return [row for row in models
            if (not claude_only or row['id'].startswith('anthropic/claude-'))
            and (not tools_only or 'tools' in (row.get('supported_parameters') or []))
            and (not query or query in (row['id'] + ' ' + str(row.get('name', ''))).casefold())]


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
        self.title('Claude CLI • OpenRouter Key Manager')
        self.geometry('960x710')
        self.minsize(800, 580)
        self.store = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'ClaudeOpenRouterManager/profiles.json'
        self.data = {'version': 1, 'keys': [], 'active': None, 'project': str(Path.home()),
                     'cli': '', 'model': ''}
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
        self.status = tk.StringVar(value='Ready. Activate a key, select a project, then launch.')
        self.active_text = tk.StringVar()
        self.build()
        self.refresh()
        self.after(100, self.poll)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def build(self):
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Claude CLI / OpenRouter', font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='Windows-encrypted keys • Project launcher • No external Python packages').pack(anchor='w', pady=(2, 15))
        self.tree = ttk.Treeview(outer, columns=('name', 'key', 'state'), show='headings', height=8)
        for column, title, width in [('name', 'Key name', 300), ('key', 'Masked key', 220), ('state', 'Status', 150)]:
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
        for row, (label, variable, browse) in enumerate([
            ('Project folder', self.project, self.browse_project),
            ('Claude executable', self.cli, self.browse_cli),
            ('Model (optional)', self.model, None)]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=5, padx=(0, 12))
            ttk.Entry(form, textvariable=variable).grid(row=row, column=1, sticky='ew', pady=5)
            if browse:
                ttk.Button(form, text='Browse…', command=browse).grid(row=row, column=2, padx=(8, 0))
        ttk.Label(outer, text='Leave executable blank for auto-detection. Leave model blank to use Claude’s model picker.').pack(anchor='w', pady=5)
        model_actions = ttk.Frame(outer)
        model_actions.pack(fill='x', pady=4)
        self.fetch_button = ttk.Button(model_actions, text='Fetch Models', command=lambda: self.safe(self.fetch_models))
        self.fetch_button.pack(side='left')
        ttk.Button(model_actions, text='Browse cached models', command=lambda: self.safe(self.show_models)).pack(side='left', padx=8)
        ttk.Button(model_actions, text='Clear model selection', command=lambda: self.model.set('')).pack(side='left')
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
        self.data.update(project=self.project.get(), cli=self.cli.get(), model=self.model.get())
        atomic_json(self.store, self.data)
        self.status.set('Preferences saved.')

    def refresh(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for item in self.data['keys']:
            self.tree.insert('', 'end', iid=item['id'], values=(item['name'], item['mask'],
                             'ACTIVE' if item['id'] == self.data['active'] else 'Inactive'))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        active = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        self.active_text.set('Active key: ' + (active['name'] if active else 'None'))

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
        self.status.set('Key activated for future launches.')

    def deactivate(self):
        self.data['active'] = None
        self.save()
        self.refresh()
        self.status.set('Deactivated. Launch is disabled until a key is activated.')

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
        # The catalog is public; use the active key if one is selected.
        active = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        token = protect(active['secret'], decrypt=True) if active else None
        self.fetching_models = True
        self.fetch_button.configure(state='disabled')
        self.status.set('Fetching OpenRouter models…')
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

    def show_models(self):
        if not self.models:
            messagebox.showinfo('No model catalog', 'Click Fetch Models first. An API key is not required to browse the public catalog.')
            return
        if self.model_window is not None and self.model_window.winfo_exists():
            self.model_window.lift()
            return
        window = self.model_window = tk.Toplevel(self)
        window.title('OpenRouter model browser')
        window.geometry('1080x560')
        window.minsize(850, 420)
        window.transient(self)
        frame = ttk.Frame(window, padding=15)
        frame.pack(fill='both', expand=True)
        search = tk.StringVar()
        claude_only = tk.BooleanVar(value=True)
        tools_only = tk.BooleanVar(value=False)
        count = tk.StringVar()
        filters = ttk.Frame(frame)
        filters.pack(fill='x', pady=(0, 10))
        ttk.Label(filters, text='Search').pack(side='left')
        ttk.Entry(filters, textvariable=search, width=35).pack(side='left', padx=8)
        ttk.Checkbutton(filters, text='Claude models only', variable=claude_only).pack(side='left', padx=8)
        ttk.Checkbutton(filters, text='Tool calling', variable=tools_only).pack(side='left', padx=8)
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill='both', expand=True)
        table = ttk.Treeview(table_frame, columns=('id', 'name', 'context', 'input', 'output', 'tools'), show='headings')
        for col, title, width in [('id', 'Model ID', 280), ('name', 'Name', 240),
                ('context', 'Context tokens', 110), ('input', 'Input / 1M', 100),
                ('output', 'Output / 1M', 100), ('tools', 'Tools', 65)]:
            table.heading(col, text=title)
            table.column(col, width=width, minwidth=50)
        vertical = ttk.Scrollbar(table_frame, orient='vertical', command=table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=table.xview)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        def update(*_):
            rows = filter_models(self.models, search.get(), claude_only.get(), tools_only.get())
            table.delete(*table.get_children())
            for row in rows:
                pricing = row.get('pricing') or {}
                context = row.get('context_length')
                table.insert('', 'end', iid=row['id'], values=(row['id'], row.get('name', ''),
                    f'{context:,}' if isinstance(context, int) else 'N/A',
                    model_price(pricing.get('prompt')), model_price(pricing.get('completion')),
                    'Yes' if 'tools' in (row.get('supported_parameters') or []) else 'No'))
            count.set(f'{len(rows):,} shown / {len(self.models):,} fetched • Catalog: {self.models_fetched}')
        def choose(_event=None):
            selected = table.selection()
            if not selected:
                messagebox.showinfo('Select a model', 'Select a model from the list first.', parent=window)
                return
            model = selected[0]
            if not model.startswith('anthropic/claude-'):
                if not messagebox.askyesno('Model compatibility', 'Claude Code is optimized for Claude models. Other models may fail or behave differently. Select this model anyway?', parent=window):
                    return
            self.model.set(model)
            try:
                self.save()
            except OSError:
                messagebox.showerror('Save failed', 'Model selected but preferences could not be saved.', parent=window)
                return
            self.status.set('Selected model: ' + model + '. Used for future launches.')
            window.destroy()
        for variable in (search, claude_only, tools_only):
            variable.trace_add('write', update)
        table.bind('<Double-1>', choose)
        table.bind('<Return>', choose)
        ttk.Label(frame, textvariable=count).pack(anchor='w', pady=8)
        ttk.Label(frame, text='Catalog prices are estimates in USD. Availability, endpoint capabilities and actual billing can vary.', wraplength=1000).pack(anchor='w')
        bottom = ttk.Frame(frame)
        bottom.pack(fill='x', pady=10)
        ttk.Button(bottom, text='Use selected model', command=choose).pack(side='left')
        ttk.Button(bottom, text='Close', command=window.destroy).pack(side='right')
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
        item = next((x for x in self.data['keys'] if x['id'] == self.data['active']), None)
        if not item:
            raise ValueError('Activate an API key before launching.')
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
        env = launch_env(protect(item['secret'], decrypt=True), self.model.get())
        self.save()
        if Path(binary).suffix.lower() == '.exe':
            subprocess.Popen([binary], cwd=str(project), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            command = '"' + binary + '"'
            subprocess.Popen([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/k', command],
                             cwd=str(project), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.status.set('Claude launched. Run /status inside its terminal to verify OpenRouter routing.')

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
