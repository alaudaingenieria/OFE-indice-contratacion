---
name: OFE-analisis-contratacion-empresa
description: Analiza todas las licitaciones y contratos de una empresa (directamente o en UTE) en la Plataforma de Contratación del Sector Público y genera un informe HTML autónomo para compartir. Úsala cuando pidan qué, cuánto, cuándo y con quién ha contratado una empresa con la Administración.
---

# Contratación pública de una empresa (OFE)

**Idioma: usa castellano en toda la interacción** (preguntas, avisos, resúmenes y textos del informe), salvo que el
usuario pida expresamente otro idioma.

Objetivo: reunir todo lo que una empresa (o una UTE en la que participa) ha **ganado** y, si se pide, las
licitaciones a las que **se presentó sin ganar**, en uno o varios órganos de contratación. El resultado es un
**HTML autónomo** (un solo fichero que se abre sin conexión y se envía por correo) más un CSV.

El informe distingue tres relaciones: *Adjudicataria*, *Adjudicataria en UTE* y *Licitadora (no adjudicataria)*.
Cada fila tiene una casilla para excluirla, porque la identificación por nombre puede fallar.

Los datos salen del **índice OFE**: los datos abiertos de la Plataforma ya procesados y publicados en el repositorio
público `alaudaingenieria/ofe-indice-contratacion` (carpeta `data/`). Una tarea programada lo mantiene al día cada
noche. Para los periodos que el índice aún no cubra se recurre a los ficheros de Hacienda.

## 0. Preparación

Descarga los scripts de la skill desde el repositorio (no hace falta cuenta) e instala dependencias:

```bash
mkdir -p ofe && cd ofe
B=https://raw.githubusercontent.com/alaudaingenieria/ofe-indice-contratacion/main/skill
for f in buscar_empresa.py leer_indice.py placsp_empresa.py placsp_participacion.py build_informe.py informe_template.html; do
  curl -sSLf -O "$B/$f" || echo "FALTA $f"
done
pip install --break-system-packages -q pandas pyarrow requests
which pdftotext || echo "falta pdftotext (poppler-utils): solo se necesita para la participación"
```

Trabaja dentro de esa carpeta. Si GitHub no responde, díselo al usuario y no improvises otra fuente.

## 1. Entrevista inicial

Pregunta con AskUserQuestion, en una o dos tandas. No empieces sin estas respuestas:

1. **Empresa**: razón social, **CIF/NIF** (y de filiales si se quieren incluir) y **variantes del nombre**, por
   ejemplo "SACYR", "SACYR CONSTRUCCION", "SACYR CONSERVACION". Avisa de que una variante muy corta o genérica
   trae homónimos.
2. **Rango de fechas** (de adjudicación). Por defecto, los últimos 3 años cerrados más el año en curso.
3. **Fuentes** (multiselección):
   - Perfiles alojados en la Plataforma (AGE, entidades estatales, muchas locales). Siempre.
   - Plataformas autonómicas agregadas (Madrid, Cataluña, Andalucía, Euskadi, Galicia, Navarra, La Rioja…).
   - Contratos menores. Muchos más registros e importes pequeños; pregunta si interesan.
4. **Órganos de contratación** (opcional): texto o regex (p. ej. "Carreteras|ADIF|Aena"). Vacío = todos.
5. **Palabras significativas** (opcional): incluir y excluir sobre el título (regex, sin distinguir mayúsculas).
6. **Participación sin adjudicación**: ¿también las licitaciones a las que se presentó sin ganar? Explica que exige
   leer actas e informes de la mesa (unos 2 s por licitación). Por eso conviene acotarlo por órgano o palabras
   clave, y se revisa como máximo el número de licitaciones que el usuario acepte (por defecto 400).

Si el usuario ya dio algo en su mensaje, no lo vuelvas a preguntar.

## 2. Búsqueda en el índice

