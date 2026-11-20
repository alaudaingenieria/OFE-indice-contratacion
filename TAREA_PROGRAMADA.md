# Tarea programada: actualización del índice

## Qué hace

Una tarea programada de Claude ejecuta cada noche `scripts/actualizar.py --publicar`. En cada ejecución:

1. Lee `manifest.json` de la release `datos` para saber qué hay publicado.
2. Construye la lista de pendientes, en este orden:
   - fuentes y años cerrados (2020 → año anterior) que **aún no existen**;
   - el **año en curso** (acumulado de los meses ya publicados por Hacienda);
   - años cerrados generados hace más de **120 días** (los expedientes antiguos siguen recibiendo
     actualizaciones: formalizaciones, modificaciones, anulaciones).
3. Para cada pendiente: descarga el ZIP de Hacienda, genera los tres Parquet, **publica en cuanto termina**
   (sube los ficheros y el `manifest.json` actualizado) y borra los temporales.
4. Se detiene al pasar del tiempo máximo (`--max-horas`, por defecto 5). Lo que falte se hace la noche siguiente.

Como publica periodo a periodo, si la sesión se corta no se pierde lo ya hecho.

## Cuánto tarda

El cuello de botella es la descarga desde Hacienda, que varía mucho:
- en buenas condiciones (≈5 MB/s) una noche basta para varios años;
- en noviembre de 2026 llegó a servir a ~0,1 MB/s por conexión, y entonces cabe ~1 año de una fuente por noche.

La **primera carga completa** (2020–2026, tres fuentes) puede tardar de una a varias semanas de ejecuciones
nocturnas. Después, el mantenimiento normal (año en curso + algún refresco) cabe en una ejecución.

Mientras haya periodos sin indexar, la skill lo detecta (`leer_indice.py` avisa de "periodos aún no indexados")
y para esos periodos descarga los ZIP de la Plataforma como antes.

## Requisitos

- La **aplicación de GitHub de Claude instalada** en la organización `alaudaingenieria` con acceso a este repo
  (https://github.com/apps/claude/installations/select_target), para que la tarea pueda publicar en la release.
- La tarea debe tener este repositorio como fuente, con acceso de escritura.
- Red: `contrataciondelsectorpublico.gob.es` en los dominios permitidos del entorno (Configuración > Capacidades),
  y GitHub, que suele venir permitido.
- Disco: ~10 GB libres (un ZIP anual de 2–3 GB más el siguiente en descarga).

## Calendario recomendado

- **Diaria a las 01:37 (Europe/Madrid)** durante la carga inicial.
- Cuando el manifest tenga todos los años: **mensual, el día 5 a las 01:37** (Hacienda publica el mes anterior en
  los primeros días). El refresco de años cerrados entra solo cuando toca.

## Instrucción de la tarea (prompt)

> Actualiza el índice de contratación del repositorio alaudaingenieria/ofe-indice-contratacion.
> 1. Clona el repositorio y entra en él. Instala dependencias si faltan: `pip install --break-system-packages pandas pyarrow`.
> 2. Ejecuta `python3 scripts/actualizar.py --publicar --max-horas 5` y espera a que termine (puede tardar horas:
>    lánzalo en segundo plano con nohup y revisa el log cada 10 minutos hasta que escriba "Terminado").
> 3. Si una descarga de contrataciondelsectorpublico.gob.es se rechaza por la red, o si `gh release upload` falla por
>    permisos, no reintentes en bucle: anótalo.
> 4. Al final, informa en 3–5 líneas: periodos publicados en esta ejecución, periodos que quedan pendientes según el
>    log, tamaño total del índice (suma de `bytes` en manifest.json) y cualquier error.
> No modifiques los scripts ni hagas commits en el repositorio: solo publica en la release `datos`.

## Ejecución manual

```bash
pip install --break-system-packages pandas pyarrow
python3 scripts/actualizar.py --publicar --max-horas 5                       # lo mismo que la tarea
python3 scripts/actualizar.py --publicar --periodos 2024 --fuentes perfiles   # forzar un periodo concreto
python3 scripts/actualizar.py --periodos 2025                                 # generar en dist/ sin publicar
```
