# -*- coding: utf-8 -*-
"""Reine Python-Logik des ALKIS-Suchmoduls.

Dieses Modul hat bewusst keine QGIS-Abhängigkeiten, damit es ohne QGIS
getestet werden kann (siehe ``tests/``). Es enthält:

* Konfiguration der unterstützten Bundesländer und WFS-Dienste
* Laden und Durchsuchen der Gemarkungslisten
* Normalisierung und Prüfung der Benutzereingaben
* Aufbau der WFS-Abfragen (Filter-XML wird sauber maskiert)
* Hilfsfunktionen zur Auswertung der Serverantwort
"""

import difflib
import json
import os
import re
import urllib.parse
import zipfile
from collections import namedtuple
from xml.sax.saxutils import escape, unescape

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_TAG = "ALKIS-Suchmodul"

# Fallback-KBS, falls ein Shapefile ohne .prj geliefert wird. Alle
# verwendeten Dienste liefern ETRS89 / UTM 32N.
STANDARD_CRS = "EPSG:25832"

# Schutz gegen übergroße ZIP-Antworten (entpackte Größe in Bytes)
MAX_ENTPACKT = 200 * 1024 * 1024

# Maximale Stellenzahl der numerischen Eingaben
MAX_STELLEN_FLUR = 3
MAX_STELLEN_ZAEHLER = 5
MAX_STELLEN_NENNER = 5

Bundesland = namedtuple(
    "Bundesland",
    [
        "name",            # Anzeigename, z. B. "Nordrhein-Westfalen"
        "kuerzel",         # Kürzel für den Layernamen
        "praefix",         # Landesschlüssel, mit dem Gemarkungsschlüssel beginnen
        "json_datei",      # Datei mit den Gemarkungen
        "wfs_url",         # WFS-Endpunkt
        "modus",           # "kennzeichen" (Filter auf flstkennz) oder "attribute"
        "version",         # WFS-Version
        "typename_param",  # Schreibweise des TYPENAME-Parameters (dienstspezifisch)
        "filter_stil",     # "ogc" (WFS 1.1) oder "fes" (WFS 2.0)
    ],
)

BUNDESLAENDER = {
    "Nordrhein-Westfalen": Bundesland(
        "Nordrhein-Westfalen", "NRW", "05", "gemarkungen_nrw.json",
        "https://www.wfs.nrw.de/geobasis/wfs_nw_alkis_vereinfacht",
        "kennzeichen", "1.1.0", "TYPENAME", "ogc",
    ),
    "Niedersachsen": Bundesland(
        "Niedersachsen", "NI", "03", "gemarkungen_nieder.json",
        "https://opendata.lgln.niedersachsen.de/doorman/noauth/alkis_wfs_einfach",
        "kennzeichen", "1.1.0", "typename", "ogc",
    ),
    "Hessen": Bundesland(
        "Hessen", "HE", "06", "gemarkungen_hessen.json",
        "https://www.gds.hessen.de/wfs2/aaa-suite/cgi-bin/alkis/vereinf/wfs",
        "kennzeichen", "2.0.0", "TYPENAMES", "fes",
    ),
    "Rheinland-Pfalz": Bundesland(
        "Rheinland-Pfalz", "RLP", "07", "gemarkungen_rlp.json",
        "https://www.geoportal.rlp.de/registry/wfs/519",
        "attribute", "2.0.0", "TYPENAMES", "fes",
    ),
}

AVE_NAMESPACE = "http://repository.gdi-de.org/schemas/adv/produkt/alkis-vereinfacht/2.0"
OUTPUT_FORMAT = "application/x-zip-shapefile"


class SucheFehler(ValueError):
    """Fehler mit einer für Anwender lesbaren Meldung."""


Suchanfrage = namedtuple(
    "Suchanfrage",
    ["url", "layername", "kennzeichen", "hinweis", "bundesland", "gemarkung_name"],
)


# ---------------------------------------------------------------------------
# Gemarkungen
# ---------------------------------------------------------------------------

def lade_gemarkungen(bundesland, verzeichnis=PLUGIN_DIR):
    """Lädt die Gemarkungsliste eines Bundeslandes.

    Wirft ``SucheFehler`` bei unbekanntem Bundesland und ``OSError`` bzw.
    ``ValueError`` bei Problemen mit der Datei.
    """
    konfig = BUNDESLAENDER.get(bundesland)
    if konfig is None:
        raise SucheFehler("Unbekanntes Bundesland: {}".format(bundesland))
    pfad = os.path.join(verzeichnis, konfig.json_datei)
    with open(pfad, "r", encoding="utf-8") as datei:
        daten = json.load(datei)
    if not isinstance(daten, dict):
        raise ValueError("{} hat ein unerwartetes Format".format(konfig.json_datei))
    return daten


