from qgis.PyQt.QtWidgets import QDialog, QApplication
from qgis.PyQt.uic import loadUiType
import os

FORM_CLASS, _ = loadUiType(os.path.join(
    os.path.dirname(__file__), 'flurstueck_dialog.ui'
))

class FlurstueckDialog(QDialog, FORM_CLASS):
    """Dialog für Flurstückssuche in verschiedenen Bundesländern"""
    
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        
        # UI laden
        self.setupUi(self)
        
        # Signal-Verbindungen
        self.bundesland_combo.currentTextChanged.connect(self.on_bundesland_changed)
        self.suchen_button.clicked.connect(self.on_suchen_clicked)
        self.schliessen_button.clicked.connect(self.close)
        
        # Eingabevalidierung
        self.gemarkung_edit.textChanged.connect(self.validate_fields)
        self.flur_edit.textChanged.connect(self.validate_fields)
        self.zaehler_edit.textChanged.connect(self.validate_fields)
        
    def on_bundesland_changed(self, bundesland):
        """Bundesland-Wechsel verarbeiten"""
        pass
        
    def validate_fields(self):
        """Pflichtfelder validieren"""
        gemarkung = self.gemarkung_edit.text().strip()
        flur = self.flur_edit.text().strip()
        zaehler = self.zaehler_edit.text().strip()
        
        self.suchen_button.setEnabled(bool(gemarkung and flur and zaehler))
            
    def on_suchen_clicked(self):
        """Suchfunktion aufrufen"""
        # Eingaben sammeln
        bundesland = self.bundesland_combo.currentText()
        gemarkung = self.gemarkung_edit.text().strip()
        flur = self.flur_edit.text().strip()
        zaehler = self.zaehler_edit.text().strip()
        nenner = self.nenner_edit.text().strip()
        
        # Pflichtfelder prüfen
        if not all([gemarkung, flur, zaehler]):
            self.status_label.setText("Bitte alle Pflichtfelder ausfüllen!")
            self.status_label.setStyleSheet("color: red; font-style: italic;")
            return
            
        # Numerische Validierung
        try:
            int(flur)
            int(zaehler)
            if nenner:
                int(nenner)
        except ValueError:
            self.status_label.setText("Bitte nur Zahlen für Flur, Zähler und Nenner eingeben!")
            self.status_label.setStyleSheet("color: red; font-style: italic;")
            return
            
        # UI für Suche vorbereiten
        self.setEnabled(False)
        self.status_label.setText("Suche läuft...")
        self.status_label.setStyleSheet("color: #666; font-style: italic;")
        QApplication.processEvents()
        
        # Suche ausführen
        success, message = self.plugin.suche_flurstueck(bundesland, gemarkung, flur, zaehler, nenner)
        
        # UI zurücksetzen
        self.setEnabled(True)
        
        # Ergebnis anzeigen
        if success:
            self.status_label.setText(message)
            self.status_label.setStyleSheet("color: green; font-style: italic;")
        else:
            self.status_label.setText(message)
            self.status_label.setStyleSheet("color: red; font-style: italic;")
