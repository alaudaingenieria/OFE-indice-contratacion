#!/usr/bin/env python3
"""Mantiene el índice de contratación en la carpeta data/ de este repositorio.

Uso habitual (tarea programada, desde la raíz del repositorio):
  python3 scripts/actualizar.py --publicar --max-horas 5

Qué hace:
  1. Lee data/manifest.json para saber qué periodos hay y cuándo se generaron.
  2. Decide qué falta: años cerrados (2020..año anterior) que no estén, años cerrados con más de
     --refresco-dias de antigüedad y, siempre, el año en curso (meses publicados hasta el mes anterior).
  3. Para cada fuente y periodo pendiente: descarga el ZIP (o los mensuales) de Hacienda, genera los Parquet
     con build_indice.py en data/, actualiza data/manifest.json, borra los ZIP y, con --publicar, hace
     commit y push de ese periodo.
  4. Se detiene al superar --max-horas; lo que quede se hará en la siguiente ejecución.

Otras opciones: --fuentes perfiles agregadas menores, --periodos 2023 2024 (fuerza esos periodos),
--desde 2020, --refresco-dias 120, --hasta-mes 9, --zips-locales DIR (usa ZIP ya descargados con nombre <fuente>_<periodo>.zip).
"""
import argparse, datetime, glob, hashlib, json, os, shutil, subprocess, sys, threading, queue, time

BASE = 'https://contrataciondelsectorpublico.gob.es/sindicacion/'
FUENTES = {
    'perfiles': 'sindicacion_643/licitacionesPerfilesContratanteCompleto3_{p}.zip',
    'agregadas': 'sindicacion_1044/PlataformasAgregadasSinMenores_{p}.zip',
    'menores': 'sindicacion_1143/contratosMenoresPerfilesContratantes_{p}.zip',
}
TIPOS = ('licitaciones', 'adjudicaciones', 'documentos')
AQUI = os.path.dirname(os.path.abspath(__file__)); RAIZ = os.path.abspath(os.path.join(AQUI, '..'))
DESC = os.path.join(RAIZ, 'descargas'); DATA = os.path.join(RAIZ, 'data'); MAN = os.path.join(DATA, 'manifest.json')


def log(*a): print(f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]', *a, flush=True)


def git(*args, check=True):
    return subprocess.run(['git', '-C', RAIZ, *args], capture_output=True, text=True, check=check)


def publicar(msg):
    git('add', '-A', 'data')
    if not git('status', '--porcelain', 'data').stdout.strip(): return
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
    hoy = datetime.date.today(); y = hoy.year
    hay, fecha = {}, {}
    for f in man.get('ficheros', []):
        hay.setdefault((f['fuente'], f['periodo']), set()).add(f['tipo'])
        fecha[(f['fuente'], f['periodo'])] = f.get('generado', '')[:10]
    out = []
    for fu in a.fuentes:
        if a.periodos:
            pers = a.periodos
        else:
            pers = []
            for p in map(str, range(a.desde, y)):
                fe = fecha.get((fu, p), '')
                viejo = not fe or (hoy - datetime.date.fromisoformat(fe)).days > a.refresco_dias
                if hay.get((fu, p), set()) != set(TIPOS) or viejo: pers.append(p)
            pers.append(str(y))
        for p in pers:
            if p == str(y):
                meses = [f'{y}{m:02d}' for m in range(1, min(hoy.month, (a.hasta_mes or 12) + 1))]
                out.append((fu, p, [(BASE + FUENTES[fu].format(p=m), os.path.join(DESC, f'{fu}_{m}.zip')) for m in meses]))
            else:
                out.append((fu, p, [(BASE + FUENTES[fu].format(p=p), os.path.join(DESC, f'{fu}_{p}.zip'))]))
    # primero lo que no existe, después el año en curso y los refrescos
    out.sort(key=lambda t: (set(TIPOS) <= hay.get((t[0], t[1]), set()), t[1] == str(y), t[1], t[0]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fuentes', nargs='+', default=list(FUENTES)); ap.add_argument('--periodos', nargs='+')
    ap.add_argument('--desde', type=int, default=2020); ap.add_argument('--refresco-dias', type=int, default=120)
    ap.add_argument('--max-horas', type=float, default=5); ap.add_argument('--publicar', action='store_true')
    ap.add_argument('--zips-locales'); ap.add_argument('--hasta-mes', type=int, help='año en curso: último mes a incluir')
    a = ap.parse_args()
    t0 = time.time()
    os.makedirs(DESC, exist_ok=True); os.makedirs(DATA, exist_ok=True)
    man = json.load(open(MAN)) if os.path.exists(MAN) else {'ficheros': []}
    tr = pendientes(a, man)
    log(f'{len(tr)} periodos pendientes:', ', '.join(f'{f} {p}' for f, p, _ in tr))
    q = queue.Queue(maxsize=1); stop = threading.Event()

    def productor():
        for f, p, files in tr:
            if stop.is_set(): break
            ok = [dest for url, dest in files if descargar(url, dest, a.zips_locales)]
            q.put((f, p, ok, len(files)))
        q.put(None)
    threading.Thread(target=productor, daemon=True).start()
    hechos = []
    import pandas as pd
    while True:
        it = q.get()
        if it is None: break
        f, p, ok, n = it
        log(f'{f} {p}: {len(ok)}/{n} ficheros descargados')
        if not ok: continue
        r = subprocess.run([sys.executable, os.path.join(AQUI, 'build_indice.py'), '--fuente', f, '--periodo', p, '--zips', *ok, '--out', DATA])
        if r.returncode != 0:
            log(f'ERROR procesando {f} {p}'); continue
        for z in ok: os.remove(z)
        man['ficheros'] = [x for x in man.get('ficheros', []) if not (x['fuente'] == f and x['periodo'] == p)]
        for fp in sorted(glob.glob(os.path.join(DATA, f'*_{f}_{p}_p*.parquet'))):
            nm = os.path.basename(fp)
            man['ficheros'].append({'fichero': nm, 'tipo': nm.split('_')[0], 'fuente': f, 'periodo': p,
                                    'parte': int(nm.rsplit('_p', 1)[1][:-8]),
                                    'meses': len(ok) if p == str(datetime.date.today().year) else 12,
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
    log('Terminado. Hechos:', ', '.join(hechos) or 'ninguno')


if __name__ == '__main__':
    main()