def gemarkungsnamen_fuer_completer(gemarkungen):
    """Sortierte Liste der Anzeigenamen ("Name (Nummer)") für die Autovervollständigung."""
    return sorted((eintrag["full_name"] for eintrag in gemarkungen.values()), key=str.casefold)


def finde_gemarkungen(eingabe, gemarkungen):
    """Sucht Gemarkungen zur Benutzereingabe.

    Akzeptiert werden (ohne Beachtung der Groß-/Kleinschreibung):

    * ``Name (1234)``  - eindeutiger Anzeigename
    * ``051234``       - vollständiger 6-stelliger Schlüssel
    * ``1234``         - 4-stellige Gemarkungsnummer
    * ``Name``         - nur der Name (kann mehrdeutig sein!)

    Rückgabe: Liste von ``(schluessel, eintrag)``-Tupeln.
    """
    text = (eingabe or "").strip()
    if not text or not gemarkungen:
        return []

    treffer_anzeige = re.match(r"^(.*?)\s*\((\d{4})\)$", text)
    if treffer_anzeige:
        name = treffer_anzeige.group(1).strip().casefold()
        nummer = treffer_anzeige.group(2)
        return [
            (schluessel, eintrag)
            for schluessel, eintrag in gemarkungen.items()
            if eintrag["nummer"] == nummer and eintrag["name"].casefold() == name
        ]

    if re.fullmatch(r"[0-9]{6}", text):
        eintrag = gemarkungen.get(text)
        return [(text, eintrag)] if eintrag else []

    if re.fullmatch(r"[0-9]{4}", text):
        return [
            (schluessel, eintrag)
            for schluessel, eintrag in gemarkungen.items()
            if eintrag["nummer"] == text
        ]

    name = text.casefold()
    return [
        (schluessel, eintrag)
        for schluessel, eintrag in gemarkungen.items()
        if eintrag["name"].casefold() == name
    ]


def aehnliche_gemarkungen(eingabe, gemarkungen, anzahl=5):
    """Vorschläge ("Name (Nummer)") für eine nicht gefundene Eingabe."""
    text = (eingabe or "").strip().casefold()
    if not text:
        return []
    nach_name = {}
    for eintrag in gemarkungen.values():
        nach_name.setdefault(eintrag["name"].casefold(), eintrag["full_name"])

    vorschlaege = []
    for name in difflib.get_close_matches(text, list(nach_name), n=anzahl, cutoff=0.6):
        vorschlaege.append(nach_name[name])
    if len(vorschlaege) < anzahl:
        for name, anzeige in nach_name.items():
            if text in name and anzeige not in vorschlaege:
                vorschlaege.append(anzeige)
                if len(vorschlaege) >= anzahl:
                    break
    return vorschlaege


def anzahl_gleicher_namen(gemarkungen, name):
    """Wie viele Gemarkungen tragen diesen Namen?"""
    gesucht = name.casefold()
    return sum(1 for eintrag in gemarkungen.values() if eintrag["name"].casefold() == gesucht)


def gueltiger_gemarkungsschluessel(schluessel, bundesland):
    """Prüft Länge, Ziffern und Landespräfix des Gemarkungsschlüssels."""
    konfig = BUNDESLAENDER.get(bundesland)
    return bool(
        konfig
        and re.fullmatch(r"[0-9]{6}", schluessel or "")
        and schluessel.startswith(konfig.praefix)
    )


# ---------------------------------------------------------------------------
# Eingaben
# ---------------------------------------------------------------------------

def parse_zahl(text, bezeichnung, max_stellen):
    """Wandelt eine Ziffernfolge in eine normalisierte Zahl (``int``) um.

    Führende Nullen sind erlaubt ("012" == "12").
    """
    wert = (text or "").strip()
    if not re.fullmatch(r"[0-9]+", wert):
        raise SucheFehler("{} muss eine Zahl sein.".format(bezeichnung))
    zahl = int(wert)
    if zahl >= 10 ** max_stellen:
        raise SucheFehler("{} darf höchstens {} Stellen haben.".format(bezeichnung, max_stellen))
    return zahl


def erstelle_flurstueckskennzeichen(gem_schluessel, flur, zaehler, nenner=None):
    """Baut das Flurstückskennzeichen (``flstkennz``) der vereinfachten ALKIS-Dienste.

    Format (unverändert aus Version 1.3.0 übernommen)::

        <Gemarkung 6><Flur 3><Zähler 5>______             ohne Nenner
        <Gemarkung 6><Flur 3><Zähler 5>/<Nenner 5>______  mit Nenner
    """
    kennz = "{}{:03d}{:05d}".format(gem_schluessel, int(flur), int(zaehler))
    if nenner is None:
        return kennz + "______"
    return "{}/{:05d}______".format(kennz, int(nenner))


