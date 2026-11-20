#!/usr/bin/env python3
"""Mantiene el índice de contratación en la carpeta data/ de este repositorio.

Uso habitual (tarea programada, desde la raíz del repositorio):
  python3 scripts/actualizar.py --publicar --max-horas 5

Qué hace:
  1. Lee data/manifest.json para saber qué periodos hay y cuándo se generaron.
  2. Decide qué falta: años cerrados que no estén indexados (desde 2012 en perfiles, 2016 en agregadas y 2018 en
     menores; con sus 12 ZIP mensuales en paralelo, o el anual si no hay mensuales) y, del año en
     curso, los meses ya publicados que no estén indexados. Lo ya indexado no se vuelve a descargar.
  3. Para cada fuente y periodo pendiente: descarga el ZIP de Hacienda, genera los Parquet
     con build_indice.py en data/, actualiza data/manifest.json, borra los ZIP y, con --publicar, hace
     commit y push de ese periodo.
  4. Se detiene al superar --max-horas; lo que quede se hará en la siguiente ejecución.

Al indexar el anual de un año, se retiran los meses de ese año (el anual los contiene).

Otras opciones: --fuentes perfiles agregadas menores, --periodos 2023 202601 (fuerza esos periodos, p. ej. si
Hacienda corrige un fichero), --desde AAAA (primer año; por defecto el primero con datos de cada fuente), --hasta-mes 9, --zips-locales DIR (usa ZIP ya descargados con nombre <fuente>_<periodo>.zip; un año se puede construir con
sus 12 ZIP mensuales <fuente>_<AAAAMM>.zip).
"""
import argparse, datetime, glob, hashlib, json, os, shutil, subprocess, sys, threading, queue, time

BASE = 'https://contrataciondelsectorpublico.gob.es/sindicacion/'
FUENTES = {
    'perfiles': 'sindicacion_643/licitacionesPerfilesContratanteCompleto3_{p}.zip',
    'agregadas': 'sindicacion_1044/PlataformasAgregadasSinMenores_{p}.zip',
    'menores': 'sindicacion_1143/contratosMenoresPerfilesContratantes_{p}.zip',
}
TIPOS = ('licitaciones', 'adjudicaciones', 'documentos')
# Primer año con datos en cada fuente y primer año con ficheros mensuales (antes, solo hay fichero anual).
INICIO = {'perfiles': 2012, 'agregadas': 2016, 'menores': 2018}
MENSUAL_DESDE = {'perfiles': 2018, 'agregadas': 2018, 'menores': 2019}
AQUI = os.path.dirname(os.path.abspath(__file__)); RAIZ = os.path.abspath(os.path.join(AQUI, '..'))
DESC = os.path.join(RAIZ, 'descargas'); DATA = os.path.join(RAIZ, 'data'); MAN = os.path.join(DATA, 'manifest.json')


def log(*a): print(f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]', *a, flush=True)


def git(*args, check=True):
    return subprocess.run(['git', '-C', RAIZ, *args], capture_output=True, text=True, check=check)


def publicar(msg, rutas=('data',)):
    git('add', '-A', *rutas)
    if not git('status', '--porcelain', *rutas).stdout.strip(): return
    git('-c', 'user.name=Claude (tarea programada)', '-c', 'user.email=noreply@anthropic.com', 'commit', '-q', '-m', msg)
    for i in range(4):
        r = git('-c', 'push.negotiate=true', 'push', '-q', 'origin', 'HEAD', check=False)
        if r.returncode == 0: return
        log('push rechazado, reintento:', (r.stderr or '').strip()[-200:])
        git('pull', '-q', '--rebase', '--depth', '50', 'origin', 'main', check=False)
        time.sleep(10 * (i + 1))
    raise RuntimeError('No se pudo hacer push')


def descargar(url, dest, locales):
    if os.path.exists(dest): return True
    if locales and os.path.exists(os.path.join(locales, os.path.basename(dest))):
        shutil.copy(os.path.join(locales, os.path.basename(dest)), dest); return True
    for i in range(5):
        r = subprocess.run(['curl', '-sS', '-f', '-m', '21600', '-C', '-', '-o', dest + '.part', url])
        if r.returncode == 0 and subprocess.run(['unzip', '-tq', dest + '.part'], capture_output=True).returncode == 0:
            os.rename(dest + '.part', dest); return True
        if r.returncode == 22: break        # 404: aún no publicado
        time.sleep(30)
    subprocess.run(['rm', '-f', dest + '.part'])
    return False


