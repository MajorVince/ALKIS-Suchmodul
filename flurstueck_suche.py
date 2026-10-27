# -*- coding: utf-8 -*-
"""QGIS-Plugin-Klasse des ALKIS-Suchmoduls.

Die Fachlogik (Eingaben prüfen, WFS-Abfrage bauen, Gemarkungen suchen) liegt in
``alkis_core.py``. Dieses Modul kümmert sich nur um QGIS: Menü/Toolbar, den
Netzwerkabruf im Hintergrund und das Anlegen des Ergebnislayers.
"""

import os
import shutil
import tempfile
import zipfile

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import QAction
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsBlockingNetworkRequest,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeatureRequest,
    QgsMessageLog,
    QgsProject,
    QgsTask,
    QgsVectorLayer,
)

from . import alkis_core as core
from .flurstueck_dialog import FlurstueckDialog

MENUE_NAME = "ALKIS-Suchmodul"


def _log(nachricht, stufe=Qgis.Info):
    QgsMessageLog.logMessage(nachricht, core.LOG_TAG, stufe)


class WfsAbrufTask(QgsTask):
    """Lädt die WFS-Antwort im Hintergrund, damit QGIS bedienbar bleibt.

    Es wird ``QgsBlockingNetworkRequest`` verwendet: Das berücksichtigt die in
    QGIS hinterlegten Proxy-, Zertifikats- und Timeout-Einstellungen.
    """

    def __init__(self, beschreibung, url, bei_fertig):
        super().__init__(beschreibung, QgsTask.CanCancel)
        self.url = url
        self.bei_fertig = bei_fertig
        self.inhalt = None
        self.fehler = None

    def run(self):
        try:
            anfrage = QgsBlockingNetworkRequest()
            url = QUrl.fromEncoded(self.url.encode("ascii"))
            fehlercode = anfrage.get(QNetworkRequest(url))
            if fehlercode == QgsBlockingNetworkRequest.NoError:
                self.inhalt = bytes(anfrage.reply().content())
                return True
            if fehlercode == QgsBlockingNetworkRequest.TimeoutError:
                self.fehler = "Timeout: Server antwortet nicht"
            else:
                self.fehler = "Fehler beim Abruf: {}".format(anfrage.errorMessage())
        except Exception as exc:  # noqa: BLE001 - Task darf nie mit Exception enden
            self.fehler = "Unerwarteter Fehler beim Abruf: {}".format(exc)
        return False

    def finished(self, erfolg):
        # Wird vom QGIS-Task-Manager im Hauptthread aufgerufen.
        self.bei_fertig(self.inhalt if erfolg else None, self.fehler)


