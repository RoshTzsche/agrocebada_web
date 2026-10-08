# Probar el mapa y ajustar sus límites

La configuración está en `public/config.js`. El fondo inicial es satelital;
el panel Capas permite cambiar a Calles durante la sesión. Recargar vuelve a
aplicar `baseInicial`.

## Traer esta rama a tu máquina

Desde la carpeta de tu repositorio:

```sh
git status
git fetch origin
git switch --track origin/codex/mapa-satelital-config-20261008
```

`fetch` actualiza las referencias de GitHub sin cambiar los archivos abiertos.
`switch` carga los archivos de la rama elegida. Si la rama local ya existe, usa
`git switch codex/mapa-satelital-config-20261008` y después
`git pull --ff-only origin codex/mapa-satelital-config-20261008`.

Si `git status` muestra trabajo sin guardar, consérvalo antes de cambiar de rama:
haz un commit en tu rama actual, o usa
`git stash push -u -m "Trabajo antes de probar el mapa"`. El stash guarda cambios
de archivos versionados y archivos nuevos sin ignorar; no guarda `.env` ni las
bases ignoradas. Para recuperar ese trabajo, vuelve a tu rama y aplica el stash
correspondiente con `git stash apply`.

## Arrancar localmente

Necesitas Node 22 o superior. Desde la carpeta del proyecto:

```sh
node --version
npm ci
env DB_PATH=db/parcelas_master.sqlite READ_ONLY=1 LLM_MODE=mock PORT=3000 npm run dev
```

Abre http://localhost:3000. Esta base permite probar geometrías, navegación,
búsqueda y selección. Si no contiene resultados del modelo, la ficha lo avisa;
eso no impide probar el mapa. Si ya importaste tus resultados, cambia `DB_PATH`
por `db/parcelas_modelo.sqlite`. Para importar el ZIP o Excel, consulta
[IMPORTAR_MODELO.md](IMPORTAR_MODELO.md).

`npm run dev` vigila cambios del servidor. Para ver cambios de HTML, CSS y JS
del mapa debes recargar el navegador; no hay recarga automática de la página.
Detén el servidor con Ctrl+C antes de cambiar de rama o importar datos.

## Ajustar las coordenadas

```js
baseInicial: 'sat',
limites: [[18.85, -98.85], [20.04, -97.55]],
```

`limites` es un rectángulo, no una frontera administrativa. Leaflet recibe
**[latitud, longitud]**, a diferencia de las coordenadas de GeoJSON.

| Posición | Significado | Valor actual |
| --- | --- | --- |
| Primera esquina, primer número | Sur: latitud mínima | 18.85 |
| Primera esquina, segundo número | Oeste: longitud mínima | -98.85 |
| Segunda esquina, primer número | Norte: latitud máxima | 20.04 |
| Segunda esquina, segundo número | Este: longitud máxima | -97.55 |

Para ampliar el área: reduce Sur y Oeste, o aumenta Norte y Este. Con longitudes
negativas, `-99` queda más al oeste que `-98`. Mantén Sur < Norte y Oeste < Este.
Para explorar libremente de forma temporal, usa `limites: null`.

La base de referencia actual contiene 197 parcelas. Revisando todos sus 6,431
vértices, se obtuvieron estos extremos:

| Coordenada | Mínima | Máxima |
| --- | --- | --- |
| Latitud | 19.523825 | 19.9843652 |
| Longitud | -98.612993 | -98.1966995097009 |

El rectángulo actual contiene todas las parcelas. Conserva esos extremos con un
margen si quieres que sigan siendo accesibles. Si cambian las geometrías, vuelve
a comprobar sus vértices; los puntos de referencia no bastan.

Los límites restringen navegación. No recortan ni ocultan la imagen exterior.
El código ajusta el zoom mínimo al tamaño visible del mapa, por lo que encoger
el rectángulo puede forzar un acercamiento mayor. Al cargar las parcelas,
`fitBounds` encuadra sus geometrías automáticamente: `centro` y `zoom` son la
vista inicial de respaldo, no el encuadre definitivo cuando hay parcelas.

## Probar antes de integrar

1. Cambia una coordenada en `public/config.js` y guarda.
2. Recarga el navegador; usa Ctrl+Shift+R si ves una versión anterior.
3. Comprueba el fondo satelital y cambia a Calles y de vuelta a Satelital.
4. Arrastra hacia los cuatro bordes y aleja el zoom; revisa que el límite sea el
   esperado.
5. Busca y selecciona parcelas cercanas a los extremos. Abre su ficha y prueba
   la navegación desde la tabla Parcelas.
6. Prueba una ventana ancha y una pantalla móvil; su zoom mínimo puede diferir.

En otra terminal, desde el mismo repositorio:

```sh
node --check public/config.js
node --check public/app.js
git diff --check
git diff -- public/config.js
```

## Guardar tus ajustes y revisar el merge

```sh
git add public/config.js
git commit -m "Ajustar límites de navegación del mapa"
git push origin codex/mapa-satelital-config-20261008
git fetch origin
git diff --stat origin/main...HEAD
git diff origin/main...HEAD -- public/config.js public/app.js public/index.html
```

El commit guarda tus ajustes localmente; el push actualiza la misma rama y su PR.
Revisa la PR y sus checks. Cuando las pruebas locales te convenzan, fusiona la
PR en GitHub. El merge a `main` puede activar el autodespliegue de Render según
la configuración existente; ejecutar `npm run dev` solo arranca en tu máquina.

Después del merge, detén el servidor y actualiza tu copia de `main`:

```sh
git switch main
git pull --ff-only origin main
env DB_PATH=db/parcelas_master.sqlite READ_ONLY=1 LLM_MODE=mock PORT=3000 npm run dev
```

`--ff-only` evita crear un merge local inesperado. Si Git informa que las ramas
han divergido, inspecciona el historial antes de resolverlo; no descartes cambios.
Para otro ajuste, crea una rama nueva desde ese `main` actualizado.