# ---------------------------------------------------------------------------
# WFS-Abfragen
# ---------------------------------------------------------------------------

def _literal(wert):
    """Maskiert einen Wert für die Verwendung als XML-Text."""
    return escape(str(wert))


def _filter_kennzeichen(stil, kennzeichen):
    wert = _literal(kennzeichen)
    if stil == "fes":
        return (
            '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" '
            'xmlns:ave="{ns}">'
            "<fes:PropertyIsEqualTo>"
            "<fes:ValueReference>ave:flstkennz</fes:ValueReference>"
            "<fes:Literal>{wert}</fes:Literal>"
            "</fes:PropertyIsEqualTo>"
            "</fes:Filter>"
        ).format(ns=AVE_NAMESPACE, wert=wert)
    return (
        '<Filter xmlns="http://www.opengis.net/ogc" xmlns:ave="{ns}">'
        "<PropertyIsEqualTo>"
        "<PropertyName>ave:flstkennz</PropertyName>"
        "<Literal>{wert}</Literal>"
        "</PropertyIsEqualTo>"
        "</Filter>"
    ).format(ns=AVE_NAMESPACE, wert=wert)


def _filter_attribute(gemarkung, flur, zaehler, nenner):
    bedingungen = [
        ("gemarkung", gemarkung),
        ("flur", "Flur {}".format(flur)),
        ("flstnrzae", zaehler),
    ]
    if nenner is not None:
        bedingungen.append(("flstnrnen", nenner))
    teile = "".join(
        "<PropertyIsEqualTo><ValueReference>{feld}</ValueReference>"
        "<Literal>{wert}</Literal></PropertyIsEqualTo>".format(feld=feld, wert=_literal(wert))
        for feld, wert in bedingungen
    )
    return '<Filter xmlns="http://www.opengis.net/fes/2.0"><And>{}</And></Filter>'.format(teile)


def erstelle_abruf_url(konfig, filter_xml):
    """Setzt die GetFeature-URL zusammen (Parameterschreibweise je Dienst)."""
    if konfig.modus == "attribute":
        url = "{}?SERVICE=WFS&VERSION={}&REQUEST=GetFeature".format(konfig.wfs_url, konfig.version)
        url += "&{}=ave:Flurstueck".format(konfig.typename_param)
        url += "&SRSNAME=urn:ogc:def:crs:EPSG::25832"
        url += "&OUTPUTFORMAT={}".format(OUTPUT_FORMAT)
    else:
        url = "{}?SERVICE=WFS&VERSION={}".format(konfig.wfs_url, konfig.version)
        url += "&REQUEST=GetFeature&{}=ave:Flurstueck".format(konfig.typename_param)
        url += "&OUTPUTFORMAT={}".format(OUTPUT_FORMAT)
    return url + "&FILTER=" + urllib.parse.quote(filter_xml)


def erstelle_layername(konfig, gemarkung_anzeige, flur, zaehler, nenner=None):
    """Name des Ergebnislayers, z. B. ``NRW - Altena - Flur 3 - Flurstück 12/4``."""
    ortsteil = gemarkung_anzeige.split("(")[0].strip()
    name = "{} - {} - Flur {} - Flurstück {}".format(konfig.kuerzel, ortsteil, flur, zaehler)
    if nenner is not None:
        name += "/{}".format(nenner)
    return name


