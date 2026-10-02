"""Watercourse names: the distinctive words of a name, with Welsh names read as English.

OS Open Rivers names the Wye, Severn and Teme by their Welsh names ("Afon Gwy",
"Afon Hafren", "Afon Tefeidiad") even in England, while the Environment Agency's
gauges and spots.csv use the English ones. Without an alias the spot at
Symonds Yat on "Afon Gwy" was matched to the Garren Brook gauge rather than the
Wye's, and Ironbridge's "Afon Hafren" never matched "River Severn" (2 Oct 2026).
"""

from __future__ import annotations

import re

GENERIC = {"river", "afon", "beck", "brook", "water", "the", "stream", "burn", "canal", "lake", "mere",
           "tarn", "reservoir"}

# Welsh word -> English word, for the rivers OS Open Rivers itself names both ways
# (watercourse_name / watercourse_name_alternative). "Taf" is left out: OS gives it
# as the Welsh name of both the River Taf and the River Taff.
WELSH = {
    "gwy": "wye", "hafren": "severn", "tefeidiad": "teme", "wysg": "usk", "mynwy": "monnow",
    "tywi": "towy", "efyrnwy": "vyrnwy", "ieithon": "ithon", "nedd": "neath", "llwchwr": "loughor",
    "dyfrdwy": "dee", "dyfi": "dovey", "alun": "alyn", "rhymni": "rhymney", "ebwy": "ebbw",
    "ogwr": "ogmore", "afan": "avan", "troddi": "trothy", "ddawan": "thaw", "chwiler": "wheeler",
    "elái": "ely",
}


def river_words(name: str | None, welsh: bool = True) -> frozenset[str]:
    """The distinctive words of a watercourse name, Welsh words read as English:
    "River Wharfe" and "Wharfe" both give {wharfe}; "Afon Gwy" and "River Wye" both give {wye}.
    `welsh=False` leaves the Welsh words as they are."""
    words = re.findall(r"[^\W\d_]{2,}", (name or "").lower())   # letters only, so "o'" and "(1)" drop out
    return frozenset((WELSH.get(w, w) if welsh else w) for w in words if w not in GENERIC)


def same_river(a: str | None, b: str | None) -> bool:
    """True when two names share a distinctive word (after the Welsh aliases)."""
    return bool(river_words(a) & river_words(b))
