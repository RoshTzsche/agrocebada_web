// Configuración del front. Para capas raster (NIR/satelital propias, p. ej. de un servidor de tiles)
// agrega entradas aquí: { nombre, url: 'https://.../{z}/{x}/{y}.png', attribution }
window.CONFIG = {
  baseInicial: 'sat', // 'sat' = satelital; 'calles' = OpenStreetMap.
  centro: [19.75, -98.4], zoom: 10,
  // Región de navegación aproximada, no límites administrativos.
  // Leaflet usa [latitud, longitud]: esquina suroeste y esquina noreste.
  // Edita estas esquinas y recarga el navegador para probar. Usa null para explorar sin límite.
  limites: [[18.85, -98.85], [20.04, -97.55]],
  bases: {
    calles: { url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', attribution: '© OpenStreetMap' },
    sat: { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attribution: 'Esri, Maxar, Earthstar Geographics' }
  },
  rasters: [] // ej: [{ nombre: 'NIR mensual', url: '...', attribution: '...' }]
};