```bash
python3 buscar_empresa.py --nif A12345678 --nombre "SACYR" --nombre "SACYR CONSERVACION" \
  --desde 2023-01-01 --hasta 2026-10-31 --fuentes perfiles agregadas menores \
  [--organo "Carreteras|ADIF"] [--kw "firme|conservaci"] [--kw-excluir "limpieza"] [--participacion] \
  --out resultado.json
```

- Descarga del índice solo lo necesario: licitaciones y adjudicaciones desde el año inicial en adelante (los
  cambios posteriores de cada expediente están en periodos más recientes) y, con `--participacion`, los enlaces
  a documentos del rango. Tarda de 30 s a 3 min según rango y fuentes.
- Cada fila de `adjudicaciones` es **un lote adjudicado**: adjudicatario y NIF, fechas de adjudicación y
  formalización, importes con y sin IVA, nº de ofertas, presupuesto del lote, órgano y enlace a la Plataforma.
- `relacion` es `Adjudicataria en UTE` si el NIF empieza por U o el nombre contiene UTE o "unión temporal".
  `coincidencia` (NIF o Nombre) ayuda a revisar las dudosas.
- **Revisa con el usuario los adjudicatarios distintos encontrados** antes de seguir: lista los nombres únicos
  con su nº de filas e importe y pregunta si alguno no es la empresa (homónimos). Quita esas variantes o añade
  `--kw-excluir` y repite.
- La salida indica `indice_generado` (fecha del índice). Menciónala en el resumen.

## 3. Periodos que el índice aún no cubre

Si `periodos_sin_indice` no está vacío (p. ej. mientras la tarea programada completa la carga inicial), avisa al
usuario y, para esos años y fuentes, usa los ficheros de Hacienda. Descarga los **12 ZIP mensuales** de cada año
en paralelo (el servidor limita la velocidad por conexión), por ejemplo con `xargs -P 12`:

| Fuente | URL (`AAAAMM`) |
|---|---|
| perfiles | `https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3_AAAAMM.zip` |
| agregadas | `https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/PlataformasAgregadasSinMenores_AAAAMM.zip` |
| menores | `https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1143/contratosMenoresPerfilesContratantes_AAAAMM.zip` |

La red debe permitir `contrataciondelsectorpublico.gob.es`. Si se rechaza, pide al usuario que lo añada a los
dominios permitidos (Configuración > Capacidades) y espera. Después:

```bash
python3 placsp_empresa.py --zips "Perfiles PLACSP=zips/perfiles_*.zip" "Agregadas autonómicas=zips/agregadas_*.zip" \
  --nif A12345678 --nombre "SACYR" --desde 2019-01-01 --hasta 2019-12-31 [mismos filtros] --out resultado_zip.json
python3 - <<'EOF'
import json
a, b = json.load(open('resultado.json')), json.load(open('resultado_zip.json'))
k = lambda r: (r['id'], r.get('lote'), r.get('adjudicatario'))
vistos = {k(r) for r in a['adjudicaciones']}
a['adjudicaciones'] += [r for r in b['adjudicaciones'] if k(r) not in vistos]
ids = {r['id'] for r in a['candidatas_participacion']}
a['candidatas_participacion'] += [r for r in b['candidatas_participacion'] if r['id'] not in ids]
json.dump(a, open('resultado.json', 'w'), ensure_ascii=False)
EOF
```

## 4. Participación sin adjudicación (si se pidió)

`resultado.json` trae `candidatas_participacion`: licitaciones del rango, dentro de los filtros, ya resueltas, que
no ganó la empresa y con documentos de la mesa (actas, informes de valoración, adjudicación, aperturas).
Cuéntalas; si son más de las aceptadas, propón acotar por órgano o palabras, o revisar solo las N más recientes.

```bash
python3 placsp_participacion.py --in resultado.json --nif A12345678 --nombre "SACYR" --max 400 --out participacion.json
```

