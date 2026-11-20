#!/usr/bin/env python3
"""Descarga y carga el índice publicado (carpeta data/ del repositorio), uniendo periodos y quedándose con la última versión.

Como módulo:
  from leer_indice import cargar
  lic, adj = cargar(fuentes=['perfiles', 'agregadas'], desde=2022, hasta=2026)                 # licitaciones + adjudicaciones
  lic, adj, docs = cargar(fuentes=['perfiles'], desde=2024, hasta=2026, documentos=True)        # + enlaces a pliegos y actas

Desde la línea de comandos (solo descarga):
  python3 leer_indice.py --fuentes perfiles agregadas --desde 2022 --hasta 2026 [--documentos] --dir indice/

No necesita cuenta de GitHub: el repositorio es público.
"""
import argparse, json, os, subprocess

REPO = 'alaudaingenieria/ofe-indice-contratacion'
URL = f'https://raw.githubusercontent.com/{REPO}/main/data/'
PREFIJOS = {
    'D:': 'https://contrataciondelsectorpublico.gob.es/wps/wcm/connect/PLACE_es/Site/area/docAccCmpnt?srv=cmpnt&cmpntname=GetDocumentsById&source=library&DocumentIdParam=',
    'F:': 'https://contrataciondelsectorpublico.gob.es/FileSystem/servlet/GetDocumentByIdServlet?DocumentIdParam=',
    'L:': 'https://contrataciondelsectorpublico.gob.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl=',
    'S:': 'https://contrataciondelsectorpublico.gob.es/sindicacion/',
}


def url_completa(u):
    """Devuelve la URL completa (y ya en el dominio contrataciondelsectorpublico.gob.es) de un enlace abreviado."""
    if u and u[:2] in PREFIJOS: return PREFIJOS[u[:2]] + u[2:]
    return (u or '').replace('https://contrataciondelestado.es', 'https://contrataciondelsectorpublico.gob.es')


def _get(nombre, d):
    dest = os.path.join(d, nombre)
    if not os.path.exists(dest):
        subprocess.run(['curl', '-sSLf', '--retry', '3', '-o', dest + '.part', URL + nombre], check=True)
        os.rename(dest + '.part', dest)
    return dest


def manifest(d='indice'):
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, 'manifest.json')
    if os.path.exists(p): os.remove(p)   # siempre el más reciente
    return json.load(open(_get('manifest.json', d)))


def descargar(fuentes, desde, hasta, documentos=False, d='indice'):
    """Licitaciones y adjudicaciones: todos los periodos desde 'desde' en adelante (los cambios posteriores de un
    expediente están en periodos más recientes). Documentos: solo de 'desde' a 'hasta'+1, porque pesan más.
    Los periodos son años (AAAA) o, para el año en curso, meses (AAAAMM)."""
    man = manifest(d)
    yr = lambda f: int(f['periodo'][:4])
    sel = [f for f in man['ficheros'] if f['fuente'] in fuentes and (
        (f['tipo'] in ('licitaciones', 'adjudicaciones') and yr(f) >= desde) or
        (documentos and f['tipo'] == 'documentos' and desde <= yr(f) <= hasta + 1))]
    for f in sel: _get(f['fichero'], d)
    cubiertos = {(f['fuente'], f['periodo'][:4]) for f in man['ficheros']}
    faltan = sorted({(fu, str(y)) for fu in fuentes for y in range(desde, hasta + 1)} - cubiertos)
    return man, sel, faltan


def cargar(fuentes, desde, hasta, documentos=False, d='indice'):
    import pandas as pd
    man, files, faltan = descargar(fuentes, desde, hasta, documentos, d)
    if faltan: print('AVISO: periodos aún no indexados:', ', '.join(f'{a} {b}' for a, b in faltan))
    out = []
    for tipo in ['licitaciones', 'adjudicaciones'] + (['documentos'] if documentos else []):
        dfs = []
        for f in files:
            if f['tipo'] != tipo: continue
            df = pd.read_parquet(os.path.join(d, f['fichero'])); df['fuente'] = f['fuente']; dfs.append(df)
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        if len(df):
            if tipo == 'adjudicaciones':   # quedarse con las filas de la última versión de cada expediente
                ult = df.groupby('id')['updated'].transform('max'); df = df[df['updated'] == ult].drop_duplicates()
            else:
                df = df.sort_values('updated').drop_duplicates('id', keep='last')
            for c in ('id', 'link'):
                if c in df: df[c] = df[c].map(url_completa)
        out.append(df.reset_index(drop=True))
    return tuple(out)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--fuentes', nargs='+', default=['perfiles', 'agregadas']); ap.add_argument('--desde', type=int, required=True)
    ap.add_argument('--hasta', type=int, required=True); ap.add_argument('--documentos', action='store_true'); ap.add_argument('--dir', default='indice')
    a = ap.parse_args()
    man, files, faltan = descargar(a.fuentes, a.desde, a.hasta, a.documentos, a.dir)
    print(f'Índice generado {man.get("generado")}: {len(files)} ficheros en {a.dir}/')
    if faltan: print('Periodos aún no indexados (habrá que usar los ZIP de la Plataforma):', ', '.join(f'{x} {y}' for x, y in faltan))
