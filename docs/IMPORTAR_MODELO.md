# Cambiar el archivo del modelo sin cambiar el dashboard

El rendimiento es un resultado cerrado por parcela. El mapa y las barras muestran
un conjunto seleccionado, sin eje de campañas. Las fechas pertenecen a las
mediciones, no crean predicciones mensuales de rendimiento.

## Flujo

1. El equipo exporta un `.xlsx` con las hojas de este contrato, o un ZIP con CSVs
   del mismo nombre. El importador no ejecuta el modelo ni calcula SHAP.
2. El importador valida los datos y prepara `db/parcelas_modelo.sqlite` a partir
   de una copia consistente de las geometrías. La base de referencia se conserva.
3. El servidor abre esa salida usando `DB_PATH` y `READ_ONLY=1`.
4. La API entrega números almacenados; las gráficas y AI ANALYTICS los presentan.

La importación se ejecuta en tu computadora con Python 3.10 o posterior, sin
instalar paquetes Python. El servidor de producción sigue usando Node; no importa
archivos ni escribe datos durante las visitas.

## Primera importación del paquete preliminar

Desde la raíz del repositorio, coloca el ZIP en una carpeta local y ejecuta:

```bash
npm run importar:modelo -- /ruta/paquete_web_mvp.zip --dataset preliminar-v1 --sin-mediciones --validar
npm run importar:modelo -- /ruta/paquete_web_mvp.zip --dataset preliminar-v1 --sin-mediciones
DB_PATH=db/parcelas_modelo.sqlite READ_ONLY=1 DEMO_MODE=0 LLM_MODE=mock npm start
```

El paquete inicial tiene 197 resultados, 138 observados y 6,107 contribuciones.
En 138 parcelas los aportes no reconstruyen la predicción. En las otras 59 falta
confirmar la procedencia. Se conservan en SQLite con su estado, pero no se entregan
como explicación de la predicción ni se envían a AI ANALYTICS.

`--sin-mediciones` conserva los datos previos: las mediciones mensuales del ZIP
requieren corregir IDs de capa y metadatos antes de importarse. Esos datos previos
no se presentan como mediciones procedentes del nuevo archivo.

## Excel que debe entregar el equipo

Usa hojas llamadas `resultados`, `contribuciones`, `mediciones` y `capas`.
`resultados` es obligatoria; las otras son opcionales. Encabezados en la primera
fila, una fila por registro, sin títulos anteriores, celdas combinadas ni fórmulas.
Exporta fórmulas como valores. Se acepta `.xlsx`, no el formato antiguo `.xls`.
CSV usa UTF-8 y valores numéricos con punto decimal; Excel debe usar celdas numéricas.

### Hoja resultados

| Columna | Requisito |
|---|---|
| ID_POLIGONO | Obligatoria; identificador exacto, por ejemplo AGC_001 |
| version_modelo | Obligatoria; versión exportada, por ejemplo hgb-integrado-v1 |
| prediccion | Obligatoria; rendimiento >= 0, expresado en t/ha |
| observado | Puede estar vacío si no existe rendimiento medido; cero es un valor |
| unidad_rendimiento | Obligatoria; t/ha |
| tipo_prediccion | final, oof o desconocida; si falta, se guarda desconocida |
| fold_id | Identifica el entrenamiento que generó la predicción OOF |
| valor_base_t_ha | Base original del explicador, sin ajustar para forzar igualdad |
| prediccion_explicada | Salida del mismo modelo/entrada que se explicó con SHAP |
| campana | Opcional; etiqueta heredada del conjunto, no eje temporal |

Sin `campana`, el importador utiliza el identificador pasado en `--dataset`.
La API mantiene los nombres históricos `campaña` y `versión_modelo` en SQLite
para compatibilidad. La ficha muestra el conjunto, no una trayectoria anual.

Cada archivo contiene un único conjunto y versión. Para mostrar resultados del
modelo final y evaluar OOF, exporta las predicciones por separado e identifica el
tipo; no sustituyas unas por otras sin aclararlo. El importador recibe una columna
`prediccion` elegida explícitamente; no elige entre columnas OOF/final por su nombre.

### Hoja contribuciones

| Columna | Requisito |
|---|---|
| ID_POLIGONO, version_modelo, campana | Misma identidad que en resultados; campana puede omitirse |
| variable | Identidad completa de la variable y proveedor |
| valor | Valor de entrada; puede estar vacío, sin sustituirlo por cero |
| unidad | Unidad de la entrada, por ejemplo °C o mm/ciclo |
| aporte_t_ha | Contribución original de SHAP en t/ha |

