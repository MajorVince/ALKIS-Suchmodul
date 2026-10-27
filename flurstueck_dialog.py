# -*- coding: utf-8 -*-
"""Suchdialog des ALKIS-Suchmoduls."""

import os

from qgis.PyQt.QtCore import Qt, QRegularExpression, QStringListModel
from qgis.PyQt.QtGui import QRegularExpressionValidator
from qgis.PyQt.QtWidgets import QCompleter, QDialog
from qgis.PyQt.uic import loadUiType
from qgis.core import QgsSettings

from . import alkis_core as core

FORM_CLASS, _ = loadUiType(os.path.join(os.path.dirname(__file__), "flurstueck_dialog.ui"))

EINSTELLUNG_BUNDESLAND = "ALKIS-Suchmodul/bundesland"

STIL_INFO = "color: #666; font-style: italic;"
STIL_OK = "color: green; font-style: italic;"
STIL_FEHLER = "color: red; font-style: italic;"


class FlurstueckDialog(QDialog, FORM_CLASS):
    """Dialog für die Flurstückssuche in mehreren Bundesländern."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setupUi(self)

        self.status_label.setWordWrap(True)
        self.suchen_button.setDefault(True)
        self.gemarkung_edit.setToolTip(
            "Gemarkungsname eingeben und aus der Liste wählen. Bei gleichnamigen "
            "Gemarkungen die Variante mit Nummer wählen, z. B. \"Holthausen (1020)\"."
        )
        self.flur_edit.setToolTip("Flurnummer, z. B. 3")
        self.zaehler_edit.setToolTip("Flurstückszähler, z. B. 12")
        self.nenner_edit.setToolTip("Flurstücksnenner (nur bei Flurstücken wie 12/4)")

        # Autovervollständigung der Gemarkungen
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setMaxVisibleItems(12)
        self.gemarkung_edit.setCompleter(self._completer)

        # Nur Ziffern in den Zahlenfeldern
        for feld, stellen in (
            (self.flur_edit, core.MAX_STELLEN_FLUR),
            (self.zaehler_edit, core.MAX_STELLEN_ZAEHLER),
            (self.nenner_edit, core.MAX_STELLEN_NENNER),
        ):
            regel = QRegularExpression("[0-9]{{0,{}}}".format(stellen))
            feld.setValidator(QRegularExpressionValidator(regel, feld))

        # zuletzt verwendetes Bundesland wiederherstellen
        letztes = QgsSettings().value(EINSTELLUNG_BUNDESLAND, "")
        index = self.bundesland_combo.findText(str(letztes))
        if index >= 0:
            self.bundesland_combo.setCurrentIndex(index)
        self._aktualisiere_completer(self.bundesland_combo.currentText())

        # Signale
        self.bundesland_combo.currentTextChanged.connect(self.on_bundesland_changed)
        self.suchen_button.clicked.connect(self.on_suchen_clicked)
        self.schliessen_button.clicked.connect(self.close)
        for feld in (self.gemarkung_edit, self.flur_edit, self.zaehler_edit):
            feld.textChanged.connect(self.validate_fields)

        self.validate_fields()

    # ------------------------------------------------------------------

    def _aktualisiere_completer(self, bundesland):
        gemarkungen = self.plugin.get_gemarkungen_for_bundesland(bundesland)
        namen = core.gemarkungsnamen_fuer_completer(gemarkungen)
        self._completer.setModel(QStringListModel(namen, self._completer))

    def _setze_status(self, text, stil):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(stil)

    def _eingaben_vollstaendig(self):
        return bool(
            self.gemarkung_edit.text().strip()
            and self.flur_edit.text().strip()
            and self.zaehler_edit.text().strip()
        )

    # ------------------------------------------------------------------

    def on_bundesland_changed(self, bundesland):
        """Bundesland-Wechsel: Vorschlagsliste tauschen, Gemarkung zurücksetzen."""
        QgsSettings().setValue(EINSTELLUNG_BUNDESLAND, bundesland)
        self._aktualisiere_completer(bundesland)
        self.gemarkung_edit.clear()
        self._setze_status("", STIL_INFO)

    def validate_fields(self):
        """Suchen-Button nur bei ausgefüllten Pflichtfeldern aktivieren."""
        self.suchen_button.setEnabled(self._eingaben_vollstaendig())

    def _setze_laufend(self, laufend):
        self.groupBox.setEnabled(not laufend)
        self.suchen_button.setEnabled(not laufend and self._eingaben_vollstaendig())

    def on_suchen_clicked(self):
        """Suche starten; das Ergebnis kommt asynchron über ``_suche_beendet``."""
        if not self._eingaben_vollstaendig():
            self._setze_status("Bitte alle Pflichtfelder ausfüllen!", STIL_FEHLER)
            return

        self._setze_laufend(True)
        self._setze_status("Suche läuft …", STIL_INFO)
        try:
            self.plugin.suche_flurstueck(
                self.bundesland_combo.currentText(),
                self.gemarkung_edit.text().strip(),
                self.flur_edit.text().strip(),
                self.zaehler_edit.text().strip(),
                self.nenner_edit.text().strip(),
                self._suche_beendet,
            )
        except Exception as exc:  # noqa: BLE001 - Dialog darf nicht hängen bleiben
            self._suche_beendet(False, "Unerwarteter Fehler: {}".format(exc))

    def _suche_beendet(self, erfolg, nachricht):
        self._setze_laufend(False)
        self._setze_status(nachricht, STIL_OK if erfolg else STIL_FEHLER)
