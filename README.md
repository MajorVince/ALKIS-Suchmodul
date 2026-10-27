# ALKIS-Suchmodul

QGIS-Erweiterung zur Suche von Flurstücken über die ALKIS-WFS-Dienste der Länder.
Das Ergebnis wird als Layer in das aktuelle QGIS-Projekt geladen und die Karte
zoomt auf das Flurstück.

| Bundesland         | Kürzel | Dienst                                   |
|--------------------|--------|-------------------------------------------|
| Nordrhein-Westfalen| NRW    | ALKIS vereinfacht (Geobasis NRW)          |
| Niedersachsen      | NI     | ALKIS einfach (LGLN OpenData)             |
| Hessen             | HE     | ALKIS vereinfacht (GDS Hessen)            |
| Rheinland-Pfalz    | RLP    | ALKIS vereinfacht (Geoportal RLP)         |

Voraussetzung: QGIS 3.16 oder neuer und eine Internetverbindung für den Abruf der Flurstücke.

## Installation

**Als ZIP (empfohlen):**

1. ZIP bauen: `python3 build_plugin_zip.py` (erzeugt `alkis_suchmodul.zip`)
2. In QGIS: *Erweiterungen → Erweiterungen verwalten und installieren → Aus ZIP installieren*
3. ZIP auswählen und installieren; Erweiterung „ALKIS-Suchmodul“ aktivieren.

**Manuell:** Den Repo-Inhalt in einen Ordner `alkis_suchmodul` im QGIS-Plugin-Verzeichnis kopieren
(*Einstellungen → Benutzerprofile → Aktuellen Profilordner öffnen → `python/plugins`*) und QGIS neu starten.

## Benutzung

1. Symbol in der Werkzeugleiste oder Menü *Erweiterungen → ALKIS-Suchmodul* öffnen.
2. Bundesland wählen.
3. Gemarkung eingeben und aus der Vorschlagsliste wählen. Akzeptiert werden Name, `Name (Nummer)`,
   die 4-stellige Gemarkungsnummer oder der 6-stellige Schlüssel.
4. Flur, Zähler und (optional) Nenner eingeben, auf **Suchen** klicken.

Gleichnamige Gemarkungen (z. B. „Holthausen“ gibt es in NRW siebenmal) müssen über die
Variante mit Nummer gewählt werden, z. B. `Holthausen (1020)`.

Das Ergebnis ist ein temporärer Speicherlayer (Name z. B. `NRW - Altena - Flur 3 - Flurstück 12/4`).
Soll er dauerhaft erhalten bleiben: Rechtsklick auf den Layer → *Exportieren → Objekte speichern als …*.

## Bekannte Einschränkung

In Rheinland-Pfalz filtert der Dienst nach dem Gemarkungsnamen. Bei gleichnamigen Gemarkungen
kann das Ergebnis deshalb mehrere Gemarkungen umfassen; das Plugin weist in der Statusmeldung darauf hin.

## Entwicklung

Die Fachlogik liegt in `alkis_core.py` (ohne QGIS-Abhängigkeit) und wird mit Unit-Tests geprüft:

```
python3 -m unittest discover -s tests -v
```

## Lizenz

CC0 1.0 (siehe `LICENSE`)
