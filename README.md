# OFE · Índice de contratación pública

Índice reducido de los **datos abiertos de la Plataforma de Contratación del Sector Público (PLACSP)**,
pensado para buscar contratos y adjudicatarios en segundos, sin descargar decenas de GB de ficheros de Hacienda
cada vez.

Todos los datos proceden de los ficheros públicos de Hacienda
([Licitaciones en perfiles del contratante](https://www.hacienda.gob.es/es-ES/GobiernoAbierto/Datos%20Abiertos/Paginas/LicitacionesContratante.aspx)).
Aquí no hay información interna: el repositorio y sus datos son públicos.

## Dónde están los datos

En la carpeta **`data/`** de este repositorio. Se descargan sin cuenta:
`https://raw.githubusercontent.com/alaudaingenieria/ofe-indice-contratacion/main/data/<fichero>`

`data/manifest.json` lista los ficheros publicados, con periodo, parte, filas, tamaño, sha256, meses incluidos y
fecha de generación. Para usar el índice **no hace falta clonar el repositorio** (crecerá con el tiempo): basta con
descargar el manifest y los ficheros que se necesiten, como hace `scripts/leer_indice.py`.

### Fuentes

| `fuente` | Conjunto de la PLACSP |
|---|---|
| `perfiles` | Licitaciones de perfiles alojados en la Plataforma (sindicación 643) |
| `agregadas` | Plataformas autonómicas agregadas, sin menores (sindicación 1044) |
| `menores` | Contratos menores (sindicación 1143) |

### Periodos

- `2020` … año anterior: un periodo por año, generado a partir del ZIP anual de Hacienda.
- Año en curso: un periodo por mes (`202601`, `202602`…), según los va publicando Hacienda (primeros días del mes
  siguiente). Cuando Hacienda publica el anual, este sustituye a los meses de ese año.

Los ficheros de Hacienda recogen **los cambios registrados en cada periodo**: una formalización en 2026 de un
expediente licitado en 2023 aparece en el periodo de 2026. Por eso los periodos cerrados no se regeneran, y para
ver la situación actual de un expediente hay que cargar también los periodos posteriores.

### Ficheros (por fuente y periodo)

Cada fichero se divide en partes de menos de 45 MB (`_p01`, `_p02`…), porque GitHub no admite ficheros de más
de 100 MB. Une todas las partes de un mismo tipo, fuente y periodo.

| Fichero | Una fila por | Columnas principales |
|---|---|---|
| `licitaciones_<fuente>_<periodo>_pNN.parquet` | expediente | `id`, `updated`, `expediente`, `organo`, `organo_nif`, `organo_superior`, `titulo`, `link`, `estado`, `tipo`, `subtipo`, `procedimiento`, `urgencia`, `cpv`, `provincia`, `nuts`, `presupuesto_sin_iva`, `presupuesto_con_iva`, `valor_estimado`, `duracion`, `duracion_unidad`, `prorroga_texto`, `fecha_anuncio`, `fecha_primera_publicacion`, `n_lotes` |
| `adjudicaciones_<fuente>_<periodo>_pNN.parquet` | lote y adjudicatario | `id`, `updated`, `lote`, `titulo_lote`, `presupuesto_lote_sin_iva`, `resultado`, `fecha_adjudicacion`, `fecha_formalizacion`, `importe_adjudicado_sin_iva`, `importe_adjudicado_con_iva`, `n_ofertas`, `oferta_mas_baja`, `oferta_mas_alta`, `adjudicatario`, `nif_adjudicatario` |
| `documentos_<fuente>_<periodo>_pNN.parquet` | expediente | `id`, `updated`, `docs_pliegos` y `docs_mesa` (JSON: lista de `[nombre, url]`) |

Los códigos (`tipo`, `procedimiento`, `resultado`, `estado`, `urgencia`) son los de la especificación CODICE de la PLACSP.

### Reglas para usarlo bien

- **Un expediente aparece en varios periodos** (cada vez que se actualiza). Al unir periodos, quédate con la fila
  de `updated` más reciente por `id`; en `adjudicaciones`, con todas las filas de esa última versión.
  `scripts/leer_indice.py` ya lo hace.
- Para un rango de fechas, carga también **todos los periodos posteriores** (licitaciones y adjudicaciones son
  pequeños): ahí están los cambios posteriores de esos expedientes. `leer_indice.py` lo hace.
- **Prefijos**: para ahorrar espacio, las URLs largas van abreviadas. `scripts/leer_indice.py` (`url_completa`)
  las reconstruye:

| Prefijo | Equivale a |
|---|---|
| `D:` | `https://contrataciondelsectorpublico.gob.es/wps/wcm/connect/PLACE_es/Site/area/docAccCmpnt?srv=cmpnt&cmpntname=GetDocumentsById&source=library&DocumentIdParam=` |
| `F:` | `https://contrataciondelsectorpublico.gob.es/FileSystem/servlet/GetDocumentByIdServlet?DocumentIdParam=` |
| `L:` | `https://contrataciondelsectorpublico.gob.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl=` |
| `S:` | `https://contrataciondelsectorpublico.gob.es/sindicacion/` |

  Para descargar un documento desde un script, envía un User-Agent de navegador: la Plataforma rechaza los demás.

## Uso rápido

```python
import sys; sys.path.insert(0, 'scripts')
from leer_indice import cargar
lic, adj = cargar(fuentes=['perfiles', 'agregadas'], desde=2022, hasta=2025)
sacyr = adj[adj.adjudicatario.str.contains('SACYR', case=False, na=False)].merge(lic, on=['id', 'updated', 'fuente'], how='left')
```

## Mantenimiento

El índice se regenera con una **tarea programada** de Claude: ver [TAREA_PROGRAMADA.md](TAREA_PROGRAMADA.md).

| Script | Para qué |
|---|---|
| `scripts/actualizar.py` | Decide qué periodos faltan o están viejos, descarga, genera y publica en `data/` (commit y push por periodo) |
| `scripts/build_indice.py` | Convierte uno o varios ZIP de la PLACSP en los tres Parquet, en partes de < 45 MB |
| `scripts/leer_indice.py` | Descarga y une el índice publicado (el punto de entrada recomendado para consumirlo) |
