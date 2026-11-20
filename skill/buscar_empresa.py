#!/usr/bin/env python3
"""Busca en el índice OFE (alaudaingenieria/ofe-indice-contratacion) las adjudicaciones a una empresa,
directamente o en UTE, y las licitaciones candidatas para buscar participación sin adjudicación.

python3 buscar_empresa.py --nif A12345678 --nombre "SACYR" --nombre "SACYR CONSERVACION" \
    --desde 2023-01-01 --hasta 2026-10-31 [--fuentes perfiles agregadas menores] \
    [--organo "Carreteras|ADIF"] [--kw "firme|conservaci"] [--kw-excluir "limpieza"] [--participacion] \
    --out resultado.json

Escribe el mismo formato que placsp_empresa.py (claves 'adjudicaciones' y 'candidatas_participacion') y,
además, 'periodos_sin_indice': los años del rango que el índice aún no cubre (búscalos con placsp_empresa.py).
Necesita en la misma carpeta: leer_indice.py y placsp_empresa.py (de ahí toma la normalización y los códigos).
"""
import argparse, json, os, re, sys
import pandas as pd
import pyarrow.parquet as pq
import pyarrow.dataset as ds
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leer_indice as LI
import placsp_empresa as PE

def limpio(d):
    return {k: (None if isinstance(v, float) and v != v else v) for k, v in d.items()}


NOMBRE_FUENTE = {'perfiles': 'Perfiles PLACSP', 'agregadas': 'Agregadas autonómicas', 'menores': 'Contratos menores'}