def pendientes(a, man):
    """Lista de (fuente, periodo, [(url, destino)]) a procesar.

    - Años cerrados (desde el primero con datos de cada fuente hasta el año anterior) que no estén en el índice: se
      construyen con sus 12 ficheros mensuales, descargados en paralelo (mismo contenido que el anual y mucho más
      rápido: el servidor limita la velocidad por conexión y no admite descargas por trozos). Los años sin ficheros
      mensuales (perfiles y agregadas antes de 2018, menores antes de 2019) se descargan con el fichero anual.
    - Año en curso: un periodo por mes (AAAAMM), solo los meses publicados que no estén ya en el índice.
    - Un periodo ya indexado no se vuelve a descargar salvo que se pida con --periodos (p. ej. si Hacienda
      corrige un año).
    """
    hoy = datetime.date.today(); y = hoy.year
    hay = {}
    for f in man.get('ficheros', []):
        hay.setdefault((f['fuente'], f['periodo']), set()).add(f['tipo'])
    completo = lambda fu, p: hay.get((fu, p), set()) == set(TIPOS)
    zipurl = lambda fu, p: (BASE + FUENTES[fu].format(p=p), os.path.join(DESC, f'{fu}_{p}.zip'))
    # Un año cerrado se construye con sus 12 ficheros mensuales (descargados en paralelo) si existen; si no, con el anual.
    ficheros_año = lambda fu, yr: ([zipurl(fu, f'{yr}{m:02d}') for m in range(1, 13)] if yr >= MENSUAL_DESDE[fu] else [zipurl(fu, str(yr))])
    out = []
    for fu in a.fuentes:
        if a.periodos:
            out += [(fu, p, ficheros_año(fu, int(p)) if len(p) == 4 else [zipurl(fu, p)]) for p in a.periodos]
            continue
        for yr in range(max(a.desde or 0, INICIO[fu]), y):
            if not completo(fu, str(yr)):
                out.append((fu, str(yr), ficheros_año(fu, yr)))
        ult = min(hoy.month - 1, a.hasta_mes or 12)
        for m in range(1, ult + 1):
            p = f'{y}{m:02d}'
            if not completo(fu, p): out.append((fu, p, [zipurl(fu, p)]))
    out.sort(key=lambda t: (t[1][:4], len(t[1]), t[1], t[0]))
    return out


def retirar(man, f, periodos):
    """Quita del índice (ficheros y manifest) los periodos indicados de una fuente."""
    for p in periodos:
        for fp in glob.glob(os.path.join(DATA, f'*_{f}_{p}_p*.parquet')): os.remove(fp)
    man['ficheros'] = [x for x in man.get('ficheros', []) if not (x['fuente'] == f and x['periodo'] in periodos)]


REGISTRO = os.path.join(RAIZ, 'registro_actualizaciones.log')


def registrar(estado, detalle, publicar_git):
    """Añade una línea al registro de ejecuciones (y la publica) para comprobar que la tarea se ejecuta."""
    man = json.load(open(MAN)) if os.path.exists(MAN) else {'ficheros': []}
    tam = sum(f.get('bytes', 0) for f in man['ficheros']) / 1e9
    ult = max((f['periodo'] for f in man['ficheros'] if len(f['periodo']) == 6), default='-')
    from zoneinfo import ZoneInfo
    ahora = datetime.datetime.now(ZoneInfo('Europe/Madrid'))
    linea = (f"{ahora:%Y-%m-%d %H:%M} | {estado:<14} | {detalle} | "
             f"índice: {len(man['ficheros'])} ficheros, {tam:.2f} GB, último mes {ult}")
    nuevo = not os.path.exists(REGISTRO)
    with open(REGISTRO, 'a', encoding='utf-8') as fh:
        if nuevo:
            fh.write('# Registro de ejecuciones de scripts/actualizar.py (una línea por ejecución)\n'
                     '# fecha (hora de Madrid) | estado (SIN CAMBIOS / ACTUALIZADO / INCOMPLETO / ERROR) | detalle | estado del índice\n')
        fh.write(linea + '\n')
    log('Registro:', linea)
    if publicar_git:
        try:
            publicar(f'Registro: {estado.lower()}', rutas=('registro_actualizaciones.log',))
        except Exception as e:
            log('No se pudo publicar el registro:', e)


