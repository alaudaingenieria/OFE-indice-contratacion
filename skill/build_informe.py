#!/usr/bin/env python3
"""Genera el informe HTML autónomo (sin dependencias externas) de contratación de una empresa
a partir de la plantilla informe_template.html (misma carpeta, o --plantilla).

python3 build_informe.py --adj resultado.json [--part participacion.json] --empresa "SACYR" \
   --nif A123 --nombre SACYR --criterios "2024-2026; perfiles PLACSP; palabras: ..." --out Informe_SACYR.html
También escribe <out>.csv con todas las filas.
"""
import argparse, csv, datetime, json, os, re, unicodedata

LEGAL = r'\b(S\s?A\s?U|S\s?L\s?U|S\s?A|S\s?L|SOCIEDAD ANONIMA|SOCIEDAD LIMITADA|SME|MP)\b'


def norm(s):
    s = unicodedata.normalize('NFD', (s or '').upper()); s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', re.sub(LEGAL, ' ', re.sub(r'[^A-Z0-9 ]+', ' ', s.replace('.', '')))).strip()


def socios(name, variants):
    s = re.sub(r'(?i)\b(en compromiso de |compromiso de )?u\.?\s?t\.?\s?e\.?\b|uni[oó]n temporal de empresas|\bley\s*18/1982\b', ' ', name)
    parts = re.split(r'\s+-\s+|\s*/\s*|\s*;\s*|\s*_\s*|,\s+(?=[A-ZÁÉÍÓÚÑ]{3})|\s+[Yy]\s+(?=[A-ZÁÉÍÓÚÑ])|\s+con\s+|(?<=[a-zA-Z])-(?=[A-Za-z])', s)
    out = []
    for p in parts:
        n = norm(p)
        if len(n) < 3 or any(norm(v) and norm(v) in n for v in variants): continue
        out.append(n.title())
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--adj', required=True); ap.add_argument('--part'); ap.add_argument('--out', required=True)
    ap.add_argument('--empresa', required=True); ap.add_argument('--nif', action='append', default=[])
    ap.add_argument('--nombre', action='append', default=[]); ap.add_argument('--criterios', default='')
    ap.add_argument('--plantilla', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'informe_template.html'))
    a = ap.parse_args()
    rows = json.load(open(a.adj))['adjudicaciones']
    if a.part: rows += json.load(open(a.part))
    data = []
    for i, r in enumerate(rows):
        pres = r.get('presupuesto_lote_sin_iva') or (r.get('presupuesto_sin_iva') if (r.get('n_lotes') or 0) <= 1 else None)
        imp = r.get('importe_adjudicado_sin_iva')
        data.append({
            'k': f"{r.get('id')}|{r.get('lote', '')}|{i}", 'rel': r['relacion'], 'fuente': r.get('fuente', ''),
            'fecha': r.get('fecha_adjudicacion') or r.get('fecha_anuncio') or '', 'fanuncio': r.get('fecha_anuncio', ''),
            'organo': r.get('organo', ''), 'exp': r.get('expediente', ''), 'titulo': r.get('titulo', ''),
            'lote': r.get('lote', ''), 'tlote': r.get('titulo_lote', ''), 'tipo': r.get('tipo', '') or 'Sin dato',
            'proc': r.get('procedimiento', ''), 'cpv': r.get('cpv', ''), 'prov': r.get('provincia', ''),
            'pres': pres, 'imp': imp, 'baja': (1 - imp / pres) if (imp and pres and pres > 0 and imp <= pres * 1.5) else None,
            'ofertas': r.get('n_ofertas'), 'adj': r.get('adjudicatario') or r.get('adjudicatarios', ''),
            'nif': r.get('nif_adjudicatario', ''), 'coin': r.get('coincidencia', ''),
            'socios': socios(r.get('adjudicatario', ''), a.nombre) if 'UTE' in r['relacion'] else [],
            'evid': r.get('evidencia_extracto', ''), 'evurl': r.get('evidencia_url', ''), 'evdoc': r.get('evidencia_documento', ''),
            'link': r.get('link', ''),
        })
    data.sort(key=lambda d: d['fecha'], reverse=True)
    meta = {'empresa': a.empresa, 'nif': a.nif, 'variantes': a.nombre, 'criterios': a.criterios,
            'generado': datetime.date.today().isoformat(), 'id': re.sub(r'\W+', '_', a.empresa.lower()) + '_' + datetime.date.today().isoformat()}
    payload = json.dumps({'meta': meta, 'rows': data}, ensure_ascii=False).replace('</', '<\\/')
    html = open(a.plantilla, encoding='utf-8').read().replace('__DATA__', payload).replace('__TITLE__', f'Contratación pública – {a.empresa}')
    open(a.out, 'w', encoding='utf-8').write(html)
    cols = ['rel', 'fecha', 'fanuncio', 'organo', 'exp', 'titulo', 'lote', 'tlote', 'tipo', 'proc', 'cpv', 'prov', 'pres', 'imp', 'baja',
            'ofertas', 'adj', 'nif', 'coin', 'socios', 'fuente', 'evdoc', 'evurl', 'link']
    with open(re.sub(r'\.html?$', '', a.out) + '.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';'); w.writerow(cols)
        for d in data:
            w.writerow([(' | '.join(d[c]) if isinstance(d[c], list) else str(d[c]).replace('.', ',') if isinstance(d[c], float)
                         else '' if d[c] is None else re.sub(r'[\r\n\t]+', ' ', str(d[c]))) for c in cols])
    print(len(data), 'filas ->', a.out)




if __name__ == '__main__':
    main()
