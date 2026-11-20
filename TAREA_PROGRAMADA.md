# Tarea programada: actualización del índice

## Qué hace

Una tarea programada de Claude ejecuta **cada semana** (sábados de madrugada) `scripts/actualizar.py --publicar`.
En cada ejecución:

1. Lee `data/manifest.json` para saber qué hay publicado.
2. Construye la lista de pendientes, de lo más antiguo a lo más reciente:
   - años cerrados (2020 → año anterior) cuyo **fichero anual aún no esté indexado**. Si Hacienda todavía no ha
     publicado el anual (enero), cubre ese año con sus meses;
   - del **año en curso**, los **meses ya publicados que no estén indexados**.
3. Para cada pendiente: descarga el ZIP de Hacienda, genera los tres Parquet en `data/`, actualiza
   `data/manifest.json` y **hace commit y push en cuanto termina ese periodo**. Después borra los temporales.
   Al indexar el anual de un año, retira los meses de ese año.
4. Se detiene al pasar del tiempo máximo (`--max-horas`, por defecto 5). Lo que falte se hace en la siguiente ejecución.
5. **Deja constancia en `registro_actualizaciones.log`** (raíz del repositorio) y la publica, haya cambios o no.

**Nada se descarga dos veces.** Los ficheros de Hacienda recogen los cambios de cada periodo (una formalización
de 2026 de un expediente de 2023 está en el fichero de 2026), así que un año o un mes cerrado no cambia. En
régimen normal cada ejecución solo tiene trabajo si Hacienda ha publicado un mes nuevo; si no, termina en un
minuto. Si Hacienda avisara de que ha corregido un fichero, se fuerza a mano:
`python3 scripts/actualizar.py --publicar --periodos 2023 --fuentes perfiles`.

Como publica periodo a periodo, si la sesión se corta no se pierde lo ya hecho.

## Cuánto tarda

El cuello de botella es la descarga desde Hacienda, que varía mucho:
- en buenas condiciones (≈5 MB/s) una noche basta para varios años;
- en noviembre de 2026 llegó a servir a ~0,1 MB/s por conexión, y entonces cabe ~1 año de una fuente por noche.

La **primera carga completa** (2020–2026, tres fuentes) puede tardar de una a varias semanas de ejecuciones
nocturnas. Después, el mantenimiento normal es un mes nuevo de cada fuente al mes.

Mientras haya periodos sin indexar, quien consuma el índice lo detecta (`leer_indice.py` avisa de "periodos aún no indexados")
y para esos periodos descarga los ZIP de la Plataforma como antes.

## Requisitos

- La **aplicación de GitHub de Claude instalada** en la organización `alaudaingenieria` con acceso a este repo
  (https://github.com/apps/claude/installations/select_target), para que la tarea pueda hacer push.
- La tarea debe tener este repositorio como fuente, con acceso de escritura.
- Las releases de GitHub no se pueden crear desde las sesiones de Claude; por eso el índice vive en `data/`.
- Red: `contrataciondelsectorpublico.gob.es` en los dominios permitidos del entorno (Configuración > Capacidades),
  y GitHub, que suele venir permitido.
- Disco: ~10 GB libres (un ZIP anual de 2–3 GB más el siguiente en descarga).

## Calendario

- **Semanal: sábados a las 01:37 (Europe/Madrid).** Hacienda publica cada mes en los primeros días del siguiente,
  así que el mes nuevo entra en el índice como mucho una semana después de publicarse.

## Registro de ejecuciones

Cada ejecución añade una línea a `registro_actualizaciones.log` y hace commit y push, **también cuando no hay nada
nuevo**. Sirve para comprobar de un vistazo que la tarea se está ejecutando cada semana: si la última línea tiene más
de 7–8 días, la tarea ha dejado de funcionar.

```
2026-11-21 01:37 | SIN CAMBIOS    | no hay periodos nuevos que indexar | índice: 149 ficheros, 1.09 GB, último mes 202610
2026-12-05 01:38 | ACTUALIZADO    | publicado: agregadas 202611, menores 202611, perfiles 202611 | índice: …
```

| Estado | Significado |
|---|---|
| `SIN CAMBIOS` | Todo estaba al día. Si Hacienda aún no ha publicado el último mes, lo indica ("Hacienda aún no ha publicado: …") |
| `ACTUALIZADO` | Se han indexado y publicado periodos nuevos (los enumera) |
| `INCOMPLETO` | Algo quedó pendiente (límite de tiempo o descarga fallida); se reintenta en la siguiente ejecución |
| `ERROR` | El script falló; la línea incluye el error |

## Instrucción de la tarea (prompt)

> Actualiza el índice de contratación del repositorio alaudaingenieria/ofe-indice-contratacion.
> 1. Trabaja en el clon del repositorio (si no está, clónalo con `git clone --depth 1`). Instala dependencias si
>    faltan: `pip install --break-system-packages pandas pyarrow`.
> 2. Ejecuta `python3 scripts/actualizar.py --publicar --max-horas 5` y espera a que termine (puede tardar horas:
>    lánzalo en segundo plano con nohup y revisa el log cada 10 minutos hasta que escriba "Terminado").
> 3. Si una descarga de contrataciondelsectorpublico.gob.es se rechaza por la red, o si el push falla por permisos,
>    no reintentes en bucle: anótalo.
> 4. Al final, informa en 3–5 líneas: periodos publicados en esta ejecución, periodos que quedan pendientes según el
>    log, tamaño total del índice (suma de `bytes` en data/manifest.json) y cualquier error.
> No modifiques los scripts: los únicos commits son los que hace actualizar.py en data/.

## Crecimiento del repositorio

Como nada se regenera, el repositorio crece poco más que el propio índice. Lo único que deja historial extra es
el relevo de los meses del año por su fichero anual (una vez al año). Quien consume el índice descarga ficheros sueltos y no necesita
clonar, así que el tamaño del historial no le afecta.

## Ejecución manual

```bash
pip install --break-system-packages pandas pyarrow
python3 scripts/actualizar.py --publicar --max-horas 5                       # lo mismo que la tarea
python3 scripts/actualizar.py --publicar --periodos 2024 --fuentes perfiles   # forzar un periodo (corrección de Hacienda)
python3 scripts/actualizar.py --periodos 2025                                 # generar en data/ sin commit
```
