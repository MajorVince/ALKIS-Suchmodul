# -*- coding: utf-8 -*-
"""Tests für alkis_core (laufen ohne QGIS): python3 -m unittest discover -s tests -v"""

import os
import re
import sys
import tempfile
import unittest
import urllib.parse
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alkis_core as core  # noqa: E402

DATEN = {name: core.lade_gemarkungen(name) for name in core.BUNDESLAENDER}


class TestGemarkungsdaten(unittest.TestCase):
    def test_struktur_aller_dateien(self):
        for land, daten in DATEN.items():
            konfig = core.BUNDESLAENDER[land]
            self.assertGreater(len(daten), 1000, land)
            for schluessel, eintrag in daten.items():
                self.assertTrue(core.gueltiger_gemarkungsschluessel(schluessel, land), (land, schluessel))
                self.assertEqual(eintrag["nummer"], schluessel[2:], (land, schluessel))
                self.assertTrue(eintrag["name"].strip(), (land, schluessel))
                self.assertEqual(eintrag["full_name"], "{} ({})".format(eintrag["name"], eintrag["nummer"]))
                self.assertTrue(schluessel.startswith(konfig.praefix))

    def test_vollstaendige_namen_sind_eindeutig(self):
        for land, daten in DATEN.items():
            namen = [e["full_name"] for e in daten.values()]
            self.assertEqual(len(namen), len(set(namen)), land)


class TestEingaben(unittest.TestCase):
    def test_parse_zahl(self):
        self.assertEqual(core.parse_zahl("012", "Flur", 3), 12)
        self.assertEqual(core.parse_zahl(" 7 ", "Flur", 3), 7)
        self.assertEqual(core.parse_zahl("0", "Flur", 3), 0)

    def test_parse_zahl_fehler(self):
        for text in ("", "  ", "abc", "1a", "-1", "1.5", "²", None):
            with self.assertRaises(core.SucheFehler, msg=repr(text)):
                core.parse_zahl(text, "Flur", 3)
        with self.assertRaises(core.SucheFehler):
            core.parse_zahl("1000", "Flur", 3)

    def test_flurstueckskennzeichen(self):
        self.assertEqual(core.erstelle_flurstueckskennzeichen("051004", 3, 12), "051004003" "00012" "______")
        self.assertEqual(core.erstelle_flurstueckskennzeichen("051004", "3", "12", "4"),
                         "051004003" "00012" "/00004" "______")
        self.assertEqual(core.erstelle_flurstueckskennzeichen("051004", 3, 12, 0),
                         "051004003" "00012" "/00000" "______")


class TestGemarkungssuche(unittest.TestCase):
    def setUp(self):
        self.nrw = DATEN["Nordrhein-Westfalen"]

    def test_eindeutiger_name(self):
        treffer = core.finde_gemarkungen("altena", self.nrw)
        self.assertEqual([s for s, _ in treffer], ["051004"])

    def test_mehrdeutiger_name(self):
        treffer = core.finde_gemarkungen("Holthausen", self.nrw)
        self.assertGreater(len(treffer), 1)

    def test_anzeigename_nummer_und_schluessel(self):
        self.assertEqual([s for s, _ in core.finde_gemarkungen("Holthausen (1020)", self.nrw)], ["051020"])
        self.assertEqual([s for s, _ in core.finde_gemarkungen("holthausen(1020)", self.nrw)], ["051020"])
        self.assertEqual([s for s, _ in core.finde_gemarkungen("1020", self.nrw)], ["051020"])
        self.assertEqual([s for s, _ in core.finde_gemarkungen("051020", self.nrw)], ["051020"])

    def test_falsche_kombination(self):
        self.assertEqual(core.finde_gemarkungen("Altena (1020)", self.nrw), [])
        self.assertEqual(core.finde_gemarkungen("999999", self.nrw), [])
        self.assertEqual(core.finde_gemarkungen("", self.nrw), [])
        self.assertEqual(core.finde_gemarkungen("Altena", {}), [])

    def test_vorschlaege(self):
        vorschlaege = core.aehnliche_gemarkungen("Altenaa", self.nrw)
        self.assertIn("Altena (1004)", vorschlaege)

    def test_completer_liste(self):
        namen = core.gemarkungsnamen_fuer_completer(self.nrw)
        self.assertEqual(len(namen), len(self.nrw))
        self.assertIn("Altena (1004)", namen)


