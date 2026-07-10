"""
test_aoi.py — Cubre únicamente _vertex_count() y _simplify_to_vertex_budget()
de aoi.py: la lógica de control de flujo (cuándo parar de simplificar,
qué hacer si la simplificación colapsa a vacío) que es la parte
genuinamente propia de este archivo.

NO se testea layer_geometry_to_wkt_4326() aquí: requeriría fingir una
QgsVectorLayer completa (getFeatures, crs, features con geometría real)
y el valor de un fake tan elaborado sería bajo frente al riesgo de que
diverja del comportamiento real de PyQGIS -- exactamente el tipo de
falsa confianza que advierte el docstring de conftest.py. Esa función
solo se valida corriendo dentro de QGIS real.
"""

import json

from GeoSpecies_Tools.species_download import aoi


class FakeGeometry:
    """Geometría de prueba: una lista plana de N puntos como si fuera el
    anillo exterior de un polígono. simplify() reduce el conteo de forma
    determinística según la tolerancia, para poder controlar el test."""

    def __init__(self, num_points, empty=False):
        self.num_points = num_points
        self._empty = empty

    def asJson(self):
        coords = [[float(i), float(i)] for i in range(self.num_points)]
        return json.dumps({"type": "Polygon", "coordinates": [coords]})

    def isEmpty(self):
        return self._empty

    def simplify(self, tolerance):
        # Cada duplicación de tolerancia reduce a la mitad los puntos.
        factor = max(1, int(tolerance / 0.0001))
        new_count = max(1, self.num_points // (factor * 2))
        return FakeGeometry(new_count)


class FakeGeometryCollapsesToEmpty:
    """Simula una geometría que colapsa a vacía apenas se intenta
    simplificar -- para probar que _simplify_to_vertex_budget no falla
    y conserva la última geometría válida en vez de propagar el vacío."""

    def __init__(self, num_points):
        self.num_points = num_points

    def asJson(self):
        coords = [[float(i), float(i)] for i in range(self.num_points)]
        return json.dumps({"type": "Polygon", "coordinates": [coords]})

    def isEmpty(self):
        return False

    def simplify(self, tolerance):
        empty = FakeGeometryCollapsesToEmpty(0)
        empty.isEmpty = lambda: True
        return empty


# ---------------------------------------------------------------------
# _vertex_count
# ---------------------------------------------------------------------

def test_vertex_count_polygon():
    geom = FakeGeometry(10)
    assert aoi._vertex_count(geom) == 10


def test_vertex_count_multipolygon():
    coords = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]],
            [[[2.0, 2.0], [3.0, 2.0]]],
        ],
    }

    class FakeMulti:
        def asJson(self):
            return json.dumps(coords)

    assert aoi._vertex_count(FakeMulti()) == 5  # 3 + 2 puntos


def test_vertex_count_invalid_json_returns_zero():
    class BrokenGeometry:
        def asJson(self):
            return "no es json válido"

    assert aoi._vertex_count(BrokenGeometry()) == 0


# ---------------------------------------------------------------------
# _simplify_to_vertex_budget
# ---------------------------------------------------------------------

def test_simplify_returns_original_if_already_under_budget():
    geom = FakeGeometry(50)
    result = aoi._simplify_to_vertex_budget(geom, max_vertices=100)
    assert result is geom  # no se llamó a simplify() para nada


def test_simplify_reduces_vertex_count_under_budget():
    geom = FakeGeometry(10000)
    result = aoi._simplify_to_vertex_budget(geom, max_vertices=500)
    assert aoi._vertex_count(result) <= 500


def test_simplify_gives_up_gracefully_if_geometry_collapses_to_empty():
    """
    Si simplify() colapsa la geometría a vacía en el primer intento, la
    función debe devolver la geometría ORIGINAL (no la vacía, no lanzar
    excepción) -- aunque siga por encima del presupuesto de vértices.
    Esto documenta una limitación real: en ese caso el WKT resultante
    puede seguir siendo más complejo de lo ideal para el parámetro
    `geometry` de GBIF.
    """
    geom = FakeGeometryCollapsesToEmpty(10000)
    result = aoi._simplify_to_vertex_budget(geom, max_vertices=500)
    assert result is geom
    assert aoi._vertex_count(result) == 10000  # sigue por encima del presupuesto
