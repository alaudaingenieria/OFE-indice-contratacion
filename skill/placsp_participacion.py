#!/usr/bin/env python3
"""Lee actas de mesa / informes de valoración / resoluciones de las licitaciones candidatas y
detecta si la empresa (o una UTE suya) se presentó aunque no ganara.

python3 placsp_participacion.py --in resultado.json --nif A123 --nombre "SACYR" --out participacion.json [--max 400]
"""
import argparse, hashlib, json, os, re, subprocess, sys, time, unicodedata
from concurrent.futures import ThreadPoolExecutor
import requests

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
CACHE = 'cache_actas'
os.makedirs(CACHE, exist_ok=True)


def strip(s):
    s = unicodedata.normalize('NFD', s.upper())
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def text_of(url):
    url = url.replace('https://contrataciondelestado.es', 'https://contrataciondelsectorpublico.gob.es')
    tp = f'{CACHE}/{hashlib.md5(url.encode()).hexdigest()}.txt'
    if os.path.exists(tp): return open(tp, encoding='utf-8', errors='ignore').read()
    for i in range(3):
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=120)
            if r.status_code == 200 and not r.content.startswith(b'<html><head><title>Request Rejected'):
                if r.content[:4] == b'%PDF':
                    p = tp + '.pdf'; open(p, 'wb').write(r.content)
                    t = subprocess.run(['pdftotext', '-q', '-layout', p, '-'], capture_output=True, timeout=300).stdout.decode('utf-8', 'ignore')
                    os.remove(p)
                else:
                    t = r.content.decode('utf-8', 'ignore'); t = re.sub(r'<[^>]+>', ' ', t)
                open(tp, 'w', encoding='utf-8').write(t); return t
            if r.status_code in (404, 500): return ''
        except Exception:
            pass
        time.sleep(4 * (i + 1))
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--nif', action='append', default=[]); ap.add_argument('--nombre', action='append', default=[])
    ap.add_argument('--max', type=int, default=400); ap.add_argument('--hilos', type=int, default=16)
    a = ap.parse_args()
    pats = [re.compile(re.escape(n.upper().replace('-', ''))) for n in a.nif]
    pats += [re.compile(r'(?<![A-Z0-9])' + r'\W{0,3}'.join(map(re.escape, strip(v).replace('.', ' ').split())) + r'(?![A-Z0-9])') for v in a.nombre]
    cand = json.load(open(a.inp))['candidatas_participacion'][:a.max]
    jobs = [(i, fn, u) for i, c in enumerate(cand) for fn, u in c['docs_mesa']]
    print(f'{len(cand)} licitaciones, {len(jobs)} documentos', file=sys.stderr)
    hits = {}
    def work(j):
        i, fn, u = j
        t = text_of(u); T = strip(t)
        for p in pats:
            m = p.search(T)
            if m:
                return i, fn, u, re.sub(r'\s+', ' ', t[max(0, m.start() - 250): m.end() + 250])
        return None
    with ThreadPoolExecutor(a.hilos) as ex:
        for k, r in enumerate(ex.map(work, jobs)):
            if r and r[0] not in hits: hits[r[0]] = r
            if (k + 1) % 100 == 0: print(f'{k + 1}/{len(jobs)} documentos, {len(hits)} participaciones', file=sys.stderr)
    out = []
    for i, (i2, fn, u, snip) in sorted(hits.items()):
        c = dict(cand[i]); c.pop('docs_mesa', None)
        out.append({**c, 'relacion': 'Licitadora (no adjudicataria)', 'evidencia_documento': fn, 'evidencia_url': u, 'evidencia_extracto': snip})
    json.dump(out, open(a.out, 'w'), ensure_ascii=False)
    print(json.dumps({'licitaciones_revisadas': len(cand), 'participaciones_detectadas': len(out)}))


if __name__ == '__main__':
    main()