class TestSuchanfrage(unittest.TestCase):
    def anfrage(self, land, gemarkung, flur="3", zaehler="12", nenner=""):
        return core.bereite_suche_vor(land, gemarkung, flur, zaehler, nenner, DATEN[land])

    @staticmethod
    def filter_aus_url(url):
        return urllib.parse.unquote(url.split("&FILTER=", 1)[1])

    def test_nrw(self):
        a = self.anfrage("Nordrhein-Westfalen", "Altena (1004)")
        self.assertTrue(a.url.startswith("https://www.wfs.nrw.de/geobasis/wfs_nw_alkis_vereinfacht?SERVICE=WFS&VERSION=1.1.0"))
        self.assertIn("&REQUEST=GetFeature&TYPENAME=ave:Flurstueck", a.url)
        self.assertIn("&OUTPUTFORMAT=application/x-zip-shapefile", a.url)
        self.assertIn("<PropertyName>ave:flstkennz</PropertyName>", self.filter_aus_url(a.url))
        self.assertIn("<Literal>051004003" "00012" "______</Literal>", self.filter_aus_url(a.url))
        self.assertEqual(a.layername, "NRW - Altena - Flur 3 - Flurstück 12")
        self.assertIsNone(a.hinweis)

    def test_niedersachsen_kleingeschriebener_parameter(self):
        a = self.anfrage("Niedersachsen", "Memmert", "1", "5", "2")
        self.assertIn("&typename=ave:Flurstueck", a.url)
        self.assertIn("<Literal>030101001" "00005" "/00002" "______</Literal>", self.filter_aus_url(a.url))
        self.assertEqual(a.layername, "NI - Memmert - Flur 1 - Flurstück 5/2")

    def test_hessen_fes(self):
        a = self.anfrage("Hessen", "Allmendfeld")
        self.assertIn("VERSION=2.0.0", a.url)
        self.assertIn("&TYPENAMES=ave:Flurstueck", a.url)
        self.assertIn("<fes:ValueReference>ave:flstkennz</fes:ValueReference>", self.filter_aus_url(a.url))

    def test_rlp_kombinierter_filter(self):
        a = self.anfrage("Rheinland-Pfalz", "Friesenhagen (0001)", "012", "007", "3")
        filter_xml = self.filter_aus_url(a.url)
        self.assertIn("&SRSNAME=urn:ogc:def:crs:EPSG::25832", a.url)
        self.assertIn("<ValueReference>gemarkung</ValueReference><Literal>Friesenhagen</Literal>", filter_xml)
        self.assertIn("<Literal>Flur 12</Literal>", filter_xml)
        self.assertIn("<ValueReference>flstnrzae</ValueReference><Literal>7</Literal>", filter_xml)
        self.assertIn("<ValueReference>flstnrnen</ValueReference><Literal>3</Literal>", filter_xml)
        self.assertIsNone(a.hinweis)

    def test_rlp_ohne_nenner_und_mit_hinweis_bei_gleichen_namen(self):
        a = self.anfrage("Rheinland-Pfalz", "Fischbach (0003)")
        self.assertNotIn("flstnrnen", self.filter_aus_url(a.url))
        self.assertIsNotNone(a.hinweis)

    def test_filter_wird_maskiert(self):
        daten = {"051004": {"name": "A&B <Test>", "nummer": "1004", "full_name": "A&B <Test> (1004)"}}
        a = core.bereite_suche_vor("Nordrhein-Westfalen", "A&B <Test>", "1", "1", "", daten)
        # Die Gemarkung geht nur über den Schlüssel in den NRW-Filter ein; der Name steckt nur im Layernamen.
        self.assertNotIn("<Test>", self.filter_aus_url(a.url))
        rlp = {"070001": {"name": "A&B <Test>", "nummer": "0001", "full_name": "A&B <Test> (0001)"}}
        b = core.bereite_suche_vor("Rheinland-Pfalz", "A&B <Test>", "1", "1", "", rlp)
        filter_xml = self.filter_aus_url(b.url)
        self.assertIn("A&amp;B &lt;Test&gt;", filter_xml)
        self.assertNotIn("<Test>", filter_xml)

    def test_mehrdeutiger_name(self):
        with self.assertRaises(core.SucheFehler) as ctx:
            self.anfrage("Nordrhein-Westfalen", "Holthausen")
        self.assertIn("Holthausen (", str(ctx.exception))
        self.assertIn("-mal", str(ctx.exception))

    def test_unbekannte_gemarkung_mit_vorschlag(self):
        with self.assertRaises(core.SucheFehler) as ctx:
            self.anfrage("Nordrhein-Westfalen", "Altenaa")
        self.assertIn("Altena (1004)", str(ctx.exception))

    def test_ungueltige_eingaben(self):
        faelle = [
            ("Nordrhein-Westfalen", "", "1", "1", ""),
            ("Nordrhein-Westfalen", "Altena", "x", "1", ""),
            ("Nordrhein-Westfalen", "Altena", "1", "", ""),
            ("Nordrhein-Westfalen", "Altena", "1", "1", "abc"),
            ("Nordrhein-Westfalen", "Altena", "1000", "1", ""),
            ("Atlantis", "Altena", "1", "1", ""),
        ]
        for land, gem, flur, zaehler, nenner in faelle:
            with self.assertRaises(core.SucheFehler, msg=(land, gem, flur, zaehler, nenner)):
                core.bereite_suche_vor(land, gem, flur, zaehler, nenner, DATEN.get(land, {"x": 1}))

    def test_url_ist_ascii(self):
        a = self.anfrage("Nordrhein-Westfalen", "Altena (1004)")
        a.url.encode("ascii")
        rlp = DATEN["Rheinland-Pfalz"]
        umlaut = next(s for s, e in rlp.items() if re.search("[äöüß]", e["name"].casefold()))
        b = core.bereite_suche_vor("Rheinland-Pfalz", rlp[umlaut]["full_name"], "1", "1", "", rlp)
        b.url.encode("ascii")