class FlurstueckSuche:
    """Hauptklasse des Plugins."""

    def __init__(self, iface):
        self.iface = iface
        self.dlg = None
        self.action = None
        self._gemarkungen = {}
        self._tasks = set()

    # ------------------------------------------------------------------
    # Plugin-Lebenszyklus
    # ------------------------------------------------------------------

    def initGui(self):
        icon_pfad = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_pfad) if os.path.exists(icon_pfad) else QIcon()
        self.action = QAction(icon, "ALKIS-Suchmodul", self.iface.mainWindow())
        self.action.setToolTip("Flurstück über ALKIS-WFS-Dienste suchen")
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(MENUE_NAME, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        if self.dlg is not None:
            self.dlg.close()
            self.dlg.deleteLater()
            self.dlg = None
        if self.action is not None:
            self.iface.removePluginMenu(MENUE_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def run(self):
        """Öffnet den Suchdialog (oder holt den vorhandenen nach vorn)."""
        if self.dlg is None:
            self.dlg = FlurstueckDialog(self, self.iface.mainWindow())
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()

    # ------------------------------------------------------------------
    # Daten
    # ------------------------------------------------------------------

    def get_gemarkungen_for_bundesland(self, bundesland):
        """Gemarkungsliste des Bundeslandes (wird einmalig geladen)."""
        if bundesland not in self._gemarkungen:
            try:
                self._gemarkungen[bundesland] = core.lade_gemarkungen(bundesland)
                _log("Gemarkungen geladen: {} ({} Einträge)".format(
                    bundesland, len(self._gemarkungen[bundesland])))
            except (OSError, ValueError, core.SucheFehler) as exc:
                _log("Fehler beim Laden der Gemarkungen für {}: {}".format(bundesland, exc),
                     Qgis.Critical)
                self._gemarkungen[bundesland] = {}
        return self._gemarkungen[bundesland]

    # ------------------------------------------------------------------
    # Suche
    # ------------------------------------------------------------------

    def suche_flurstueck(self, bundesland, gemarkung_text, flur_text, zaehler_text,
                         nenner_text, bei_fertig):
        """Startet die Suche. ``bei_fertig(erfolg, nachricht)`` wird später im
        Hauptthread aufgerufen (auch bei Eingabefehlern)."""
        try:
            anfrage = core.bereite_suche_vor(
                bundesland, gemarkung_text, flur_text, zaehler_text, nenner_text,
                self.get_gemarkungen_for_bundesland(bundesland),
            )
        except core.SucheFehler as exc:
            self._melde(bei_fertig, False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            _log("Fehler bei der Eingabeverarbeitung: {}".format(exc), Qgis.Critical)
            self._melde(bei_fertig, False, "Fehler bei der Eingabeverarbeitung: {}".format(exc))
            return

        _log("Suche Flurstück: {}".format(anfrage.kennzeichen))
        _log("WFS-URL: {}".format(anfrage.url))

        def abgeschlossen(inhalt, fehler):
            self._tasks.discard(task)
            if inhalt is None:
                self._melde(bei_fertig, False, fehler or "Suche abgebrochen")
                return
            erfolg, nachricht = self._verarbeite_antwort(inhalt, anfrage)
            self._melde(bei_fertig, erfolg, nachricht)

        task = WfsAbrufTask("ALKIS-Suchmodul: {}".format(anfrage.layername), anfrage.url, abgeschlossen)
        self._tasks.add(task)
        QgsApplication.taskManager().addTask(task)

    @staticmethod
    def _melde(bei_fertig, erfolg, nachricht):
        try:
            bei_fertig(erfolg, nachricht)
        except RuntimeError:
            # Dialog wurde inzwischen zerstört - nichts mehr zu melden.
            pass

    # ------------------------------------------------------------------
    # Antwort verarbeiten
    # ------------------------------------------------------------------

    def _verarbeite_antwort(self, inhalt, anfrage):
        """Erzeugt aus der Serverantwort (Shapefile-ZIP oder GML) einen Layer."""
        if not inhalt:
            return False, "Leere Antwort vom Server"

        fehlertext = core.wfs_fehlertext(inhalt)
        if fehlertext:
            _log(fehlertext, Qgis.Warning)
            return False, fehlertext

        arbeitsordner = tempfile.mkdtemp(prefix="alkis_suchmodul_")
        quelle = None
        try:
            if core.ist_zip(inhalt):
                zip_pfad = os.path.join(arbeitsordner, "antwort.zip")
                with open(zip_pfad, "wb") as datei:
                    datei.write(inhalt)
                entpackt = os.path.join(arbeitsordner, "daten")
                os.makedirs(entpackt)
                core.entpacke_zip_sicher(zip_pfad, entpackt)
                daten_pfad = core.finde_shapefile(entpackt)
                if not daten_pfad:
                    return False, "Kein Shapefile in der Antwort gefunden!"
            else:
                daten_pfad = os.path.join(arbeitsordner, "antwort.gml")
                with open(daten_pfad, "wb") as datei:
                    datei.write(inhalt)

            quelle = QgsVectorLayer(daten_pfad, "temp_source", "ogr")
            if not quelle.isValid():
                return False, "Antwort des Servers konnte nicht gelesen werden!"
            if not quelle.crs().isValid():
                quelle.setCrs(QgsCoordinateReferenceSystem(core.STANDARD_CRS))
            if quelle.featureCount() == 0:
                return False, "Flurstück nicht gefunden (keine Treffer beim Dienst)."

            # materialize() übernimmt Geometrietyp (auch Multipart), Felder und KBS
            # in einen Speicherlayer, der unabhängig von den Temp-Dateien ist.
            layer = quelle.materialize(QgsFeatureRequest())
            if layer is None or not layer.isValid():
                return False, "Ergebnislayer konnte nicht erstellt werden!"
            layer.setName(anfrage.layername)
            anzahl = layer.featureCount()
        except zipfile.BadZipFile:
            return False, "Ungültige ZIP-Datei empfangen"
        except ValueError as exc:  # unsicherer oder zu großer ZIP-Inhalt
            return False, "Ungültige Antwort des Servers: {}".format(exc)
        except Exception as exc:  # noqa: BLE001
            _log("Verarbeitungsfehler: {}".format(exc), Qgis.Critical)
            return False, "Fehler bei der Verarbeitung: {}".format(exc)
        finally:
            quelle = None  # Dateihandle freigeben, bevor aufgeräumt wird
            shutil.rmtree(arbeitsordner, ignore_errors=True)

        QgsProject.instance().addMapLayer(layer)
        self.iface.setActiveLayer(layer)
        self._zoome_auf_layer(layer)
        _log("Layer erstellt: {} ({} Objekt(e))".format(layer.name(), anzahl))

        nachricht = "Flurstück erfolgreich geladen: {}".format(layer.name())
        if anzahl > 1:
            nachricht += " ({} Objekte)".format(anzahl)
        if anfrage.hinweis:
            nachricht += "\nHinweis: {}".format(anfrage.hinweis)
        return True, nachricht

    def _zoome_auf_layer(self, layer):
        """Zoomt auf den Layer - mit Transformation ins Projekt-KBS."""
        canvas = self.iface.mapCanvas()
        extent = layer.extent()
        if extent.isNull() or not extent.isFinite():
            return
        ziel_crs = canvas.mapSettings().destinationCrs()
        if layer.crs().isValid() and ziel_crs.isValid() and layer.crs() != ziel_crs:
            try:
                trafo = QgsCoordinateTransform(layer.crs(), ziel_crs, QgsProject.instance())
                extent = trafo.transformBoundingBox(extent)
            except QgsCsException as exc:
                _log("Koordinatentransformation fehlgeschlagen: {}".format(exc), Qgis.Warning)
                return
        extent.scale(1.5)
        canvas.setExtent(extent)
        canvas.refresh()
