from qgis.PyQt.QtCore import Qt, QSettings, QTranslator, QCoreApplication
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import QAction, QApplication, QMessageBox
from qgis.core import QgsProject, QgsVectorLayer, QgsMessageLog, QgsApplication, Qgis
from .flurstueck_dialog import FlurstueckDialog
import requests
import urllib.parse
import os
import tempfile
import zipfile
import json

class FlurstueckSuche:
    """Hauptklasse für das Flurstück-Suche Plugin"""
    
    def __init__(self, iface):
        self.iface = iface
        self.dlg = None
        
        self.gemarkungen_nrw = self.load_gemarkungen_json("gemarkungen_nrw.json")
        self.gemarkungen_nieder = self.load_gemarkungen_json("gemarkungen_nieder.json")
        self.gemarkungen_hessen = self.load_gemarkungen_json("gemarkungen_hessen.json")
        self.gemarkungen_rlp = self.load_gemarkungen_json("gemarkungen_rlp.json")
        
        self.fluren = {}
        self.wfs_urls = {
            "Nordrhein-Westfalen": "https://www.wfs.nrw.de/geobasis/wfs_nw_alkis_vereinfacht",
            "Niedersachsen": "https://opendata.lgln.niedersachsen.de/doorman/noauth/alkis_wfs_einfach",
            "Hessen": "https://www.gds.hessen.de/wfs2/aaa-suite/cgi-bin/alkis/vereinf/wfs",
            "Rheinland-Pfalz": "https://www.geoportal.rlp.de/registry/wfs/519"
        }

    def load_gemarkungen_json(self, filename):
        """Lädt Gemarkungen aus JSON-Datei"""
        try:
            json_path = os.path.join(os.path.dirname(__file__), filename)

            if not os.path.exists(json_path):
                QgsMessageLog.logMessage(
                    f"Warnung: {filename} nicht gefunden",
                    "Flurstück-Suche",
                    Qgis.Warning
                )
                return {}

            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            QgsMessageLog.logMessage(
                f"Gemarkungen geladen: {filename} ({len(data)} Einträge)",
                "Flurstück-Suche"
            )
            return data

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Fehler beim Laden von {filename}: {str(e)}",
                "Flurstück-Suche",
                Qgis.Critical
            )
            return {}
        
    def initGui(self):
        """Initialisiert die GUI"""
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
            
        self.action = QAction(
            icon,
            "ALKIS-Suchmodul",
            self.iface.mainWindow()
        )
        self.action.triggered.connect(self.run)
        
        self.iface.addPluginToMenu("ALKIS-Suchmodul", self.action)
        self.iface.addToolBarIcon(self.action)
        
    def unload(self):
        """Entfernt Plugin aus QGIS"""
        self.iface.removePluginMenu("ALKIS-Suchmodul", self.action)
        self.iface.removeToolBarIcon(self.action)
        
    def run(self):
        """Öffnet Suchdialog"""
        if self.dlg:
            self.dlg.close()
            self.dlg = None
    
        self.dlg = FlurstueckDialog(self, self.iface.mainWindow())
        self.dlg.show()
        self.dlg.activateWindow()
        
    def get_gemarkungen_for_bundesland(self, bundesland):
        """Gibt Gemarkungen für Bundesland zurück"""
        gemarkungen_map = {
            "Nordrhein-Westfalen": self.gemarkungen_nrw,
            "Niedersachsen": self.gemarkungen_nieder,
            "Hessen": self.gemarkungen_hessen,
            "Rheinland-Pfalz": self.gemarkungen_rlp
        }
        return gemarkungen_map.get(bundesland, {})
        
    def suche_flurstueck(self, bundesland, gemarkung_name, flur_text, zaehler_text, nenner_text):
        """Hauptsuchfunktion"""
        
        if not bundesland or not gemarkung_name or not flur_text or not zaehler_text:
            return False, "Bitte alle Pflichtfelder ausfüllen!"
            
        try:
            gemarkungen_data = self.get_gemarkungen_for_bundesland(bundesland)
            if not gemarkungen_data:
                return False, f"Keine Gemarkungsdaten für {bundesland} gefunden!"
            
            gem_schluessel, gem_full_name = self.find_gemarkung_by_name(gemarkung_name, gemarkungen_data)
            if not gem_schluessel:
                return False, f"Gemarkung '{gemarkung_name}' nicht gefunden!"
            
            if not self.validate_gemarkungsschluessel(gem_schluessel, bundesland):
                return False, "Ungültiger Gemarkungsschlüssel!"

            flur_num = int(flur_text)
            zaehler_num = int(zaehler_text)
            
            flur = flur_text.zfill(3)
            zaehler = zaehler_text.zfill(5)
        
            if bundesland == "Rheinland-Pfalz":
                success, message = self.suche_rheinland_pfalz(gem_schluessel, gem_full_name, gemarkungen_data, 
                                                              flur_text, zaehler_text, nenner_text, 
                                                              gemarkung_name, bundesland)
                return success, message
            
            flstkennz = self.erstelle_flurstueckskennzeichen(bundesland, gem_schluessel, flur, zaehler, nenner_text)
            QgsMessageLog.logMessage(f"Suche Flurstück: {flstkennz}", "Flurstück-Suche")

        except ValueError:
            return False, "Bitte nur Zahlen für Flur, Zähler und Nenner eingeben!"
        except Exception as e:
            return False, f"Fehler bei der Eingabeverarbeitung: {str(e)}"
        
        try:
            wfs_url = self.wfs_urls.get(bundesland)
            if not wfs_url:
                return False, f"Keine WFS-URL für {bundesland} konfiguriert!"
            
            url = self.erstelle_wfs_request_standard(flstkennz, wfs_url, bundesland)
            QgsMessageLog.logMessage(f"WFS-URL: {url}", "Flurstück-Suche")
            
            response = requests.get(url, timeout=30)
            
            if response.status_code != 200:
                return False, f"Fehler beim Abruf: HTTP {response.status_code}"
                
            return self.verarbeite_wfs_antwort(response, bundesland, gem_full_name, flur_text, zaehler_text, nenner_text)
            
        except requests.exceptions.Timeout:
            return False, "Timeout: Server antwortet nicht"
        except requests.exceptions.RequestException as e:
            return False, f"Netzwerkfehler: {str(e)}"
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler: {str(e)}", "Flurstück-Suche", Qgis.Critical)
            return False, f"Unerwarteter Fehler: {str(e)}"
    
    def suche_rheinland_pfalz(self, gem_schluessel, gem_full_name, gemarkungen_data, 
                              flur_text, zaehler_text, nenner_text, gemarkung_name, bundesland):
        """Spezielle Suchfunktion für Rheinland-Pfalz mit kombinierter Filterung"""
        try:
            wfs_url = self.wfs_urls.get("Rheinland-Pfalz")
            if not wfs_url:
                return False, "Keine WFS-URL für Rheinland-Pfalz konfiguriert!"
            
            gemarkung_value = gemarkungen_data[gem_schluessel]["name"]
            flur_value = f"Flur {flur_text}"
            
            url = self.erstelle_wfs_request_rlp(gemarkung_value, flur_value, zaehler_text, nenner_text, wfs_url)
            QgsMessageLog.logMessage(f"RLP WFS-URL: {url}", "Flurstück-Suche")
            
            response = requests.get(url, timeout=30)
            
            if response.status_code != 200:
                return False, f"Fehler beim Abruf: HTTP {response.status_code}"
            
            return self.verarbeite_wfs_antwort(response, bundesland, gem_full_name, flur_text, zaehler_text, nenner_text)
            
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler RLP-Suche: {str(e)}", "Flurstück-Suche", Qgis.Critical)
            return False, f"Fehler bei RLP-Suche: {str(e)}"
    
    def erstelle_wfs_request_rlp(self, gemarkung, flur, zaehler, nenner, wfs_url):
        """Erstellt WFS-Request für Rheinland-Pfalz mit kombinierter Filterung"""
        url = f"{wfs_url}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
        url += "&TYPENAMES=ave:Flurstueck"
        url += "&SRSNAME=urn:ogc:def:crs:EPSG::25832"
        url += "&OUTPUTFORMAT=application/x-zip-shapefile"
        
        filter_xml = f'''<Filter xmlns="http://www.opengis.net/fes/2.0">
  <And>
    <PropertyIsEqualTo>
      <ValueReference>gemarkung</ValueReference>
      <Literal>{gemarkung}</Literal>
    </PropertyIsEqualTo>
    <PropertyIsEqualTo>
      <ValueReference>flur</ValueReference>
      <Literal>{flur}</Literal>
    </PropertyIsEqualTo>
    <PropertyIsEqualTo>
      <ValueReference>flstnrzae</ValueReference>
      <Literal>{zaehler}</Literal>
    </PropertyIsEqualTo>'''
    
        if nenner and nenner.strip():
            filter_xml += f'''
    <PropertyIsEqualTo>
      <ValueReference>flstnrnen</ValueReference>
      <Literal>{nenner}</Literal>
    </PropertyIsEqualTo>'''
        
        filter_xml += '''
  </And>
</Filter>'''
        
        filter_encoded = urllib.parse.quote(filter_xml)
        url += f"&FILTER={filter_encoded}"
        
        return url
    
    def erstelle_wfs_request_standard(self, flstkennz, wfs_url, bundesland):
        """Erstellt WFS-Request für NRW, Niedersachsen und Hessen"""
        
        config = {
            "Nordrhein-Westfalen": {
                "version": "1.1.0",
                "typename": "TYPENAME",
                "filter_ns": "ogc_with_ave",
                "output_format": "application/x-zip-shapefile"
            },
            "Niedersachsen": {
                "version": "1.1.0",
                "typename": "typename",
                "filter_ns": "ogc_with_ave",
                "output_format": "application/x-zip-shapefile"
            },
            "Hessen": {
                "version": "2.0.0", 
                "typename": "TYPENAMES",
                "filter_ns": "fes",
                "output_format": "application/x-zip-shapefile"
            }
        }
        
        cfg = config.get(bundesland)
        if not cfg:
            return None
        
        url = f"{wfs_url}?SERVICE=WFS&VERSION={cfg['version']}"
        url += f"&REQUEST=GetFeature&{cfg['typename']}=ave:Flurstueck"
        url += f"&OUTPUTFORMAT={cfg['output_format']}"
        
        if cfg['filter_ns'] == "fes":
            filter_xml = f'''<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" 
                     xmlns:ave="http://repository.gdi-de.org/schemas/adv/produkt/alkis-vereinfacht/2.0">
                      <fes:PropertyIsEqualTo>
                        <fes:ValueReference>ave:flstkennz</fes:ValueReference>
                        <fes:Literal>{flstkennz}</fes:Literal>
                      </fes:PropertyIsEqualTo>
                    </fes:Filter>'''
        else:
            filter_xml = f'''<Filter xmlns="http://www.opengis.net/ogc" 
                     xmlns:ave="http://repository.gdi-de.org/schemas/adv/produkt/alkis-vereinfacht/2.0">
                      <PropertyIsEqualTo>
                        <PropertyName>ave:flstkennz</PropertyName>
                        <Literal>{flstkennz}</Literal>
                      </PropertyIsEqualTo>
                    </Filter>'''
        
        filter_encoded = urllib.parse.quote(filter_xml)
        url += f"&FILTER={filter_encoded}"
        
        return url
    
    def erstelle_flurstueckskennzeichen(self, bundesland, gem_schluessel, flur, zaehler, nenner_text):
        """Erstellt flstkennz je nach Bundesland-Format"""
        
        zaehler_formatted = zaehler.zfill(5)
        
        # Alle Bundesländer außer RLP verwenden das gleiche Format
        if nenner_text and nenner_text.strip():
            nenner_formatted = nenner_text.zfill(5)
            return f"{gem_schluessel}{flur}{zaehler_formatted}/{nenner_formatted}______"
        else:
            return f"{gem_schluessel}{flur}{zaehler_formatted}______"
    
    def verarbeite_wfs_antwort(self, response, bundesland, gem_full_name, flur_text, zaehler_text, nenner_text):
        """Verarbeitet die WFS-Antwort (ZIP oder XML)"""
        
        content_type = response.headers.get('Content-Type', '').lower()
        
        # Alle Bundesländer verwenden jetzt Shapefiles
        # XML-Verarbeitung nur als Fallback bei Problemen
        if 'xml' in content_type and 'shapefile' not in content_type:
            return self.verarbeite_xml_antwort(response, bundesland, gem_full_name, flur_text, zaehler_text, nenner_text)
        else:
            return self.verarbeite_shapefile_antwort(response, bundesland, gem_full_name, flur_text, zaehler_text, nenner_text)
    
    def verarbeite_shapefile_antwort(self, response, bundesland, gem_full_name, flur_text, zaehler_text, nenner_text):
        """Verarbeitet Shapefile-Antwort"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_zip:
                tmp_zip.write(response.content)
                zip_path = tmp_zip.name
                
            with tempfile.TemporaryDirectory() as tmp_dir:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(tmp_dir)
                    
                shape_files = [f for f in os.listdir(tmp_dir) if f.endswith('.shp')]
                if not shape_files:
                    for root, dirs, files in os.walk(tmp_dir):
                        shape_files = [f for f in files if f.endswith('.shp')]
                        if shape_files:
                            tmp_dir = root
                            break
                            
                if not shape_files:
                    os.unlink(zip_path)
                    return False, "Kein Shapefile gefunden!"
                    
                shape_path = os.path.join(tmp_dir, shape_files[0])
                ortsteil = gem_full_name.split('(')[0].strip()

                bundesland_kuerzel = {
                    "Nordrhein-Westfalen": "NRW",
                    "Niedersachsen": "NI", 
                    "Hessen": "HE",
                    "Rheinland-Pfalz": "RLP"
                }.get(bundesland, "")
                
                memory_layer_name = f"{bundesland_kuerzel} - {ortsteil} - Flur {flur_text} - Flurstück {zaehler_text}"
                if nenner_text:
                    memory_layer_name += f"/{nenner_text}"
                    
                source_memory_layer = QgsVectorLayer(shape_path, "temp_source", "ogr")

                if not source_memory_layer.isValid():
                    if zip_path and os.path.exists(zip_path):
                        os.unlink(zip_path)
                    return False, "Shapefile konnte nicht geladen werden!"

                if source_memory_layer.featureCount() == 0:
                    if zip_path and os.path.exists(zip_path):
                        os.unlink(zip_path)
                    return False, "Keine Geometrien im Shapefile!"

                memory_layer = QgsVectorLayer(
                    f"Polygon?crs={source_memory_layer.crs().authid()}",
                    memory_layer_name,
                    "memory"
                )

                provider = memory_layer.dataProvider()
                provider.addAttributes(source_memory_layer.fields())
                memory_layer.updateFields()

                features = list(source_memory_layer.getFeatures())
                provider.addFeatures(features)
                memory_layer.updateExtents()

                QgsMessageLog.logMessage(
                    f"Memory-Layer erstellt: {memory_layer.name()}",
                    "Flurstück-Suche"
                )

                QgsProject.instance().addMapLayer(memory_layer)                    
                
                canvas = self.iface.mapCanvas()
                extent = memory_layer.extent()

                if extent.isNull() or not extent.isFinite():
                    center = memory_layer.extent().center()
                    canvas.setCenter(center)
                    canvas.zoomScale(1000)
                else:
                    extent_buffered = extent.buffered(50)
                    canvas.setExtent(extent_buffered)

                canvas.refresh()
                
            os.unlink(zip_path)
            
            return True, f"Flurstück erfolgreich geladen: {memory_layer_name}"
            
        except zipfile.BadZipFile:
            return False, "Ungültige ZIP-Datei empfangen"
        except Exception as e:
            QgsMessageLog.logMessage(f"Shapefile-Verarbeitungsfehler: {str(e)}", "Flurstück-Suche", Qgis.Critical)
            return False, f"Fehler bei Shapefile-Verarbeitung: {str(e)}"
    
    def verarbeite_xml_antwort(self, response, bundesland, gem_full_name, flur_text, zaehler_text, nenner_text):
        """Verarbeitet XML-Antwort (Fallback)"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.xml', delete=False, mode='w', encoding='utf-8') as tmp_xml:
                tmp_xml.write(response.text)
                xml_path = tmp_xml.name
            
            ortsteil = gem_full_name.split('(')[0].strip()
            
            bundesland_kuerzel = {
                "Nordrhein-Westfalen": "NRW",
                "Niedersachsen": "NI", 
                "Hessen": "HE",
                "Rheinland-Pfalz": "RLP"
            }.get(bundesland, "")
            
            memory_layer_name = f"{bundesland_kuerzel} - {ortsteil} - Flur {flur_text} - Flurstück {zaehler_text}"
            if nenner_text:
                memory_layer_name += f"/{nenner_text}"
            
            source_layer = QgsVectorLayer(xml_path, "temp_xml_source", "ogr")
            
            if not source_layer.isValid():
                os.unlink(xml_path)
                return False, "XML konnte nicht geladen werden!"
            
            if source_layer.featureCount() == 0:
                os.unlink(xml_path)
                return False, "Keine Geometrien in XML gefunden!"
            
            memory_layer = QgsVectorLayer(
                f"Polygon?crs={source_layer.crs().authid()}",
                memory_layer_name,
                "memory"
            )
            
            provider = memory_layer.dataProvider()
            provider.addAttributes(source_layer.fields())
            memory_layer.updateFields()
            
            features = list(source_layer.getFeatures())
            provider.addFeatures(features)
            memory_layer.updateExtents()
            
            QgsMessageLog.logMessage(
                f"Memory-Layer aus XML erstellt: {memory_layer.name()}",
                "Flurstück-Suche"
            )
            
            QgsProject.instance().addMapLayer(memory_layer)
            
            canvas = self.iface.mapCanvas()
            extent = memory_layer.extent()
            
            if extent.isNull() or not extent.isFinite():
                center = memory_layer.extent().center()
                canvas.setCenter(center)
                canvas.zoomScale(1000)
            else:
                extent_buffered = extent.buffered(50)
                canvas.setExtent(extent_buffered)
            
            canvas.refresh()
            
            os.unlink(xml_path)
            
            return True, f"Flurstück erfolgreich geladen: {memory_layer_name}"
            
        except Exception as e:
            QgsMessageLog.logMessage(f"XML-Verarbeitungsfehler: {str(e)}", "Flurstück-Suche", Qgis.Critical)
            return False, f"Fehler bei XML-Verarbeitung: {str(e)}"
        
    def find_gemarkung_by_name(self, gemarkung_input, gemarkungen_data):
        """Findet passende Gemarkung"""
        if not gemarkung_input or not gemarkungen_data:
            return None, None
        
        input_lower = gemarkung_input.lower().strip()
        
        if input_lower.isdigit() and len(input_lower) == 4:
            for schluessel, data in gemarkungen_data.items():
                if data["nummer"] == input_lower:
                    return schluessel, data["full_name"]
            
        for schluessel, data in gemarkungen_data.items():
            if input_lower == data["name"].lower():
                return schluessel, data["full_name"]

        return None, None
    
    def validate_gemarkungsschluessel(self, schluessel, bundesland):
        """Validiert Gemarkungsschlüssel"""
        validators = {
            "Nordrhein-Westfalen": lambda s: len(s) == 6 and s.startswith('05'),
            "Niedersachsen": lambda s: len(s) == 6 and s.startswith('03'),
            "Hessen": lambda s: len(s) == 6 and s.startswith('06'),
            "Rheinland-Pfalz": lambda s: len(s) == 6 and s.startswith('07')
        }
        validator = validators.get(bundesland)
        return validator(schluessel) if validator else False