class TestAntwort(unittest.TestCase):
    def test_ist_zip(self):
        self.assertTrue(core.ist_zip(b"PK\x03\x04abc"))
        self.assertFalse(core.ist_zip(b"<?xml"))
        self.assertFalse(core.ist_zip(b""))

    def test_wfs_fehler_ows(self):
        xml = (b'<?xml version="1.0"?><ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1">'
               b'<ows:Exception exceptionCode="InvalidParameterValue"><ows:ExceptionText>Ung&#252;ltiger '
               b'Filter\n   &amp; mehr</ows:ExceptionText></ows:Exception></ows:ExceptionReport>')
        text = core.wfs_fehlertext(xml)
        self.assertIn("Der Server meldet einen Fehler:", text)
        self.assertIn("& mehr", text)
        self.assertNotIn("\n", text)

    def test_wfs_fehler_service_exception(self):
        xml = b"<ServiceExceptionReport><ServiceException code='x'><![CDATA[Kaputt]]></ServiceException></ServiceExceptionReport>"
        self.assertIn("Kaputt", core.wfs_fehlertext(xml))

    def test_kein_fehler(self):
        self.assertIsNone(core.wfs_fehlertext(b"PK\x03\x04"))
        self.assertIsNone(core.wfs_fehlertext(b"<wfs:FeatureCollection/>"))
        self.assertIsNone(core.wfs_fehlertext(b""))

    def test_zip_entpacken_und_shapefile_finden(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_pfad = os.path.join(tmp, "a.zip")
            with zipfile.ZipFile(zip_pfad, "w") as z:
                z.writestr("unter/ordner/flst.dbf", b"x")
                z.writestr("unter/ordner/flst.shp", b"x")
            ziel = os.path.join(tmp, "out")
            os.makedirs(ziel)
            core.entpacke_zip_sicher(zip_pfad, ziel)
            self.assertEqual(core.finde_shapefile(ziel), os.path.join(ziel, "unter", "ordner", "flst.shp"))
            leer = os.path.join(tmp, "leer")
            os.makedirs(leer)
            self.assertIsNone(core.finde_shapefile(leer))

    def test_zip_slip_wird_abgelehnt(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_pfad = os.path.join(tmp, "boese.zip")
            with zipfile.ZipFile(zip_pfad, "w") as z:
                z.writestr("../../boese.txt", b"x")
            ziel = os.path.join(tmp, "out")
            os.makedirs(ziel)
            with self.assertRaises(ValueError):
                core.entpacke_zip_sicher(zip_pfad, ziel)
            self.assertFalse(os.path.exists(os.path.join(tmp, "boese.txt")))


if __name__ == "__main__":
    unittest.main()
