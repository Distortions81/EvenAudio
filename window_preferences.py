"""Saved window behavior, independent of session-only audio controls."""
import json
from pathlib import Path
import tempfile

from appearance import preference_path

DEFAULTS = {'keep_running': True, 'close_notice_seen': False}


def load_preferences():
    try:
        data = json.loads(preference_path().with_name('window.json').read_text(encoding='utf-8'))
        return {key: data[key] if type(data.get(key)) is bool else value
                for key, value in DEFAULTS.items()}
    except (OSError, ValueError, AttributeError, TypeError):
        return DEFAULTS.copy()


def save_preferences(preferences):
    temporary = None
    try:
        path = preference_path().with_name('window.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.window-', delete=False) as handle:
            temporary = handle.name
            json.dump({key: preferences[key] for key in DEFAULTS}, handle)
            handle.write('\n')
        Path(temporary).replace(path)
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