def leer(files, d, cols=None, ids=None):
    dfs = []
    for f in files:
        p = os.path.join(d, f['fichero'])
        t = ds.dataset(p).to_table(columns=cols, filter=(ds.field('id').isin(list(ids)) if ids is not None else None)).to_pandas()
        t['fuente'] = f['fuente']; dfs.append(t)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nif', action='append', default=[]); ap.add_argument('--nombre', action='append', default=[])
    ap.add_argument('--desde', required=True); ap.add_argument('--hasta', required=True)
    ap.add_argument('--fuentes', nargs='+', default=['perfiles', 'agregadas'])
    ap.add_argument('--organo'); ap.add_argument('--kw'); ap.add_argument('--kw-excluir')
    ap.add_argument('--participacion', action='store_true'); ap.add_argument('--out', required=True)
    ap.add_argument('--dir', default='indice')
    a = ap.parse_args()
    if not a.nif and not a.nombre: ap.error('indica al menos un --nif o un --nombre')
    a.participacion = bool(a.participacion); PE.setup(a)   # prepara NIFS, patrones de nombre y prefiltro
    y0, y1 = int(a.desde[:4]), int(a.hasta[:4])
    man, files, faltan = LI.descargar(a.fuentes, y0, y1, documentos=a.participacion, d=a.dir)
    F = lambda t: [f for f in files if f['tipo'] == t]
    print(f'Índice del {man.get("generado", "?")}: {len(files)} ficheros', file=sys.stderr)

    # 1) Adjudicaciones de la empresa: prefiltro rápido por texto y comprobación exacta (NIF o nombre normalizado)
    adj = []
    for f in F('adjudicaciones'):
        t = pd.read_parquet(os.path.join(a.dir, f['fichero']))
        m = t['adjudicatario'].str.contains(PE.PRE, na=False) | t['nif_adjudicatario'].str.contains(PE.PRE, na=False)
        t = t[m].copy(); t['fuente'] = f['fuente']; adj.append(t)
    adj = pd.concat(adj, ignore_index=True) if adj else pd.DataFrame(columns=['id'])
    if len(adj):
        adj['coincidencia'] = [PE.match(n, s) for n, s in zip(adj['nif_adjudicatario'], adj['adjudicatario'])]
        adj = adj[adj['coincidencia'].notna()]
    ids = set(adj['id']) if len(adj) else set()

    # 2) Última versión de esos expedientes (licitaciones): las adjudicaciones válidas son las de esa versión
    lic = leer(F('licitaciones'), a.dir, ids=ids) if ids else pd.DataFrame(columns=['id', 'updated'])
    lic = lic.sort_values('updated').drop_duplicates('id', keep='last') if len(lic) else lic
    ult = dict(zip(lic['id'], lic['updated'])) if len(lic) else {}
    if len(adj):
        adj = adj[[ult.get(i) == u for i, u in zip(adj['id'], adj['updated'])]].drop_duplicates(['id', 'lote', 'nif_adjudicatario', 'adjudicatario'])
    rows = []
    L = lic.set_index('id').to_dict('index') if len(lic) else {}

    def base(i):
        l = L[i]
        return {'fuente': NOMBRE_FUENTE.get(l['fuente'], l['fuente']), 'id': LI.url_completa(i), 'updated': l['updated'],
                'expediente': l['expediente'], 'organo': l['organo'], 'titulo': l['titulo'], 'link': LI.url_completa(l['link']),
                'estado': l['estado'], 'tipo': PE.TIPO.get(l['tipo'], l['tipo']), 'procedimiento': PE.PROC.get(l['procedimiento'], l['procedimiento']),
                'cpv': l['cpv'], 'provincia': l['provincia'], 'presupuesto_sin_iva': l['presupuesto_sin_iva'],
                'valor_estimado': l['valor_estimado'], 'fecha_anuncio': l['fecha_anuncio'] or l['fecha_primera_publicacion'], 'n_lotes': l['n_lotes']}
    ok = lambda b: ((not PE.ORG or PE.ORG.search(b['organo'] or '')) and (not PE.KW or PE.KW.search(b['titulo'] or ''))
                    and not (PE.KWX and PE.KWX.search(b['titulo'] or '')))
    for r in adj.to_dict('records') if len(adj) else []:
        if r['id'] not in L: continue
        b = base(r['id'])
        if not ok(b): continue
        fecha = r['fecha_adjudicacion'] or b['fecha_anuncio']
        if not (a.desde <= (fecha or '') <= a.hasta): continue
        rows.append({**b, 'relacion': 'Adjudicataria' + (' en UTE' if PE.is_ute(r['nif_adjudicatario'], r['adjudicatario']) else ''),
                     'coincidencia': r['coincidencia'], 'adjudicatario': r['adjudicatario'], 'nif_adjudicatario': r['nif_adjudicatario'],
                     'lote': r['lote'], 'titulo_lote': r['titulo_lote'], 'presupuesto_lote_sin_iva': r['presupuesto_lote_sin_iva'],
                     'resultado': PE.RES.get(r['resultado'], r['resultado']), 'fecha_adjudicacion': r['fecha_adjudicacion'],
                     'fecha_formalizacion': r['fecha_formalizacion'], 'importe_adjudicado_sin_iva': r['importe_adjudicado_sin_iva'],
                     'importe_adjudicado_con_iva': r['importe_adjudicado_con_iva'], 'n_ofertas': r['n_ofertas'],
                     'oferta_mas_baja': r['oferta_mas_baja'], 'oferta_mas_alta': r['oferta_mas_alta']})

    # 3) Candidatas para participación: licitaciones del rango, dentro de los filtros, resueltas y no ganadas
    cand = []
    if a.participacion:
        cols = ['id', 'updated', 'expediente', 'organo', 'titulo', 'link', 'estado', 'tipo', 'procedimiento', 'cpv', 'provincia',
                'presupuesto_sin_iva', 'valor_estimado', 'fecha_anuncio', 'fecha_primera_publicacion', 'n_lotes']
        cl = []
        for f in F('licitaciones'):
            t = pd.read_parquet(os.path.join(a.dir, f['fichero']), columns=cols); t['fuente'] = f['fuente']
            if PE.ORG: t = t[t['organo'].str.contains(PE.ORG, na=False)]
            if PE.KW: t = t[t['titulo'].str.contains(PE.KW, na=False)]
            if PE.KWX: t = t[~t['titulo'].str.contains(PE.KWX, na=False)]
            cl.append(t)
        cl = pd.concat(cl, ignore_index=True).sort_values('updated').drop_duplicates('id', keep='last') if cl else pd.DataFrame()
        fa = cl['fecha_anuncio'].where(cl['fecha_anuncio'] != '', cl['fecha_primera_publicacion']) if len(cl) else []
        cl = cl[(fa >= a.desde) & (fa <= a.hasta)] if len(cl) else cl
        ganadas = {r['id'] for r in rows} | ids
        cl = cl[~cl['id'].isin(ganadas)] if len(cl) else cl
        print(f'{len(cl)} licitaciones dentro de los filtros para revisar participación', file=sys.stderr)
        if len(cl):
            cids = set(cl['id']); L.update(cl.set_index('id').to_dict('index'))
            aw = leer(F('adjudicaciones'), a.dir, cols=['id', 'updated', 'adjudicatario', 'resultado'], ids=cids)
            dc = leer(F('documentos'), a.dir, ids=cids)
            dc = dc.sort_values('updated').drop_duplicates('id', keep='last') if len(dc) else dc
            win = aw.groupby('id')['adjudicatario'].apply(lambda s: ' | '.join(sorted({x for x in s if x}))).to_dict() if len(aw) else {}
            for r in dc.to_dict('records') if len(dc) else []:
                if r['id'] not in win: continue   # sin resultado: aún no hay mesa
                docs = [[n, LI.url_completa(u)] for n, u in json.loads(r['docs_mesa'] or '[]') if PE.DOCS_PART.search(n or '')]
                if docs: cand.append({**base(r['id']), 'docs_mesa': docs, 'adjudicatarios': win[r['id']]})
    rows = [limpio(r) for r in rows]; cand = [limpio(r) for r in cand]
    out = {'adjudicaciones': rows, 'candidatas_participacion': cand,
           'periodos_sin_indice': [f'{fu} {y}' for fu, y in faltan], 'indice_generado': man.get('generado')}
    json.dump(out, open(a.out, 'w'), ensure_ascii=False, default=lambda x: None)
    print(json.dumps({'adjudicaciones': len(rows), 'expedientes': len({r['id'] for r in rows}),
                      'importe_adjudicado_sin_iva': round(sum(r['importe_adjudicado_sin_iva'] or 0 for r in rows)),
                      'candidatas_participacion': len(cand), 'periodos_sin_indice': out['periodos_sin_indice']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