Cada participación lleva `evidencia_documento`, `evidencia_url` y un `evidencia_extracto` con el texto donde
aparece la empresa (en un acta suele ser la tabla de puntuaciones). Algunas actas cubren varios expedientes o
citan a la empresa por otro motivo: el extracto sirve para descartarlas desde el informe.

## 5. Informe HTML autónomo

```bash
python3 build_informe.py --adj resultado.json [--part participacion.json] --empresa "SACYR (grupo)" \
  --nif A12345678 --nombre SACYR --nombre "SACYR CONSERVACION" \
  --criterios "Adjudicaciones 2023–oct 2026; perfiles + agregadas + menores; órganos: todos; índice del 2026-11-20" \
  --out Informe_SACYR.html
```

Genera `Informe_<empresa>.html` y `Informe_<empresa>.csv` a partir de `informe_template.html` (marcadores
`__TITLE__` y `__DATA__`; para cambiar el diseño se edita solo la plantilla del repositorio). El HTML no usa
librerías externas e incluye:

- **Cabecera**: empresa, NIF, variantes, criterios y fecha. Pon en `--criterios` todo lo que acote el resultado
  (fechas, fuentes, órganos, palabras, nº de licitaciones revisadas para participación, fecha del índice): sin eso
  la tasa de éxito engaña.
- **Filtros**: relación (directa, en UTE o sin ganar), fechas, órgano, tipo, fuente y búsqueda.
- **Indicadores**: adjudicaciones (lotes y expedientes), importe adjudicado, parte en UTE, licitaciones sin ganar,
  tasa de éxito, baja media y nº de órganos.
- **Gráficos**: importe por año (directo y en UTE, con las participaciones sin ganar), órganos principales (pulsar
  filtra), tipo de contrato, socios de UTE (pulsar busca) y tasa de éxito por órgano.
- **Tabla** con casilla ✓ por fila: desmarcarla excluye la fila de todo el informe. Las exclusiones se guardan en el
  navegador, un botón las reactiva todas de golpe y se pueden exportar e importar en JSON para compartirlas.
  También exporta a CSV las filas incluidas, muestra la evidencia de cada participación y enlaza a la Plataforma.

Entrega con SendUserFile el HTML y el CSV. Resume en castellano, en 4–6 líneas: importe total, años, órganos
principales, peso de las UTE y socios habituales, participaciones y tasa de éxito si se buscaron, fecha del índice
y las limitaciones que apliquen.

## Limitaciones que hay que decir cuando apliquen

- Los datos abiertos solo identifican al **adjudicatario**. La participación sin ganar sale de leer documentos de la
  mesa, así que es incompleta: hay órganos que no los publican, PDF escaneados y licitaciones fuera del filtro.
- Por **nombre** puede haber homónimos o faltar variantes (antiguas razones sociales, abreviaturas en las UTE). Por
  **NIF** es exacto, pero las UTE tienen NIF propio.
- Algunas entidades publican pliegos y actas en su propia plataforma (p. ej. parte de Aena, procedimiento "Otros").
  Ahí solo constan las adjudicaciones.
- Importes sin IVA. La baja solo se calcula si hay presupuesto del lote o el expediente tiene un solo lote.
- El índice va con hasta un mes de retraso: Hacienda publica cada mes en los primeros días del siguiente.
- Las plataformas autonómicas que no se agregan a la Plataforma del Estado no están incluidas. Si el usuario
  necesita una concreta, búscala con WebSearch y dile si tiene datos abiertos descargables.

## Mantenimiento

Scripts, plantilla e índice viven en https://github.com/alaudaingenieria/ofe-indice-contratacion (carpetas
`skill/`, `scripts/` y `data/`). El índice lo actualiza la tarea programada descrita en `TAREA_PROGRAMADA.md`.
Para cambiar el informe o la búsqueda, se modifica el repositorio; este SKILL.md solo cambia si cambia el
procedimiento.
