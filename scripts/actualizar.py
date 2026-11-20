#!/usr/bin/env python3
"""Mantiene el índice de contratación publicado como release "datos" del repositorio.

Uso habitual (tarea programada):
  python3 scripts/actualizar.py --publicar --max-horas 5

Qué hace:
  1. Lee el manifest.json publicado (si existe) para saber qué periodos hay y cuándo se generaron.
  2. Decide qué falta: años cerrados (2020..año anterior) que no estén publicados, años cerrados con más
     de --refresco-dias de antigüedad y, siempre, el año en curso (meses publicados hasta el mes anterior).
  3. Para cada fuente y periodo pendiente: descarga el ZIP (o los mensuales), genera los tres Parquet con
     build_indice.py, borra los ZIP y, con --publicar, sube los ficheros y el manifest actualizado.
  4. Se detiene al superar --max-horas; lo que quede se hará en la siguiente ejecución.

Otras opciones: --fuentes perfiles agregadas menores, --periodos 2023 2024 (fuerza esos periodos),
--desde 2020, --refresco-dias 120, --repo alaudaingenieria/ofe-indice-contratacion.
"""
import argparse, datetime, glob, hashlib, json, os, subprocess, sys, threading, queue, time

BASE = 'https://contrataciondelsectorpublico.gob.es/sindicacion/'
FUENTES = {
    'perfiles': 'sindicacion_643/licitacionesPerfilesContratanteCompleto3_{p}.zip',
    'agregadas': 'sindicacion_1044/PlataformasAgregadasSinMenores_{p}.zip',
    'menores': 'sindicacion_1143/contratosMenoresPerfilesContratantes_{p}.zip',
}
TIPOS = ('licitaciones', 'adjudicaciones', 'documentos')
AQUI = os.path.dirname(os.path.abspath(__file__))
DESC = os.path.abspath(os.path.join(AQUI, '..', 'descargas')); DIST = os.path.abspath(os.path.join(AQUI, '..', 'dist'))
TAG = 'datos'


def log(*a): print(f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]', *a, flush=True)


def gh(*args, check=True):
    return subprocess.run(['gh', *args], capture_output=True, text=True, check=check)


def manifest_publicado(repo):
    try:
        gh('release', 'download', TAG, '--repo', repo, '--pattern', 'manifest.json', '--dir', DIST, '--clobber')
        return json.load(open(os.path.join(DIST, 'manifest.json')))
    except Exception:
        return {'ficheros': []}


def publicar(repo, files, man):
    if gh('release', 'view', TAG, '--repo', repo, check=False).returncode != 0:
        gh('release', 'create', TAG, '--repo', repo, '--title', 'Índice de contratación (datos)',
           '--notes', 'Ficheros Parquet del índice. Ver README y manifest.json.')
    gh('release', 'upload', TAG, '--repo', repo, '--clobber', *files)
    mp = os.path.join(DIST, 'manifest.json')
    json.dump(man, open(mp, 'w'), indent=1, ensure_ascii=False)
    gh('release', 'upload', TAG, '--repo', repo, '--clobber', mp)


