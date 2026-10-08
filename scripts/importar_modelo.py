#!/usr/bin/env python3
"""Importación local de tablas XLSX/ZIP/CSV a una copia consistente de SQLite.

No ejecuta modelos ni Excel. Usa únicamente la biblioteca estándar de Python.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import posixpath
import re
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TABLES = {'resultados', 'contribuciones', 'mediciones', 'capas'}
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MAX_BYTES = 100 * 1024 * 1024


def fail(message):
    raise ValueError(message)


def text(value):
    return '' if value is None else str(value).strip()


def number(value, field, optional=False):
    if text(value) == '':
        if optional:
            return None
        fail(f'Falta un número en {field}')
    if isinstance(value, bool):
        fail(f'{field}: un booleano no es una medición')
    try:
        result = float(value)
    except (TypeError, ValueError):
        fail(f'{field}: número inválido {value!r}; usa celdas numéricas o punto decimal')
    if not math.isfinite(result):
        fail(f'{field}: no se admite NaN ni infinito')
    return result


def records(rows, name):
    rows = list(rows)
    if not rows:
        return []
    header = [text(x) for x in rows[0]]
    if not all(header) or len(set(header)) != len(header):
        fail(f'{name}: encabezados vacíos o repetidos')
    output = []
    for index, values in enumerate(rows[1:], 2):
        if not any(text(x) for x in values):
            continue
        if len(values) > len(header) and any(text(x) for x in values[len(header):]):
            fail(f'{name}, fila {index}: hay valores fuera de las columnas')
        row = dict(zip(header, list(values) + [''] * (len(header) - len(values))))
        row['_fila'] = index
        output.append(row)
    return output


def csv_records(data, name):
    content = data.decode('utf-8-sig')
    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    return records(csv.reader(io.StringIO(content), dialect), name)


def read_xlsx(archive):
    """Lee tablas de valores: cadenas compartidas/inline, números y fechas Excel."""
    strings = []
    if 'xl/sharedStrings.xml' in archive.namelist():
        root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
        strings = [''.join(si.itertext()) for si in root.findall('s:si', NS)]
    date_styles = set()
    if 'xl/styles.xml' in archive.namelist():
        styles = ET.fromstring(archive.read('xl/styles.xml'))
        formats = {int(x.attrib['numFmtId']): x.attrib.get('formatCode', '')
                   for x in styles.findall('s:numFmts/s:numFmt', NS)}
        for i, xf in enumerate(styles.findall('s:cellXfs/s:xf', NS)):
            fmt = int(xf.attrib.get('numFmtId', 0))
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', formats.get(fmt, '')).lower()
            if fmt in set(range(14, 23)) | {45, 46, 47} or re.search(r'[dy]', code):
                date_styles.add(i)
    workbook = ET.fromstring(archive.read('xl/workbook.xml'))
    props = workbook.find('s:workbookPr', NS)
    epoch = datetime(1904, 1, 1) if props is not None and props.attrib.get('date1904') in {'1', 'true'} else datetime(1899, 12, 30)
    relationships = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
    targets = {r.attrib['Id']: r.attrib['Target'] for r in relationships}
    output = {}
    for sheet in workbook.findall('s:sheets/s:sheet', NS):
        name = sheet.attrib['name'].strip().lower()
        if name not in TABLES:
            continue
        if name in output:
            fail(f'Hoja repetida: {name}')
        target = targets[sheet.attrib[f'{{{REL}}}id']]
        path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        root = ET.fromstring(archive.read(path))
        rows = []
        for row in root.findall('s:sheetData/s:row', NS):
            values = {}
            for cell in row.findall('s:c', NS):
                ref = cell.attrib.get('r', '')
                letters = re.match(r'([A-Z]+)', ref)
                if not letters:
                    fail(f'{name}: celda sin referencia')
                column = 0
                for letter in letters.group(1):
                    column = column * 26 + ord(letter) - 64
                if column > 128:
                    fail(f'{name}: el contrato admite hasta 128 columnas')
                if cell.find('s:f', NS) is not None:
                    fail(f'{name}!{ref}: exporta valores, no fórmulas con resultados en caché')
                kind = cell.attrib.get('t', 'n')
                value = cell.findtext('s:v', '', NS)
                if kind == 's':
                    value = strings[int(value)]
                elif kind == 'inlineStr':
                    inline = cell.find('s:is', NS)
                    value = ''.join(inline.itertext()) if inline is not None else ''
                elif kind == 'e':
                    fail(f'{name}!{ref}: error de Excel {value}')
                elif kind == 'b':
                    value = value == '1'
                elif kind == 'n' and value:
                    value = float(value)
                    if int(cell.attrib.get('s', 0)) in date_styles:
                        value = (epoch + timedelta(days=value)).date().isoformat()
                    elif value.is_integer():
                        value = int(value)
                values[column - 1] = value
            if values:
                rows.append([values.get(i, '') for i in range(max(values) + 1)])
        output[name] = records(rows, name)
    return output


def load_tables(filename):
    if filename.stat().st_size > MAX_BYTES:
        fail('El archivo supera 100 MiB')
    if filename.suffix.lower() == '.csv':
        return {'resultados': csv_records(filename.read_bytes(), 'resultados')}
    if filename.suffix.lower() not in {'.zip', '.xlsx'}:
        fail('Usa un Excel .xlsx, un ZIP de CSVs o un CSV de resultados; .xls no está soportado')
    with ZipFile(filename) as archive:
        if sum(x.file_size for x in archive.infolist()) > MAX_BYTES:
            fail('El contenido descomprimido supera 100 MiB')
        if filename.suffix.lower() == '.xlsx':
            return read_xlsx(archive)
        output = {}
        for member in archive.namelist():
            basename = Path(member).name.lower()
            name = basename.removesuffix('.csv')
            if name in TABLES and basename.endswith('.csv'):
                if name in output:
                    fail(f'Archivo repetido en ZIP: {basename}')
                output[name] = csv_records(archive.read(member), name)
        return output


def identifier(value, field):
    result = text(value)
    if not result or len(result) > 128:
        fail(f'{field}: identificador vacío o demasiado largo')
    return result


def key(row, dataset):
    return (identifier(row.get('ID_POLIGONO'), 'ID_POLIGONO'),
            identifier(row.get('campana', row.get('campaña')) or dataset, 'campana/conjunto'),
            identifier(row.get('version_modelo', row.get('versión_modelo')), 'version_modelo'))


def validate(tables, args, known):
    results, contributions, measurements, layers = {}, {}, {}, {}
    warnings = []
    for row in tables.get('resultados', []):
        k = key(row, args.dataset)
        if k in results:
            fail(f'resultados: clave repetida {k}')
        prediction = number(row.get('prediccion'), f'prediccion {k}')
        observed = number(row.get('observado'), f'observado {k}', True)
        if prediction < 0 or (observed is not None and observed < 0):
            fail(f'{k}: el rendimiento no puede ser negativo')
        unit = text(row.get('unidad_rendimiento'))
        if unit != 't/ha':
            fail(f'{k}: unidad_rendimiento debe ser t/ha; convierte otras unidades en el notebook')
        kind = text(row.get('tipo_prediccion')) or 'desconocida'
        if kind not in {'final', 'oof', 'desconocida'}:
            fail(f'{k}: tipo_prediccion debe ser final, oof o desconocida')
        base = number(row.get('valor_base_t_ha'), f'valor_base_t_ha {k}', True)
        error = None if observed is None else prediction - observed
        absolute = None if error is None else abs(error)
        relative = None if observed in (None, 0) else absolute / abs(observed) * 100
        results[k] = dict(prediccion=prediction, observado=observed, valor_base_t_ha=base,
                          error_firmado_t_ha=error, error_absoluto_t_ha=absolute,
                          error_relativo_pct=relative, unidad_rendimiento=unit,
                          tipo_prediccion=kind, fold_id=text(row.get('fold_id')) or None,
                          prediccion_explicada=number(row.get('prediccion_explicada'), f'prediccion_explicada {k}', True),
                          shap_estado='no_disponible', shap_residuo_t_ha=None)
    if not results:
        fail('Falta la tabla/hoja resultados o no contiene filas')
    groups = {k[1:] for k in results}
    if len(groups) != 1:
        fail('Cada archivo debe contener un único conjunto y versión de modelo; importa versiones por separado')
    for row in tables.get('contribuciones', []):
        k = key(row, args.dataset)
        if k not in results:
            fail(f'contribuciones: no existe un resultado para {k}')
        variable = identifier(row.get('variable'), 'variable')
        ck = k + (variable,)
        if ck in contributions:
            fail(f'contribuciones: clave repetida {ck}')
        unit = text(row.get('unidad'))
        if not unit:
            fail(f'{ck}: falta unidad de la variable')
        contributions[ck] = (number(row.get('valor'), f'valor {ck}', True), unit,
                             number(row.get('aporte_t_ha'), f'aporte_t_ha {ck}'))
    by_result = {}
    for ck, values in contributions.items():
        by_result.setdefault(ck[:3], []).append(values[2])
    for k, row in results.items():
        values = by_result.get(k, [])
        if not values:
            continue
        if row['valor_base_t_ha'] is None:
            row['shap_estado'] = 'base_ausente'
            continue
        reconstructed = row['valor_base_t_ha'] + math.fsum(values)
        residue = row['prediccion'] - reconstructed
        row['shap_residuo_t_ha'] = residue
        explained = row['prediccion_explicada']
        if abs(residue) > args.tolerancia or (explained is not None and abs(explained - reconstructed) > args.tolerancia):
            row['shap_estado'] = 'incoherente'
        elif row['tipo_prediccion'] == 'desconocida' or explained is None or (row['tipo_prediccion'] == 'oof' and not row['fold_id']):
            row['shap_estado'] = 'procedencia_pendiente'
        else:
            row['shap_estado'] = 'coherente'
    if args.sin_mediciones:
        if tables.get('mediciones'):
            warnings.append('Mediciones y capas omitidas por --sin-mediciones; las series anteriores se conservan')
    else:
        for row in tables.get('capas', []):
            layer_id = identifier(row.get('id'), 'capa id')
            if layer_id in layers:
                fail(f'capas: id repetido {layer_id}')
            unit, source, aggregation = (text(row.get(x)) for x in ['unidad', 'fuente', 'agregacion'])
            if not unit or unit.lower() == 'variable' or not source or not aggregation:
                fail(f'capas {layer_id}: completa unidad, fuente y agregacion antes de importar mediciones')
            layers[layer_id] = (identifier(row.get('nombre'), 'nombre'), text(row.get('grupo')) or 'Modelo',
                                text(row.get('descripcion')), unit, source, aggregation)
        for row in tables.get('mediciones', []):
            pid, layer_id = identifier(row.get('ID_POLIGONO'), 'ID_POLIGONO'), identifier(row.get('capa_id'), 'capa_id')
            when = text(row.get('fecha'))
            if when:
                try:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', when):
                        raise ValueError('Formato de fecha')
                    date.fromisoformat(when)
                except ValueError:
                    fail(f'mediciones: fecha inválida {when!r}; usa AAAA-MM-DD o vacío para estáticos')
            variable = identifier(row.get('variable'), 'variable')
            mk = (pid, layer_id, when, variable)
            if mk in measurements:
                fail(f'mediciones: clave repetida {mk}; separa las fuentes/variables en capa_id')
            if layer_id not in layers:
                fail(f'mediciones: falta metadata de capa {layer_id}')
            measurements[mk] = number(row.get('valor'), f'valor {mk}', True)
    unknown = sorted(({k[0] for k in results} | {k[0] for k in measurements}) - known)
    if unknown:
        warnings.append(f'IDs sin geometría (se almacenan, no se dibujan): {", ".join(unknown)}')
    status = {}
    for row in results.values():
        status[row['shap_estado']] = status.get(row['shap_estado'], 0) + 1
    return results, contributions, measurements, layers, warnings, status


def schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS parcelas_resultados (
      ID_POLIGONO TEXT NOT NULL, campaña TEXT NOT NULL, versión_modelo TEXT NOT NULL,
      prediccion REAL NOT NULL, observado REAL, PRIMARY KEY(ID_POLIGONO,campaña,versión_modelo));
    CREATE TABLE IF NOT EXISTS parcelas_contribuciones (
      ID_POLIGONO TEXT NOT NULL, campaña TEXT NOT NULL, versión_modelo TEXT NOT NULL,
      variable TEXT NOT NULL, valor REAL, unidad TEXT NOT NULL, aporte_t_ha REAL NOT NULL,
      PRIMARY KEY(ID_POLIGONO,campaña,versión_modelo,variable));
    CREATE TABLE IF NOT EXISTS capas (
      id TEXT PRIMARY KEY,nombre TEXT NOT NULL,grupo TEXT NOT NULL,descripcion TEXT,unidad TEXT,min REAL,max REAL);
    CREATE TABLE IF NOT EXISTS datos (
      ID_POLIGONO TEXT NOT NULL,capa_id TEXT NOT NULL,fecha TEXT NOT NULL DEFAULT '',
      variable TEXT NOT NULL DEFAULT '',valor REAL,PRIMARY KEY(ID_POLIGONO,capa_id,fecha,variable));
    CREATE TABLE IF NOT EXISTS modelo_importaciones (
      campana TEXT NOT NULL,version_modelo TEXT NOT NULL,dataset_id TEXT NOT NULL,label TEXT NOT NULL,
      source_file TEXT NOT NULL,source_sha256 TEXT NOT NULL,imported_at TEXT NOT NULL,
      PRIMARY KEY(campana,version_modelo));
    CREATE TABLE IF NOT EXISTS modelo_mediciones (
      campana TEXT NOT NULL,version_modelo TEXT NOT NULL,ID_POLIGONO TEXT NOT NULL,
      capa_id TEXT NOT NULL,fecha TEXT NOT NULL,variable TEXT NOT NULL,
      PRIMARY KEY(ID_POLIGONO,capa_id,fecha,variable));
    CREATE INDEX IF NOT EXISTS idx_datos_capa ON datos(capa_id,fecha);
    ''')
    additions = dict(valor_base_t_ha='REAL',error_firmado_t_ha='REAL',error_absoluto_t_ha='REAL',
                     error_relativo_pct='REAL',mae_historial_t_ha='REAL',unidad_rendimiento="TEXT NOT NULL DEFAULT 't/ha'",
                     tipo_prediccion="TEXT NOT NULL DEFAULT 'desconocida'",fold_id='TEXT',
                     prediccion_explicada='REAL',shap_estado="TEXT NOT NULL DEFAULT 'procedencia_pendiente'",shap_residuo_t_ha='REAL')
    names = {x[1] for x in db.execute('PRAGMA table_info(parcelas_resultados)')}
    for name, sql_type in additions.items():
        if name not in names:
            db.execute(f'ALTER TABLE parcelas_resultados ADD COLUMN {name} {sql_type}')
    layer_columns = {x[1] for x in db.execute('PRAGMA table_info(capas)')}
    for name in ['fuente', 'agregacion']:
        if name not in layer_columns:
            db.execute(f'ALTER TABLE capas ADD COLUMN {name} TEXT')


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def import_data(args):
    incoming, base, output = args.archivo.resolve(), args.base.resolve(), args.salida.resolve()
    if not base.is_file():
        fail(f'No existe la base de geometrías: {base}')
    if output == base or (output.exists() and os.path.samefile(output, base)):
        fail('La salida debe ser distinta de la base de referencia')
    if output == incoming or (output.exists() and os.path.samefile(output, incoming)):
        fail('La salida no puede reemplazar el archivo de entrada')
    source = output if output.exists() else base
    with readonly(base) as original:
        known = {x[0] for x in original.execute('SELECT ID_POLIGONO FROM parcelas')}
    tables = load_tables(incoming)
    results, contributions, measurements, layers, warnings, status = validate(tables, args, known)
    summary = dict(resultados=len(results), observados=sum(x['observado'] is not None for x in results.values()),
                   contribuciones=len(contributions), mediciones=len(measurements), capas=len(layers),
                   shap=status, avisos=warnings, origen=incoming.name,
                   sha256=hashlib.sha256(incoming.read_bytes()).hexdigest())
    if args.validar:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output.name + '.tmp-', dir=output.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        with readonly(source) as old, sqlite3.connect(temporary) as target:
            old.backup(target)
            target.execute('PRAGMA journal_mode=DELETE')
            schema(target)
            camp, model = next(iter(results))[1:]
            with target:
                target.execute('DELETE FROM parcelas_contribuciones WHERE campaña=? AND versión_modelo=?', (camp, model))
                target.execute('DELETE FROM parcelas_resultados WHERE campaña=? AND versión_modelo=?', (camp, model))
                columns = list(next(iter(results.values())))
                sql = f'INSERT INTO parcelas_resultados (ID_POLIGONO,campaña,versión_modelo,{",".join(columns)}) VALUES ({",".join("?" for _ in range(3+len(columns)))})'
                target.executemany(sql, [k + tuple(row[name] for name in columns) for k, row in results.items()])
                target.executemany('INSERT INTO parcelas_contribuciones VALUES (?,?,?,?,?,?,?)',
                                   [k + values for k, values in contributions.items()])
                if not args.sin_mediciones and 'mediciones' in tables:
                    owned = list(target.execute('SELECT ID_POLIGONO,capa_id,fecha,variable FROM modelo_mediciones WHERE campana=? AND version_modelo=?', (camp, model)))
                    # Un archivo es la instantánea completa de las mediciones de este conjunto.
                    target.executemany('DELETE FROM datos WHERE ID_POLIGONO=? AND capa_id=? AND fecha=? AND variable=?', owned)
                    target.execute('DELETE FROM modelo_mediciones WHERE campana=? AND version_modelo=?', (camp, model))
                    for k, value in measurements.items():
                        if target.execute('SELECT 1 FROM datos WHERE ID_POLIGONO=? AND capa_id=? AND fecha=? AND variable=?', k).fetchone():
                            fail(f'Medición {k} ya pertenece a datos previos u otro conjunto; usa un capa_id distinto')
                        target.execute('INSERT INTO datos VALUES (?,?,?,?,?)', k + (value,))
                        target.execute('INSERT INTO modelo_mediciones VALUES (?,?,?,?,?,?)', (camp, model) + k)
                    for lid, values in layers.items():
                        existing = target.execute('SELECT unidad,fuente,agregacion FROM capas WHERE id=?', (lid,)).fetchone()
                        if existing is not None and existing != values[3:]:
                            fail(f'La capa {lid} ya existe con otra procedencia/unidad; usa un id distinto')
                        target.execute('''INSERT INTO capas(id,nombre,grupo,descripcion,unidad,fuente,agregacion)
                          VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET nombre=excluded.nombre,
                          grupo=excluded.grupo,descripcion=excluded.descripcion,unidad=excluded.unidad,
                          fuente=excluded.fuente,agregacion=excluded.agregacion''', (lid,) + values)
                        target.execute('UPDATE capas SET min=(SELECT MIN(valor) FROM datos WHERE capa_id=?), max=(SELECT MAX(valor) FROM datos WHERE capa_id=?) WHERE id=?', (lid, lid, lid))
                label = args.etiqueta or 'Resultados preliminares'
                target.execute('INSERT OR REPLACE INTO modelo_importaciones VALUES (?,?,?,?,?,?,?)',
                               (camp, model, args.dataset, label, incoming.name, summary['sha256'], datetime.now(timezone.utc).isoformat()))
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                fail('Falló la integridad de SQLite')
            if list(target.execute('SELECT * FROM parcelas ORDER BY ID_POLIGONO')) != list(old.execute('SELECT * FROM parcelas ORDER BY ID_POLIGONO')):
                fail('Se alteraron las geometrías')
        if output.exists():
            for suffix in ['-wal', '-shm', '-journal']:
                if Path(str(output) + suffix).exists():
                    fail('Detén el servidor que utiliza la salida antes de reemplazarla')
            backup = output.with_name(output.name + '.backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
            with readonly(output) as previous, sqlite3.connect(backup) as copy:
                previous.backup(copy)
            summary['respaldo'] = str(backup)
        os.replace(temporary, output)
        summary['salida'] = str(output)
        summary['integridad'] = 'ok'
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archivo', type=Path)
    parser.add_argument('--dataset', required=True, help='Identificador estable del conjunto, sin interpretación temporal')
    parser.add_argument('--etiqueta', help='Nombre legible del conjunto')
    parser.add_argument('--base', type=Path, default=ROOT / 'db/parcelas_master.sqlite')
    parser.add_argument('--salida', type=Path, default=ROOT / 'db/parcelas_modelo.sqlite')
    parser.add_argument('--sin-mediciones', action='store_true', help='Importar solo resultados y contribuciones; conservar las series previas')
    parser.add_argument('--validar', action='store_true', help='Revisar entrada sin escribir una base')
    parser.add_argument('--tolerancia', type=float, default=1e-6)
    args = parser.parse_args()
    try:
        identifier(args.dataset, 'dataset')
        if not math.isfinite(args.tolerancia) or not 0 < args.tolerancia <= 1e-3:
            fail('La tolerancia debe ser positiva y <= 0.001 t/ha')
        import_data(args)
    except (ValueError, OSError, sqlite3.Error, ET.ParseError, KeyError, IndexError, BadZipFile, OverflowError) as error:
        print(f'Importación cancelada: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