def main():
    try:
        _main()
    except Exception as e:
        a = sys.argv
        registrar('ERROR', f'{type(e).__name__}: {str(e)[:200]}', '--publicar' in a)
        raise


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fuentes', nargs='+', default=list(FUENTES)); ap.add_argument('--periodos', nargs='+')
    ap.add_argument('--desde', type=int, default=None, help='primer año a indexar (por defecto, el primero con datos de cada fuente)')
    ap.add_argument('--max-horas', type=float, default=5); ap.add_argument('--publicar', action='store_true')
    ap.add_argument('--zips-locales'); ap.add_argument('--hasta-mes', type=int, help='año en curso: último mes a incluir')
    a = ap.parse_args()
    t0 = time.time()
    os.makedirs(DESC, exist_ok=True); os.makedirs(DATA, exist_ok=True)
    man = json.load(open(MAN)) if os.path.exists(MAN) else {'ficheros': []}
    tr = pendientes(a, man)
    log(f'{len(tr)} periodos pendientes:', ', '.join(f'{f} {p}' for f, p, _ in tr) or 'ninguno')
    pend = tr
    q = queue.Queue(maxsize=1); stop = threading.Event()

    def productor():
        i = 0
        while i < len(pend) and not stop.is_set():
            f, p, files = pend[i]; i += 1
            meses_loc = [os.path.join(a.zips_locales or '', f'{f}_{p}{m:02d}.zip') for m in range(1, 13)]
            if len(p) == 4 and a.zips_locales and all(os.path.exists(x) for x in meses_loc):
                q.put((f, p, meses_loc, 12)); continue   # año completo a partir de sus 12 meses ya descargados
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(12) as ex:
                res = list(ex.map(lambda t: descargar(t[0], t[1], a.zips_locales), files))
            ok = [d for (u, d), r in zip(files, res) if r]
            if len(p) == 4 and len(ok) < len(files):   # año incompleto: no se publica a medias
                log(f'{f} {p}: solo {len(ok)}/{len(files)} meses descargados; se deja para la próxima ejecución')
                for d in ok: os.remove(d)
                ok = []
            q.put((f, p, ok, len(files)))
            if i == len(pend): q.join()   # da tiempo a que main() añada meses si un anual no existe
        q.put(None)
    threading.Thread(target=productor, daemon=True).start()
    hechos = []; vacios = set()
    import pandas as pd
    while True:
        it = q.get()
        try:
            if it is None: break
            f, p, ok, n = it
            if not ok: vacios.add(f'{f} {p}')
            log(f'{f} {p}: {len(ok)}/{n} ficheros descargados')
            if not ok:
                continue
            r = subprocess.run([sys.executable, os.path.join(AQUI, 'build_indice.py'), '--fuente', f, '--periodo', p, '--zips', *ok, '--out', DATA])
            if r.returncode != 0:
                log(f'ERROR procesando {f} {p}'); continue
            for z in ok:
                if z.startswith(DESC): os.remove(z)
            man['ficheros'] = [x for x in man.get('ficheros', []) if not (x['fuente'] == f and x['periodo'] == p)]
            if len(p) == 4:   # el anual sustituye a los meses de ese año
                meses = sorted({x['periodo'] for x in man['ficheros'] if x['fuente'] == f and x['periodo'][:4] == p and len(x['periodo']) == 6})
                if meses: retirar(man, f, meses); log(f'{f} {p}: retirados los meses {meses[0]}–{meses[-1]}')
            for fp in sorted(glob.glob(os.path.join(DATA, f'*_{f}_{p}_p*.parquet'))):
                nm = os.path.basename(fp)
                man['ficheros'].append({'fichero': nm, 'tipo': nm.split('_')[0], 'fuente': f, 'periodo': p,
                                        'parte': int(nm.rsplit('_p', 1)[1][:-8]),
                                        'filas': pd.read_parquet(fp, columns=['id']).shape[0], 'bytes': os.path.getsize(fp),
                                        'sha256': hashlib.sha256(open(fp, 'rb').read()).hexdigest(),
                                        'generado': datetime.date.today().isoformat()})
            man['generado'] = datetime.datetime.now().isoformat(timespec='seconds')
            man['ficheros'].sort(key=lambda x: (x['fuente'], x['periodo'], x['tipo'], x.get('parte', 1)))
            json.dump(man, open(MAN, 'w'), indent=1, ensure_ascii=False)
            if a.publicar:
                publicar(f'Índice: {f} {p}'); log(f'publicado {f} {p}')
            hechos.append(f'{f} {p}')
            if (time.time() - t0) / 3600 > a.max_horas:
                log(f'Límite de {a.max_horas} h alcanzado; el resto queda para la próxima ejecución.'); stop.set(); break
        finally:
            q.task_done()
    restantes = [f'{f} {p}' for f, p, _ in pend if f'{f} {p}' not in hechos]
    y = str(datetime.date.today().year)
    sin_publicar = [r for r in restantes if r in vacios and len(r.split()[1]) == 6 and r.split()[1][:4] == y]   # meses que Hacienda aún no ha publicado
    restantes = [r for r in restantes if r not in sin_publicar]
    nota = f' · Hacienda aún no ha publicado: {", ".join(sin_publicar)}' if sin_publicar else ''
    if hechos and not restantes:
        registrar('ACTUALIZADO', 'publicado: ' + ', '.join(hechos) + nota, a.publicar)
    elif hechos:
        registrar('INCOMPLETO', 'publicado: ' + ', '.join(hechos) + ' · pendiente: ' + ', '.join(restantes), a.publicar)
    elif restantes:
        registrar('INCOMPLETO', 'nada publicado · pendiente (no disponible o con error): ' + ', '.join(restantes), a.publicar)
    else:
        registrar('SIN CAMBIOS', 'no hay periodos nuevos que indexar' + nota, a.publicar)
    log('Terminado. Hechos:', ', '.join(hechos) or 'ninguno')


if __name__ == '__main__':
    main()