def descargar(url, dest):
    if os.path.exists(dest): return True
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
    gen = {}
    for f in man.get('ficheros', []):
        gen.setdefault((f['fuente'], f['periodo']), set()).add(f['tipo'])
        gen[(f['fuente'], f['periodo'], 'fecha')] = f.get('generado', man.get('generado', ''))[:10]
    out = []
    for fu in a.fuentes:
        if a.periodos:
            pers = a.periodos
        else:
            pers = []
            for p in map(str, range(a.desde, y)):
                tipos = gen.get((fu, p), set())
                fecha = gen.get((fu, p, 'fecha'), '')
                viejo = not fecha or (hoy - datetime.date.fromisoformat(fecha)).days > a.refresco_dias
                if tipos != set(TIPOS) or viejo: pers.append(p)
            pers.append(str(y))
        for p in pers:
            if p == str(y):
                meses = [f'{y}{m:02d}' for m in range(1, hoy.month)]
                out.append((fu, p, [(BASE + FUENTES[fu].format(p=m), os.path.join(DESC, f'{fu}_{m}.zip')) for m in meses]))
            else:
                out.append((fu, p, [(BASE + FUENTES[fu].format(p=p), os.path.join(DESC, f'{fu}_{p}.zip'))]))
    # primero lo que no existe, luego el año en curso y los refrescos
    out.sort(key=lambda t: (set(TIPOS) <= gen.get((t[0], t[1]), set()), t[1] == str(y), t[1], t[0]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fuentes', nargs='+', default=list(FUENTES)); ap.add_argument('--periodos', nargs='+')
    ap.add_argument('--desde', type=int, default=2020); ap.add_argument('--refresco-dias', type=int, default=120)
    ap.add_argument('--max-horas', type=float, default=5); ap.add_argument('--publicar', action='store_true')
    ap.add_argument('--repo', default='alaudaingenieria/ofe-indice-contratacion')
    a = ap.parse_args()
    t0 = time.time()
    os.makedirs(DESC, exist_ok=True); os.makedirs(DIST, exist_ok=True)
    man = manifest_publicado(a.repo) if a.publicar else {'ficheros': []}
    tr = pendientes(a, man)
    log(f'{len(tr)} periodos pendientes:', ', '.join(f'{f} {p}' for f, p, _ in tr))
    q = queue.Queue(maxsize=1)
    stop = threading.Event()

    def productor():
        for f, p, files in tr:
            if stop.is_set(): break
            ok = [dest for url, dest in files if descargar(url, dest)]
            q.put((f, p, ok, len(files)))
        q.put(None)
    threading.Thread(target=productor, daemon=True).start()
    hechos = []
    while True:
        it = q.get()
        if it is None: break
        f, p, ok, n = it
        log(f'{f} {p}: {len(ok)}/{n} ficheros descargados')
        if not ok: continue
        r = subprocess.run([sys.executable, os.path.join(AQUI, 'build_indice.py'), '--fuente', f, '--periodo', p, '--zips', *ok, '--out', DIST])
        if r.returncode != 0:
            log(f'ERROR procesando {f} {p}'); continue
        for z in ok: os.remove(z)
        files = [os.path.join(DIST, f'{t}_{f}_{p}.parquet') for t in TIPOS]
        import pandas as pd
        man['ficheros'] = [x for x in man.get('ficheros', []) if not (x['fuente'] == f and x['periodo'] == p)]
        for fp in files:
            man['ficheros'].append({'fichero': os.path.basename(fp), 'tipo': os.path.basename(fp).split('_')[0], 'fuente': f, 'periodo': p,
                                    'meses': len(ok) if p == str(datetime.date.today().year) else 12,
                                    'filas': pd.read_parquet(fp, columns=['id']).shape[0], 'bytes': os.path.getsize(fp),
                                    'sha256': hashlib.sha256(open(fp, 'rb').read()).hexdigest(),
                                    'generado': datetime.date.today().isoformat()})
        man['generado'] = datetime.datetime.now().isoformat(timespec='seconds')
        man['ficheros'].sort(key=lambda x: (x['fuente'], x['periodo'], x['tipo']))
        if a.publicar:
            publicar(a.repo, files, man); log(f'publicado {f} {p}')
            for fp in files: os.remove(fp)
        else:
            json.dump(man, open(os.path.join(DIST, 'manifest.json'), 'w'), indent=1, ensure_ascii=False)
        hechos.append(f'{f} {p}')
        if (time.time() - t0) / 3600 > a.max_horas:
            log(f'Límite de {a.max_horas} h alcanzado; el resto queda para la próxima ejecución.'); stop.set(); break
    log('Terminado. Hechos:', ', '.join(hechos) or 'ninguno')


if __name__ == '__main__':
    main()
