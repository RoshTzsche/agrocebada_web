# AgroCebada · Web de mapas (Node + Express + SQLite + Leaflet)

## Resultados reales del modelo

El dashboard muestra rendimiento cerrado por parcela: barras de predicción y
observado, errores y aportes SHAP coherentes. El botón Resultados colorea el mapa
por el conjunto seleccionado. Las mediciones conservan su selector de fechas.

Para importar y sustituir un Excel o ZIP sin cambiar las gráficas, sigue
[el contrato y los comandos de importación](docs/IMPORTAR_MODELO.md).

    npm run importar:modelo -- /ruta/archivo.xlsx --dataset preliminar-v1 --sin-mediciones
    DB_PATH=db/parcelas_modelo.sqlite READ_ONLY=1 DEMO_MODE=0 LLM_MODE=mock npm start

La importación requiere Python 3.10+ y no instala paquetes. Prepara una copia de
SQLite, conserva la base de geometrías y registra procedencia y estado de SHAP.

## Local
    npm install
    npm run seed:demo   # opcional: datos ALEATORIOS de prueba (capas NIR y NDVI)
    npm start           # http://localhost:3000

## API
- GET  /api/parcelas              GeoJSON de los 197 polígonos
- GET  /api/parcelas/:id          ficha por ID_POLIGONO (funciona aunque no tenga geometría)
- GET  /api/capas                 catálogo de capas temáticas
- GET  /api/capas/:id/fechas      fechas disponibles
- GET  /api/capas/:id/valores?fecha=YYYY-MM-DD[&variable=x]   { ID_POLIGONO: valor }
- POST /api/datos                 ingesta [{ID_POLIGONO, capa_id, fecha, variable, valor}] (header x-api-key si defines API_KEY)

## Modelo de datos
`parcelas` (original) + `capas` + `datos`. `datos` NO tiene FK a `parcelas`: un ID_POLIGONO puede tener datos sin geometría.

## Deploy
Variables: PORT, DB_PATH, API_KEY.
SQLite vive en un archivo: en Render/Railway/Fly monta un volumen persistente y apunta DB_PATH ahí
(el Dockerfile usa /data). Sin volumen, los POST /api/datos se pierden en cada redeploy.
    docker build -t agrocebada . && docker run -p 3000:3000 -v agc:/data agrocebada

## Cargar datos nuevos (CSV)
    node scripts/importar_csv.js datos.csv --grupo NIR --fecha 2026-02-01
    node scripts/importar_csv.js datos.csv --grupo Procesamiento --fecha-col fecha
El CSV debe tener la columna ID_POLIGONO; cada otra columna numérica se vuelve una capa (usa nombres simples, sin espacios).
Reimportar el mismo archivo reemplaza los valores, no los duplica.
Respalda siempre `db/parcelas_master.sqlite` antes de importar.

## Deploy de solo lectura (recomendado)
1. En tu laptop: importa CSVs, apaga el servidor y corre `npm run preparar`.
2. Sube el proyecto a GitHub (incluyendo db/parcelas_master.sqlite).
3. Render → New Web Service → Docker. El Dockerfile ya activa READ_ONLY=1.

