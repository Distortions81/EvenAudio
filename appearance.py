"""Small built-in ttk palettes and a persistent, user-local appearance preference."""
import json
import os
from pathlib import Path
import tempfile

PALETTES = {
    'dark': dict(bg='#15202e', fg='#e5edf6', muted='#a5b5c8', field='#203044',
                 surface='#293c52', border='#425871', accent='#55d6be', selected='#17685d'),
    'light': dict(bg='#f4f6fa', fg='#17283b', muted='#52657b', field='#ffffff',
                  surface='#e3eaf2', border='#aebdce', accent='#126d61', selected='#c7eee5'),
}


def preference_path():
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    if not base.is_absolute():
        base = Path.home() / '.config'
    return base / 'evenaudio/appearance.json'


def load_theme():
    try:
        value = json.loads(preference_path().read_text(encoding='utf-8')).get('theme')
        return value if value in PALETTES else 'dark'
    except (OSError, ValueError, AttributeError, TypeError):
        return 'dark'


def save_theme(theme):
    if theme not in PALETTES:
        return False
    temporary = None
    try:
        path = preference_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.appearance-', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({'theme': theme}, handle)
            handle.write('\n')
        temporary.replace(path)
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def apply_theme(root, theme):
    from tkinter import ttk
    colors = PALETTES[theme]
    style = ttk.Style(root)
    style.theme_use('clam')  # Unlike native themes, clam honors field/background colors.
    root.configure(background=colors['bg'])
    style.configure('.', background=colors['bg'], foreground=colors['fg'],
                    font=('sans-serif', 10), bordercolor=colors['border'],
                    lightcolor=colors['border'], darkcolor=colors['border'],
                    troughcolor=colors['surface'], selectbackground=colors['selected'],
                    selectforeground=colors['fg'])
    style.configure('Muted.TLabel', foreground=colors['muted'])
    style.configure('Title.TLabel', font=('sans-serif', 24, 'bold'))
    style.configure('TButton', padding=(10, 6), background=colors['surface'])
    style.map('TButton', background=[('active', colors['selected'])],
              foreground=[('disabled', colors['muted'])])
    style.configure('TCheckbutton', indicatorbackground=colors['field'], padding=(0, 3))
    style.map('TCheckbutton', background=[('active', colors['bg'])],
              indicatorbackground=[('selected', colors['accent']), ('!selected', colors['field'])])
    for widget in ('TSpinbox', 'TCombobox'):
        style.configure(widget, fieldbackground=colors['field'], background=colors['surface'],
                        foreground=colors['fg'], arrowcolor=colors['fg'], padding=4,
                        insertcolor=colors['fg'])
        style.map(widget, fieldbackground=[('disabled', colors['surface']),
                                          ('readonly', colors['field'])],
                  foreground=[('disabled', colors['muted']), ('readonly', colors['fg'])],
                  background=[('active', colors['selected'])],
                  selectbackground=[('!disabled', colors['selected'])],
                  selectforeground=[('!disabled', colors['fg'])])
    # Combobox popups are Tk listboxes rather than ttk widgets.
    root.option_add('*TCombobox*Listbox.background', colors['field'])
    root.option_add('*TCombobox*Listbox.foreground', colors['fg'])
    root.option_add('*TCombobox*Listbox.selectBackground', colors['selected'])
    root.option_add('*TCombobox*Listbox.selectForeground', colors['fg'])
