"""Editable starting points; each request produces an independent parameter set."""
from dataclasses import dataclass

from engine import (BANDS, DEFAULT_OUTPUT_DB, DEFAULT_RATIO, DEFAULT_RELEASE_MS,
                    DEFAULT_THRESHOLD_DB, Params)


@dataclass(frozen=True)
class Preset:
    label: str
    description: str
    threshold: float
    ratio: float
    releases: tuple
    output: float
    voices: bool = False


PRESETS = {
    'general': Preset(
        'General purpose',
        'Natural sound with gentler 4:1 leveling, slow bass recovery, and no added output gain. '
        'Voice leveling is off.', DEFAULT_THRESHOLD_DB, DEFAULT_RATIO, DEFAULT_RELEASE_MS,
        DEFAULT_OUTPUT_DB),
    'gentle-music': Preset(
        'Gentle music',
        'Lighter compression to retain more musical dynamics. Slow releases; voice leveling is off.',
        -18, 3, DEFAULT_RELEASE_MS, 3),
    'steady-music': Preset(
        'Steady music',
        'Stronger leveling with longer recovery to keep gain steadier across beats. '
        'Voice leveling is off.', -28, 10, (3000, 2225, 1651, 1225, 909, 674, 500), 8),
    'uneven-voices': Preset(
        'Uneven voices',
        'For podcasts or calls with quiet and loud voices. Enables overall volume leveling; '
        'it can affect music too.', -24, 10, (1000, 765, 585, 447, 342, 262, 200), 6, True),
}


def get_preset(key='general'):
    recipe = PRESETS[key]
    p = Params.default()
    for band in range(BANDS):
        p.threshold[band] = recipe.threshold
        p.release_ms[band] = recipe.releases[band]
    p.ratio = recipe.ratio
    p.output_db = recipe.output
    p.auto_level = float(recipe.voices)
    return p
