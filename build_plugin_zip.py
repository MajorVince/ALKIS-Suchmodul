#!/usr/bin/env python3
"""Baut ein installierbares Plugin-ZIP (QGIS: Erweiterungen -> Aus ZIP installieren).

Aufruf:  python3 build_plugin_zip.py [ausgabe.zip]
"""

import os
import sys
import zipfile

ORDNERNAME = "alkis_suchmodul"
DATEIEN = [
    "__init__.py",
    "alkis_core.py",
    "flurstueck_suche.py",
    "flurstueck_dialog.py",
    "flurstueck_dialog.ui",
    "metadata.txt",
    "icon.png",
    "LICENSE",
    "README.md",
    "gemarkungen_nrw.json",
    "gemarkungen_nieder.json",
    "gemarkungen_hessen.json",
    "gemarkungen_rlp.json",
]


def main():
    hier = os.path.dirname(os.path.abspath(__file__))
    ziel = sys.argv[1] if len(sys.argv) > 1 else os.path.join(hier, ORDNERNAME + ".zip")
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as archiv:
        for datei in DATEIEN:
            pfad = os.path.join(hier, datei)
            if not os.path.exists(pfad):
                sys.exit("Datei fehlt: {}".format(datei))
            archiv.write(pfad, "{}/{}".format(ORDNERNAME, datei))
    print("Erstellt: {}".format(ziel))


if __name__ == "__main__":
    main()
