"""
conftest.py — Stub mínimo de `qgis` para poder correr estos tests con
pytest normal, SIN una instalación de QGIS.

Alcance deliberadamente LIMITADO: cubre únicamente qgis.core y
qgis.PyQt.QtCore (los dos únicos submódulos que gbif_api.py, filters.py,
aoi.py, exporter.py y batch.py necesitan importar). NO se stubea
qgis.gui ni qgis.PyQt.QtWidgets/QThread -- por eso species_download_dialog.py
y worker.py quedan FUERA de esta suite: requieren un entorno QGIS/Qt real
(o pytest-qgis) y no se intenta fingir un QDialog/QThread aquí.

Advertencia importante para quien lea esto: estos stubs validan la
LÓGICA de este código (ramas, truncamiento, deduplicación, manejo de
cancelación), NO que las llamadas a la API real de PyQGIS tengan la
firma correcta. El bug de "'options' is not a valid keyword argument"
en exporter.py (QgsVectorFileWriter.create) NO lo habría atrapado
ningún test contra este stub, porque el stub no rechaza kwargs
inválidos como sí lo hacen las bindings SIP reales. Solo correr esto
dentro de QGIS de verdad (o con pytest-qgis) detecta ese tipo de error.
"""

import os
import sys
import types

# ---------------------------------------------------------------------
# sys.path: permitir "import GeoSpecies_Tools...." parándose en la
# carpeta que CONTIENE GeoSpecies_Tools (dos niveles arriba de este
# archivo: GeoSpecies_Tools/tests/conftest.py -> .../<repo_root>).
# ---------------------------------------------------------------------
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_PLUGIN_ROOT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _install_qgis_stub():
    if "qgis" in sys.modules:
        return  # ya hay un qgis real (o un stub previo) cargado; no pisarlo

    qgis_pkg = types.ModuleType("qgis")
    qgis_core = types.ModuleType("qgis.core")
    qgis_pyqt = types.ModuleType("qgis.PyQt")
    qgis_pyqt_qtcore = types.ModuleType("qgis.PyQt.QtCore")

    # -------------------- qgis.core --------------------

    class QgsCoordinateReferenceSystem:
        def __init__(self, authid=""):
            self._authid = authid or ""

        def authid(self):
            return self._authid

        def isValid(self):
            return bool(self._authid) and self._authid.upper() != "INVALID"

    class QgsCoordinateTransform:
        def __init__(self, src_crs=None, dst_crs=None, context=None):
            self.src_crs = src_crs
            self.dst_crs = dst_crs
            self.context = context

    class QgsPointXY:
        def __init__(self, x=0.0, y=0.0):
            self._x = x
            self._y = y

        def x(self):
            return self._x

        def y(self):
            return self._y

    class QgsGeometry:
        def __init__(self, point=None):
            self._point = point

        @staticmethod
        def fromPointXY(point):
            return QgsGeometry(point=point)

        def transform(self, xform):
            pass  # no-op: no se ejercita reproyección real en estos tests

        def isEmpty(self):
            return self._point is None

    class QgsField:
        def __init__(self, name, type_=None, typeName="", len=0, prec=0, comment="", subType=None):
            self.name_ = name
            self.type_ = type_
            self.len_ = len

    class QgsFields:
        def __init__(self):
            self._fields = []

        def append(self, field):
            self._fields.append(field)

        def __iter__(self):
            return iter(self._fields)

        def __len__(self):
            return len(self._fields)

    class QgsFeature:
        def __init__(self, fields=None):
            self._fields = fields
            self._attrs = {}
            self._geom = None

        def setGeometry(self, geom):
            self._geom = geom

        def geometry(self):
            return self._geom

        def setAttribute(self, name, value):
            self._attrs[name] = value

        def attribute(self, name):
            return self._attrs.get(name)

    class _TransformContext:
        pass

    class QgsProject:
        _instance = None

        @classmethod
        def instance(cls):
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        def transformContext(self):
            return _TransformContext()

        def crs(self):
            return QgsCoordinateReferenceSystem("")  # inválido a propósito: sin proyecto real cargado

    class QgsVectorFileWriter:
        NoError = 0

        class SaveVectorOptions:
            def __init__(self):
                self.driverName = ""
                self.fileEncoding = ""

        @staticmethod
        def writeAsVectorFormatV3(*args, **kwargs):
            raise NotImplementedError(
                "El stub de qgis no implementa escritura real de archivos vectoriales -- "
                "export_points() no está cubierto por esta suite, solo build_fields()/record_to_feature()."
            )

    class QgsVectorLayer:
        def __init__(self, *args, **kwargs):
            raise NotImplementedError(
                "El stub de qgis no implementa QgsVectorLayer real -- "
                "fuera de alcance de esta suite (ver docstring de conftest.py)."
            )

    for _name, _obj in list(locals().items()):
        if _name.startswith("Qgs"):
            setattr(qgis_core, _name, _obj)

    # -------------------- qgis.PyQt.QtCore --------------------

    class QVariant:
        Bool = 1
        Int = 2
        LongLong = 4
        Double = 6
        String = 10
        Date = 14
        DateTime = 16

    class Qt:
        AlignCenter = 0x0084
        AlignLeft = 0x0001
        AlignRight = 0x0002
        AlignTop = 0x0020
        AlignBottom = 0x0040
        Horizontal = 0x1
        Vertical = 0x2
        WindowContextHelpButtonHint = 0x00001000
        WaitCursor = 3

    qgis_pyqt_qtcore.Qt = Qt
    qgis_pyqt_qtcore.QVariant = QVariant
    # A propósito NO se define QMetaType aquí: así utils/qt_compat.py toma
    # la rama de fallback QVariant (PyQt5), que es la rama que de verdad
    # se ejercita en QGIS 3.28/3.34 -- la versión que motivó este plugin.

    qgis_pyqt.QtCore = qgis_pyqt_qtcore
    qgis_pkg.core = qgis_core
    qgis_pkg.PyQt = qgis_pyqt

    sys.modules["qgis"] = qgis_pkg
    sys.modules["qgis.core"] = qgis_core
    sys.modules["qgis.PyQt"] = qgis_pyqt
    sys.modules["qgis.PyQt.QtCore"] = qgis_pyqt_qtcore


_install_qgis_stub()
