"""Pruebas de conservación, sustitución y rechazo del contrato de importación."""
import csv
import hashlib
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape
from zipfile import ZipFile

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/importar_modelo.py'


def csv_bytes(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base, self.output = self.root / 'base.sqlite', self.root / 'output.sqlite'
        with sqlite3.connect(self.base) as db:
            db.executescript('''CREATE TABLE parcelas(ID_POLIGONO TEXT PRIMARY KEY, geometria_geojson TEXT);
                INSERT INTO parcelas VALUES ('AGC_001','{"type":"Polygon","coordinates":[]}');
                CREATE TABLE capas(id TEXT PRIMARY KEY,nombre TEXT NOT NULL,grupo TEXT NOT NULL,
                    descripcion TEXT,unidad TEXT,min REAL,max REAL);
                INSERT INTO capas VALUES ('previo','Previo','Previo',NULL,'mm',1,1);
                CREATE TABLE datos(ID_POLIGONO TEXT,capa_id TEXT,fecha TEXT,variable TEXT,valor REAL,
                    PRIMARY KEY(ID_POLIGONO,capa_id,fecha,variable));
                INSERT INTO datos VALUES ('AGC_001','previo','2024-01-01','lluvia',1);''')
        self.original = self.base.read_bytes()
        self.results = [dict(ID_POLIGONO='AGC_001', version_modelo='v1', prediccion=3.2,
                             observado=3.5, unidad_rendimiento='t/ha', valor_base_t_ha=4,
                             tipo_prediccion='final', prediccion_explicada=3.2)]
        self.contributions = [dict(ID_POLIGONO='AGC_001', version_modelo='v1', variable='tmin',
                                   valor=10, unidad='°C', aporte_t_ha=-.8)]

    def tearDown(self):
        self.temp.cleanup()

    def package(self, measurements=None, layers=None):
        filename = self.root / 'input.zip'
        with ZipFile(filename, 'w') as z:
            z.writestr('resultados.csv', csv_bytes(self.results))
            z.writestr('contribuciones.csv', csv_bytes(self.contributions))
            if measurements is not None:
                z.writestr('mediciones.csv', csv_bytes(measurements))
            if layers is not None:
                z.writestr('capas.csv', csv_bytes(layers))
        return filename

    def run_import(self, filename, *flags, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), str(filename), '--dataset', 'prueba',
                                 '--base', str(self.base), '--salida', str(self.output), *flags],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertEqual(self.base.read_bytes(), self.original, 'Se modificó la base de referencia')
        return result

    def row(self):
        with sqlite3.connect(self.output) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute('SELECT * FROM parcelas_resultados').fetchone())

    def test_preserves_geometry_existing_data_and_validates_shap(self):
        self.run_import(self.package(), '--sin-mediciones')
        row = self.row()
        self.assertAlmostEqual(row['error_firmado_t_ha'], -.3)
        self.assertEqual(row['shap_estado'], 'coherente')
        self.assertIsNone(row['mae_historial_t_ha'])
        with sqlite3.connect(self.output) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM datos').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT geometria_geojson FROM parcelas').fetchone()[0], '{"type":"Polygon","coordinates":[]}')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_reimport_removes_missing_observed_and_does_not_duplicate(self):
        self.run_import(self.package(), '--sin-mediciones')
        self.results[0]['observado'] = ''
        self.run_import(self.package(), '--sin-mediciones')
        self.assertIsNone(self.row()['observado'])
        self.assertIsNone(self.row()['error_absoluto_t_ha'])
        with sqlite3.connect(self.output) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM parcelas_resultados').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM parcelas_contribuciones').fetchone()[0], 1)
        self.assertEqual(len(list(self.root.glob('output.sqlite.backup-*'))), 1)

    def test_zero_observed_is_not_missing_and_relative_error_is_null(self):
        self.results[0]['observado'] = 0
        self.run_import(self.package(), '--sin-mediciones')
        self.assertEqual(self.row()['observado'], 0)
        self.assertEqual(self.row()['error_absoluto_t_ha'], 3.2)
        self.assertIsNone(self.row()['error_relativo_pct'])

    def test_incoherent_shap_preserves_original_base(self):
        self.results[0]['prediccion'] = 5
        self.run_import(self.package(), '--sin-mediciones')
        self.assertEqual(self.row()['shap_estado'], 'incoherente')
        self.assertEqual(self.row()['valor_base_t_ha'], 4)
        self.assertAlmostEqual(self.row()['shap_residuo_t_ha'], 1.8)

    def test_unknown_provenance_and_oof_without_fold_stay_pending(self):
        self.results[0]['tipo_prediccion'] = ''
        self.run_import(self.package(), '--sin-mediciones')
        self.assertEqual(self.row()['shap_estado'], 'procedencia_pendiente')
        self.results[0]['tipo_prediccion'] = 'oof'
        self.run_import(self.package(), '--sin-mediciones')
        self.assertEqual(self.row()['shap_estado'], 'procedencia_pendiente')

    def test_duplicate_and_nonfinite_inputs_leave_output_unchanged(self):
        self.run_import(self.package(), '--sin-mediciones')
        previous = hashlib.sha256(self.output.read_bytes()).digest()
        self.results.append(dict(self.results[0]))
        self.run_import(self.package(), '--sin-mediciones', success=False)
        self.results.pop()
        self.results[0]['prediccion'] = 'NaN'
        self.run_import(self.package(), '--sin-mediciones', success=False)
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).digest(), previous)

    def test_unknown_geometry_is_reported_and_stored(self):
        self.results[0]['ID_POLIGONO'] = 'AGC_SIN_GEOM'
        self.contributions[0]['ID_POLIGONO'] = 'AGC_SIN_GEOM'
        result = self.run_import(self.package(), '--sin-mediciones')
        self.assertIn('IDs sin geometría', result.stdout)
        self.assertEqual(self.row()['ID_POLIGONO'], 'AGC_SIN_GEOM')

    def test_measurement_snapshot_replaces_only_owned_keys(self):
        layers = [dict(id='s2_ndvi', nombre='NDVI Sentinel-2', unidad='adimensional',
                       fuente='Sentinel-2', agregacion='mediana mensual')]
        measurement = dict(ID_POLIGONO='AGC_001', capa_id='s2_ndvi', fecha='2025-04-15', variable='ndvi', valor=.4)
        self.run_import(self.package([measurement], layers))
        measurement['fecha'], measurement['valor'] = '2025-05-15', ''
        self.run_import(self.package([measurement], layers))
        with sqlite3.connect(self.output) as db:
            rows = list(db.execute('SELECT capa_id,fecha,valor FROM datos ORDER BY capa_id'))
            self.assertEqual(rows, [('previo', '2024-01-01', 1), ('s2_ndvi', '2025-05-15', None)])
        self.run_import(self.package(), '--sin-mediciones')
        with sqlite3.connect(self.output) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM datos').fetchone()[0], 2)
        self.run_import(self.package())
        with sqlite3.connect(self.output) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM datos').fetchone()[0], 2)

    def test_measurement_duplicates_and_existing_data_collisions_fail(self):
        layers = [dict(id='s2_ndvi', nombre='NDVI', unidad='adimensional', fuente='Sentinel-2', agregacion='media mensual')]
        row = dict(ID_POLIGONO='AGC_001', capa_id='s2_ndvi', fecha='2025-04-15', variable='ndvi', valor=.4)
        self.run_import(self.package([row, row], layers), success=False)
        self.assertFalse(self.output.exists())
        row.update(capa_id='previo', fecha='2024-01-01', variable='lluvia')
        layers[0].update(id='previo', nombre='Previo', unidad='mm')
        self.run_import(self.package([row], layers), success=False)
        self.assertFalse(self.output.exists())

    def test_read_only_validation_and_reference_guard(self):
        self.run_import(self.package(), '--sin-mediciones', '--validar')
        self.assertFalse(self.output.exists())
        self.run_import(self.package(), '--sin-mediciones', '--salida', str(self.base), success=False)

    def test_xlsx_inline_numeric_cells_and_missing_observed(self):
        # OOXML mínimo como fixture de lectura; no es un workbook entregable.
        filename = self.root / 'input.xlsx'
        head = list(self.results[0])
        values = list(self.results[0].values())
        values[head.index('observado')] = ''
        rows = []
        for index, row in enumerate([head, values], 1):
            cells = []
            for col, value in enumerate(row):
                ref = f'{chr(65+col)}{index}'
                if isinstance(value, (int, float)):
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
            rows.append(f'<row r="{index}">{"".join(cells)}</row>')
        with ZipFile(filename, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="resultados" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(rows) + '</sheetData></worksheet>')
        self.run_import(filename, '--sin-mediciones')
        self.assertEqual(self.row()['prediccion'], 3.2)
        self.assertIsNone(self.row()['observado'])


if __name__ == '__main__':
    unittest.main()