El importador une por identificadores, nunca por orden de filas. Comprueba
`valor_base_t_ha + suma(aporte_t_ha) = prediccion = prediccion_explicada`, con una
tolerancia de 0.000001 t/ha. Para marcar una explicación como coherente también
exige tipo de predicción conocido y, si es OOF, `fold_id`.

Esa coherencia no valida la precisión ni la causalidad. La confirmación científica
del entrenamiento y de la evaluación sigue siendo responsabilidad del equipo.
Las explicaciones pendientes se almacenan con `shap_estado` y `shap_residuo_t_ha`.

### Hoja mediciones

| Columna | Requisito |
|---|---|
| ID_POLIGONO | Identificador exacto de la parcela |
| capa_id | ID del catálogo; distingue fuente/variable, por ejemplo s2_ndvi |
| fecha | AAAA-MM-DD o fecha Excel; puede estar vacía para un dato estático |
| variable | Variable medida, por ejemplo ndvi |
| valor | Número o vacío; el vacío se conserva como NULL |

La clave `(ID_POLIGONO, capa_id, fecha, variable)` debe ser única. Una duplicación
cancela la importación completa; no se promedian proveedores ni se sobrescriben
filas según su orden.

### Hoja capas

| Columna | Requisito |
|---|---|
| id | Único y coincidente con capa_id |
| nombre | Nombre legible, por ejemplo NDVI Sentinel-2 |
| grupo | Agrupación del selector; opcional |
| descripcion | Definición útil de la medición; opcional |
| unidad | Unidad concreta; no usar el texto genérico Variable |
| fuente | Proveedor/producto, por ejemplo Sentinel-2 |
| agregacion | Procedimiento, por ejemplo mediana mensual de píxeles válidos |

Los rangos min/max se calculan a partir de las mediciones almacenadas. Para una
serie mensual, el mes va en `fecha`; la identidad de la variable permanece en la
capa. No uses capas llamadas 2025-04 para agrupar precipitación y temperaturas.

## Sustituir el archivo

Detén el servidor que usa la salida. Mantén el mismo `--dataset` y la misma
identidad del conjunto/modelo para sustituir su instantánea:

```bash
npm run importar:modelo -- /ruta/resultados_nuevos.xlsx --dataset preliminar-v1 --sin-mediciones --validar
npm run importar:modelo -- /ruta/resultados_nuevos.xlsx --dataset preliminar-v1 --sin-mediciones
```

Cuando las mediciones estén corregidas, omite `--sin-mediciones`. Si el archivo
incluye `mediciones`, representa la instantánea completa de las mediciones de ese
conjunto: se retiran las anteriores que pertenecían a esa importación y se insertan
las nuevas. Si la hoja se omite o se usa `--sin-mediciones`, se conservan las series
previas. Los resultados del conjunto siempre se reemplazan completos: un observado
que ahora venga vacío elimina su valor anterior. No se convierte en cero.

Se conservan otros conjuntos, geometrías y datos ajenos. Los IDs sin geometría
se almacenan con aviso y no aparecen como polígonos. Las mediciones que colisionan
con datos previos u otro conjunto cancelan la importación: hay que distinguir su
capa, no sobrescribirlas silenciosamente. La salida se publica mediante reemplazo
atómico solo después de comprobar integridad; si existía, se guarda un respaldo.

La opción `--validar` comprueba el archivo sin escribir. Los conflictos con datos
ya almacenados se comprueban también durante la preparación de la copia temporal.

## Publicación y recuperación

La PR de código no modifica el SQLite de referencia ni publica el paquete real.
Para publicar datos, prepara la salida, revísala localmente y versiona el archivo
que decidas usar en producción. `parcelas_modelo.sqlite` está ignorado por defecto:
si eliges ese nombre como base publicada, agrégalo de forma explícita con
`git add -f db/parcelas_modelo.sqlite` y configura el mismo `DB_PATH` en Render.
Usa `READ_ONLY=1`, `DEMO_MODE=0` y conserva la configuración de autodespliegue.

Las columnas/tablas nuevas son aditivas y el esquema anterior se mantiene. Para
recuperar una importación anterior, detén el servidor y copia el archivo
`parcelas_modelo.sqlite.backup-*` sobre la salida. La base de geometrías inicial
siempre permanece disponible.

## Verificación local

```bash
npm run verificar:importador
node --check public/app.js
node --check server.js
git diff --check
```

El hash SHA-256 del archivo, su nombre, el conjunto/modelo y la fecha de importación
quedan registrados en `modelo_importaciones`. Cambiar el Excel actualiza los datos;
cambiar nombres de hojas/columnas requiere acordar un nuevo contrato.
