"""Regional category schemes: which grades a case uses, and what they mean.

A unit outside the United States grades under its own country's or agency's
scheme, with its own codes, and those codes are what its prosecutors and
partner agencies read. Until v2026.11.317 every case here was seeded with
the Project VIC 2.0 (US) scheme and nothing else, so an examiner in the UK or
Australia graded in a scheme their reports then had to be translated from.

A case records the region it was created under (meta ``category_region``) and
keeps it for life: the codes stored on its files only mean something under
that scheme, so a case is never re-seeded with another. A case from before
regions existed is a US case. New cases take the region chosen in Settings.

The schemes other than the US are GLEAPP's (gleapp/regions.py, 2026.5.7, MIT,
Copyright (c) 2026 charpy4n6, Alexis Brignoni), whose authors took them from
the category sets the agencies themselves distribute. The US scheme is this
program's own: Project VIC 2.0 with the codes 5-9 it has always had, and
Uncategorized at 8.

Three things a scheme says that the rest of the program asks:

* ``uncategorized`` -- what a file starts as. Not 0: in several schemes 0 is
  a finding (Non-Pertinent) and in others it is the starting point.
* ``nonpertinent`` -- where a known-good (NSRL) hit may land; None when the
  scheme names no such category, and the file then stays where it is.
* ``vic_in`` / ``vic_out`` -- how a Project VIC (VICS) category maps to and
  from this scheme. Only the US scheme is VICS's own. Elsewhere only
  Non-Pertinent is carried, where the scheme has one: VICS 1 "Child Abuse
  Material" and, say, the UK's "SC Category A" are not the same legal test,
  and an export that equated them would misstate a finding in a file other
  agencies read. An unmapped grade exports with a null Category, which is
  what VICS means by "not categorized".
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_REGION = "US"
_UNCAT = "uncat"


@dataclass(frozen=True)
class Preset:
    code: int
    name: str
    color: str
    notable: bool
    key: str
    position: int


@dataclass(frozen=True)
class Scheme:
    id: str
    label: str
    presets: tuple
    uncategorized: int
    nonpertinent: int | None
    vic_in: dict = field(default_factory=dict)
    vic_out: dict = field(default_factory=dict)

    @property
    def codes(self) -> frozenset:
        return frozenset(p.code for p in self.presets)

    @property
    def first_custom(self) -> int:
        """The lowest code an examiner's own category may take.

        Never below 10, so a US case keeps the custom codes it always had.
        """
        return max(10, max(self.codes) + 1)

    @property
    def notable_codes(self) -> tuple:
        return tuple(sorted(p.code for p in self.presets if p.notable))

    def name(self, code: int) -> str:
        return next((p.name for p in self.presets if p.code == code), "")

    def severity_rank(self, code: int) -> tuple:
        """Sort key: the most severe grade first.

        The scheme's notable (illegal) grades first, in the scheme's own
        order; then the rest; Non-Pertinent last. For the US scheme this is
        the order it has always had: 1, 2, 3, then 4-9, then 0.
        """
        by_code = {p.code: p for p in self.presets}
        preset = by_code.get(code)
        if preset is None:
            return (False, 1, 1000 + int(code))
        return (code == self.nonpertinent, 0 if preset.notable else 1,
                preset.position)

    def rows(self) -> list:
        """(code, key, name, color) in display order, as casedb seeds them."""
        return [(p.code, p.key, p.name, p.color) for p in self.presets]

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label,
                "uncategorized": self.uncategorized,
                "nonpertinent": self.nonpertinent,
                "first_custom": self.first_custom,
                "notable": list(self.notable_codes),
                "vics_mapped": sorted(self.vic_out),
                "categories": [{"code": p.code, "name": p.name, "key": p.key,
                                "color": p.color, "notable": p.notable}
                               for p in self.presets]}


def _scheme(rid: str, label: str, rows, nonpertinent=None, vic=None) -> Scheme:
    presets, uncat = [], None
    for position, row in enumerate(rows):
        code, name, color, notable, key = row[:5]
        if len(row) > 5 and row[5] == _UNCAT:
            uncat = code
        presets.append(Preset(code, name, color, notable, key, position))
    assert uncat is not None, rid
    if vic is None:
        vic = {} if nonpertinent is None else {nonpertinent: 0}
    return Scheme(rid, label, tuple(presets), uncat, nonpertinent,
                  vic_in={v: k for k, v in vic.items()}, vic_out=dict(vic))


# This program's own US scheme, in code order (severity_rank depends on it).
_US = [
    (0, "Non-Pertinent",                              "#16a34a", False, "0"),
    (1, "Child Abuse Material (CAM)",                 "#dc2626", True,  "1"),
    (2, "Child Exploitive (non-CAM) / Age Difficult", "#ea580c", True,  "2"),
    (3, "CGI Animation - Child Exploitive",           "#d97706", True,  "3"),
    (4, "Comparison Images",                          "#2563eb", False, "4"),
    (5, "Adult Pornography",                          "#7c3aed", False, "5"),
    (6, "Abnormal Adult Porn",                        "#9333ea", False, "6"),
    (7, "Notable",                                    "#0891b2", False, "7"),
    (8, "Uncategorized",                              "#6b7280", False, "8", _UNCAT),
    (9, "Suspect Pictures",                           "#be123c", False, "9"),
]

REGIONS = {s.id: s for s in [
    _scheme("US", "United States (Project VIC)", _US, nonpertinent=0,
            vic={0: 0, 1: 1, 2: 2, 3: 3, 4: 4}),
    _scheme("AU", "Australia", [
        (1, "1. CAM - Real Child Pre-Pubescent <13yo", "#ff0000", True, "1"),
        (2, "2. CAM - Other Illegal Content, Child <18yo", "#ff8000", True, "2"),
        (3, "3. Investigative Interest", "#ffff00", False, "3"),
        (4, "4. Ignorable", "#00c000", False, "4"),
        (0, "0. Unchecked", "#8b93a3", False, "0", _UNCAT),
    ]),
    _scheme("AT", "Austria", [
        (0, "Nicht Kategorisiert", "#8b93a3", False, "0", _UNCAT),
        (1, "CSEM", "#ff0000", True, "1"),
        (2, "Präferenz", "#ffff00", False, "2"),
        (3, "Nicht Strafbar", "#00ff00", False, "3"),
    ]),
    _scheme("CA-EN", "Canada (English)", [
        (1, "Child Pornography", "#ff0000", True, "1"),
        (2, "Investigative Interest", "#ffff00", False, "2"),
        (3, "Other", "#008000", False, "3"),
        (0, "Unknown", "#8b93a3", False, "0", _UNCAT),
    ]),
    _scheme("CA-FR", "Canada (French)", [
        (1, "Pornographie Juvénile", "#ff0000", True, "1"),
        (2, "Images d’intérêt", "#ffff00", False, "2"),
        (3, "Autre Matériel", "#008000", False, "3"),
        (0, "Inconnu", "#8b93a3", False, "0", _UNCAT),
    ]),
    _scheme("DK", "Denmark", [
        (7, "NEW", "#8b93a3", False, "n", _UNCAT),
        (24, "DK KNOWN", "#00ff00", False, "k"),
        (8, "REVIEW", "#ffff00", False, "4"),
        (21, "DK CP CAT1", "#ff8080", True, "o"),
        (22, "DK CP CAT2", "#ff0000", True, "p"),
        (23, "DK CP CAT3", "#c00000", True, "q"),
        (10, "KNOWN LOCAL", "#00ff00", False, "0"),
        (11, "CAT1 LOCAL", "#ff8080", True, "1"),
        (12, "CAT2 LOCAL", "#ff0000", True, "2"),
        (13, "CAT3 LOCAL", "#c00000", True, "3"),
    ]),
    _scheme("EE", "Estonia", [
        (3, "Lapsporno", "#ff6347", True, "1"),
        (5, "Räige lapsporno", "#800000", True, "2"),
        (4, "OK", "#6b8e23", False, "3"),
        (2, "Tundmatu", "#8b93a3", False, "4", _UNCAT),
        (7, "???", "#c0c0c0", False, "5"),
        (8, "Võimalik uus ohver (LP)", "#ffd700", True, "6"),
    ]),
    _scheme("EUROPOL", "Europol", [
        (1, "CSEM", "#ff0000", True, "1"),
        (2, "INTEL", "#ffff00", False, "2"),
        (3, "NON PERTINENT", "#7cfc00", False, "3"),
        (0, "UNCATEGORISED", "#8b93a3", False, "0", _UNCAT),
    ], nonpertinent=3),
    _scheme("FR", "France", [
        (3, "Pédopornographique", "#ff0000", True, "1"),
        (4, "non illicite", "#6b8e23", False, "7"),
        (7, "indetermine", "#c0c0c0", False, "5"),
        (2, "Unknown", "#8b93a3", False, "4", _UNCAT),
        (5, "Nudite mineur", "#ff8c00", True, "2"),
        (8, "Mineur non illégal victime dans illegal", "#ffd700", False, "3"),
        (0, "representation de mineur", "#e0e0e0", True, "6"),
    ]),
    _scheme("IE", "Ireland", [
        (0, "Uncategorized", "#8b93a3", False, "0", _UNCAT),
        (1, "Child Sexual", "#f00000", True, "1"),
        (2, "Child Exposure", "#0070c0", True, "2"),
        (3, "Child Indicative", "#00b050", False, "3"),
        (4, "Child Ánime", "#ffff00", True, "4"),
        (5, "For Review", "#7030a0", False, "5"),
        (6, "Adult", "#938953", False, "6"),
        (7, "Evidential", "#c3d69b", False, "7"),
        (8, "Known Images", "#494429", False, "8"),
        (9, "Body Parts", "#494429", False, "9"),
    ]),
    _scheme("KR", "South Korea", [
        (0, "해당사항없음 (Non-Pertinent)", "#c0ffc0", False, "0"),
        (1, "아동음란물 영상 - 불법 (Child Abuse Material (Illegal))", "#ffc0c0", True, "1"),
        (2, "아동음란물 의심 영상 (Child Exploitive (non-CAM) / Age Difficult)", "#ffffc0", False, "2"),
        (3, "애니메이션 (CGI / Animation - Child Exploitive)", "#e0e0e0", False, "3"),
        (4, "불법 촬영 영상 (Illegal video shooting)", "#f0f8ff", True, "4"),
        (5, "미분류 (Uncategorized)", "#8b93a3", False, "5", _UNCAT),
    ], nonpertinent=0),
    _scheme("NL", "Netherlands", [
        (1, "Kinderporno", "#ff0000", True, "1"),
        (2, "Overig", "#008000", False, "2"),
        (3, "Dierenporno", "#ff8c00", True, "3"),
        (4, "Vraagteken", "#ffff00", False, "4"),
        (0, "Ongeclassificeerd", "#8b93a3", False, "0", _UNCAT),
        (5, "Onderzoek", "#0070c0", False, "5"),
        (6, "Bekeken - niet geclassificeerd", "#00ffff", False, "6"),
    ]),
    _scheme("NO", "Norway", [
        (0, "IKKE KATEGORISERT", "#8b93a3", False, "0", _UNCAT),
        (1, "ULOVLIG", "#ff0000", True, "1"),
        (2, "ANNET", "#00ff00", False, "2"),
        (3, "Filer av interesse", "#ffff00", False, "3"),
        (4, "Ulovlig annen kilde", "#ffa07a", False, "4"),
        (5, "Mulig", "#00bfff", False, "5"),
        (6, "Annet annen kilde", "#8fbc8b", False, "6"),
    ]),
    _scheme("RO", "Romania", [
        (0, "Necunoscut", "#8b93a3", False, "0", _UNCAT),
        (1, "Materiale Pornografice Minori", "#ff0000", True, "1"),
        (2, "Minori - Nuditate", "#ffa500", False, "2"),
        (3, "Alte materiale relevante", "#ffff00", False, "3"),
        (4, "Irelevant", "#008000", False, "4"),
    ]),
    _scheme("SE", "Sweden", [
        (6, "Särskilt hänsynslöst", "#ff0000", True, "2"),
        (3, "Övergreppsmaterial", "#ffff00", True, "1"),
        (4, "Ej relevant", "#008000", False, "3"),
        (7, "???", "#c0c0c0", False, "7"),
        (2, "Ej bedömt", "#8b93a3", False, "0", _UNCAT),
        (5, "Svårbedömt", "#ff8000", False, "4"),
        (8, "Stöder identifiering", "#17365d", False, "5"),
        (1, "Färdigpuberterad <18 år", "#800080", False, "6"),
    ]),
    _scheme("CH", "Switzerland", [
        (1, "KiPo", "#8b0000", True, "1"),
        (2, "VKiPo", "#8b0000", True, "2"),
        (3, "Zoophilie", "#8b0000", True, "3"),
        (0, "Unknown", "#8b93a3", False, "0", _UNCAT),
        (4, "Sexuelle_Gewalt", "#ff6347", True, "4"),
        (5, "Verbotene_Gewalt", "#ff6347", True, "5"),
        (6, "Praeferenzindikatoren", "#ffff80", True, "6"),
        (7, "Kind", "#80ff80", False, "7"),
        (8, "Unbedeutend", "#80ff80", False, "8"),
    ]),
    _scheme("UK", "United Kingdom", [
        (1, "SC Category A", "#ff6347", True, "1"),
        (2, "SC Category B", "#6b8e23", True, "2"),
        (3, "SC Category C", "#ffc0ff", True, "3"),
        (4, "Prohibited Images of Children", "#4682b4", True, "4"),
        (5, "Extreme Pornography", "#800000", True, "5"),
        (6, "Indicative / Borderline", "#ffd700", False, "6"),
        (7, "Unconfirmed", "#d8d8d8", False, "7"),
        (8, "Ignorable / Discounted", "#ffffff", False, "8"),
        (9, "Supports Identification", "#000080", False, "9"),
        (0, "Uncategorised", "#8b93a3", False, "0", _UNCAT),
    ]),
]}


def normalize(region) -> str:
    """The region's id, or the default for one unset or unknown."""
    rid = str(region or "").strip().upper()
    return rid if rid in REGIONS else DEFAULT_REGION


def get(region) -> Scheme:
    """The scheme for ``region``; US when it is unset or unknown."""
    return REGIONS[normalize(region)]


def is_region(region) -> bool:
    return str(region or "").strip().upper() in REGIONS


def choices() -> list:
    """Every scheme for the picker: US first, then by name."""
    rest = sorted((s for s in REGIONS.values() if s.id != DEFAULT_REGION),
                  key=lambda s: s.label)
    return [s.to_json() for s in [REGIONS[DEFAULT_REGION], *rest]]