def bereite_suche_vor(bundesland, gemarkung_text, flur_text, zaehler_text, nenner_text, gemarkungen):
    """Prüft alle Eingaben und erzeugt die Abfrage.

    Wirft ``SucheFehler`` mit einer verständlichen Meldung, wenn etwas fehlt,
    ungültig oder mehrdeutig ist.
    """
    konfig = BUNDESLAENDER.get(bundesland)
    if konfig is None:
        raise SucheFehler("Unbekanntes Bundesland: {}".format(bundesland))
    if not (gemarkung_text or "").strip():
        raise SucheFehler("Bitte eine Gemarkung angeben.")
    if not gemarkungen:
        raise SucheFehler("Keine Gemarkungsdaten für {} gefunden!".format(bundesland))

    try:
        flur = parse_zahl(flur_text, "Flur", MAX_STELLEN_FLUR)
        zaehler = parse_zahl(zaehler_text, "Der Zähler", MAX_STELLEN_ZAEHLER)
        nenner = None
        if (nenner_text or "").strip():
            nenner = parse_zahl(nenner_text, "Der Nenner", MAX_STELLEN_NENNER)
    except SucheFehler:
        raise
    except ValueError:  # pragma: no cover - parse_zahl wirft nur SucheFehler
        raise SucheFehler("Bitte nur Zahlen für Flur, Zähler und Nenner eingeben!")

    treffer = finde_gemarkungen(gemarkung_text, gemarkungen)
    if not treffer:
        meldung = "Gemarkung '{}' nicht gefunden!".format(gemarkung_text.strip())
        vorschlaege = aehnliche_gemarkungen(gemarkung_text, gemarkungen)
        if vorschlaege:
            meldung += " Meintest du: {}?".format(", ".join(vorschlaege))
        raise SucheFehler(meldung)
    if len(treffer) > 1:
        optionen = sorted((eintrag["full_name"] for _, eintrag in treffer), key=str.casefold)
        beispiele = ", ".join(optionen[:6]) + (" …" if len(optionen) > 6 else "")
        raise SucheFehler(
            "Der Name '{}' kommt {}-mal vor. Bitte eine Gemarkung aus der "
            "Vorschlagsliste wählen, z. B.: {}".format(gemarkung_text.strip(), len(optionen), beispiele)
        )

    schluessel, eintrag = treffer[0]
    if not gueltiger_gemarkungsschluessel(schluessel, bundesland):
        raise SucheFehler("Ungültiger Gemarkungsschlüssel!")

    hinweis = None
    if konfig.modus == "attribute":
        kennzeichen = "{} / Flur {} / {}{}".format(
            eintrag["name"], flur, zaehler, "/{}".format(nenner) if nenner is not None else ""
        )
        filter_xml = _filter_attribute(eintrag["name"], flur, zaehler, nenner)
        if anzahl_gleicher_namen(gemarkungen, eintrag["name"]) > 1:
            hinweis = (
                "Der Dienst filtert in {} nach dem Gemarkungsnamen; Gemarkungen "
                "gleichen Namens werden nicht unterschieden.".format(konfig.name)
            )
    else:
        kennzeichen = erstelle_flurstueckskennzeichen(schluessel, flur, zaehler, nenner)
        filter_xml = _filter_kennzeichen(konfig.filter_stil, kennzeichen)

    return Suchanfrage(
        url=erstelle_abruf_url(konfig, filter_xml),
        layername=erstelle_layername(konfig, eintrag["full_name"], flur, zaehler, nenner),
        kennzeichen=kennzeichen,
        hinweis=hinweis,
        bundesland=bundesland,
        gemarkung_name=eintrag["full_name"],
    )


# ---------------------------------------------------------------------------
# Serverantwort
# ---------------------------------------------------------------------------

def ist_zip(inhalt):
    """ZIP-Dateien beginnen mit der Signatur ``PK``."""
    return bytes(inhalt[:2]) == b"PK"


def wfs_fehlertext(inhalt):
    """Liefert die Fehlermeldung, falls die Antwort ein WFS-Exception-Report ist.

    Viele Dienste antworten bei Fehlern mit HTTP 200 und einem XML-Report.
    Gibt ``None`` zurück, wenn kein Fehler erkannt wurde.
    """
    if not inhalt or ist_zip(inhalt):
        return None
    text = bytes(inhalt[:20000]).decode("utf-8", errors="replace")
    if "ExceptionReport" not in text and "ServiceException" not in text:
        return None
    muster = (
        r"<(?:\w+:)?ExceptionText[^>]*>(.*?)</(?:\w+:)?ExceptionText>",
        r"<(?:\w+:)?ServiceException[^>]*>(.*?)</(?:\w+:)?ServiceException>",
    )
    for regex in muster:
        gefunden = re.search(regex, text, re.DOTALL)
        if gefunden:
            meldung = unescape(gefunden.group(1).replace("<![CDATA[", "").replace("]]>", ""))
            meldung = re.sub(r"\s+", " ", meldung).strip()
            if meldung:
                return "Der Server meldet einen Fehler: {}".format(meldung[:300])
    return "Der Server hat einen Fehler gemeldet."


def entpacke_zip_sicher(zip_pfad, ziel):
    """Entpackt ein ZIP und verhindert Pfad-Ausbrüche ("Zip Slip") und ZIP-Bomben."""
    ziel_abs = os.path.realpath(ziel)
    with zipfile.ZipFile(zip_pfad) as archiv:
        gesamt = 0
        for info in archiv.infolist():
            ausgabe = os.path.realpath(os.path.join(ziel_abs, info.filename))
            if ausgabe != ziel_abs and not ausgabe.startswith(ziel_abs + os.sep):
                raise ValueError("Ungültiger Pfad im ZIP-Archiv")
            gesamt += info.file_size
            if gesamt > MAX_ENTPACKT:
                raise ValueError("ZIP-Archiv ist zu groß")
        archiv.extractall(ziel_abs)


def finde_shapefile(verzeichnis):
    """Pfad der ersten .shp-Datei unterhalb von ``verzeichnis`` (oder ``None``)."""
    for wurzel, _ordner, dateien in sorted(os.walk(verzeichnis)):
        for datei in sorted(dateien):
            if datei.lower().endswith(".shp"):
                return os.path.join(wurzel, datei)
    return None
