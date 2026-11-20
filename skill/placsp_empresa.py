#!/usr/bin/env python3
"""Busca en los ZIP de datos abiertos de la PLACSP las adjudicaciones (por lote) a una empresa,
directamente o en UTE, y los expedientes candidatos para buscar participación sin adjudicación.

python3 placsp_empresa.py --zips z/*.zip --nif A12345678 --nombre "SACYR" --nombre "SACYR CONSTRUCCION" \
    --desde 2024-01-01 --hasta 2026-09-30 [--kw "firme|carretera"] [--kw-excluir "limpieza"] \
    [--organo "Carreteras|ADIF"] [--participacion] --out resultado.json
"""
import argparse, glob, json, re, sys, unicodedata, zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor

NS = {'cac': 'urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2',
      'cbc': 'urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2',
      'cac-place-ext': 'urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2',
      'cbc-place-ext': 'urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2'}
HDR = '<entry xmlns="http://www.w3.org/2005/Atom" ' + ' '.join(f'xmlns:{k}="{v}"' for k, v in NS.items()) + '>'
LEGAL = r'\b(S\s?A\s?U|S\s?L\s?U|S\s?A|S\s?L|S\s?COOP|SOCIEDAD ANONIMA( UNIPERSONAL)?|SOCIEDAD LIMITADA( UNIPERSONAL)?|SLP|S\s?L\s?P|SCCL|S\s?C)\b'
TIPO = {'1': 'Suministros', '2': 'Servicios', '3': 'Obras', '7': 'Administrativo especial', '8': 'Privado',
        '21': 'Gestión de servicios públicos', '22': 'Concesión de servicios', '31': 'Concesión de obras', '40': 'Concesión de servicios', '50': 'Patrimonial'}
PROC = {'1': 'Abierto', '2': 'Restringido', '3': 'Negociado sin publicidad', '4': 'Negociado con publicidad', '5': 'Diálogo competitivo',
        '6': 'Normas internas', '7': 'Derivado de acuerdo marco', '8': 'Concurso de proyectos', '9': 'Abierto simplificado',
        '10': 'Asociación para la innovación', '11': 'Derivado de asociación para la innovación', '12': 'Basado en sistema dinámico',
        '13': 'Licitación con negociación', '100': 'Otros', '999': 'Otros'}
RES = {'1': 'Adjudicado provisionalmente', '2': 'Adjudicado definitivamente', '3': 'Desierto', '4': 'Desistimiento', '5': 'Renuncia',
       '6': 'Desierto provisionalmente', '7': 'Desierto definitivamente', '8': 'Adjudicado', '9': 'Formalizado', '10': 'Licitador mejor valorado', '11': 'Anulado'}
DOCS_PART = re.compile(r'acta|valoraci|adjudicaci|apertura|clasificaci|propuesta|resoluci', re.I)


def norm(s):
    s = unicodedata.normalize('NFD', (s or '').upper())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s.replace('.', ''))
    s = re.sub(LEGAL, ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def L(t): return t.rsplit('}', 1)[-1]


def tx(el, path):
    if el is None: return ''
    r = el.findtext(path, '', NS)
    return (r or '').strip()


def num(s):
    try: return float(s)
    except Exception: return None


def setup(a):
    global A, NIFS, PATS, KW, KWX, ORG
    A = a
    NIFS = {n.upper().replace('-', '').replace(' ', '') for n in a.nif}
    PATS = [re.compile(r'(?<![A-Z0-9])' + re.escape(norm(n)) + r'(?![A-Z0-9])') for n in a.nombre if norm(n)]
    KW = re.compile(a.kw, re.I) if a.kw else None
    KWX = re.compile(a.kw_excluir, re.I) if a.kw_excluir else None
    ORG = re.compile(a.organo, re.I) if a.organo else None
    global PRE
    PRE = prefilter(a)


ACC = {'A': '[AÁÀÄ]', 'E': '[EÉÈË]', 'I': '[IÍÌÏ]', 'O': '[OÓÒÖ]', 'U': '[UÚÙÜ]', 'N': '[NÑ]'}


def prefilter(a):
    """Regex barata sobre el XML en bruto: NIF o primera palabra larga de cada variante, sin distinguir acentos."""
    toks = set(n.upper().replace('-', '') for n in a.nif)
    for v in a.nombre:
        w = max(norm(v).split() or [''], key=len)
        if w: toks.add(''.join(ACC.get(c, re.escape(c)) for c in w))
    return re.compile('|'.join(toks), re.I) if toks else None


def match(nif, name):
    n = (nif or '').upper().replace('-', '').replace(' ', '')
    if n and n in NIFS: return 'NIF'
    nn = norm(name)
    for p in PATS:
        if p.search(nn): return 'Nombre'
    return None


def is_ute(nif, name):
    return (nif or '').upper().startswith('U') or bool(re.search(r'\bU\s?T\s?E\b|UNION TEMPORAL', norm(name)))


def parse_entry(raw, fuente):
    e = ET.fromstring(HDR + raw[len('<entry>'):])
    g = lambda tag: next((c.text for c in e if L(c.tag) == tag), '') or ''
    cfs = next((c for c in e if L(c.tag) == 'ContractFolderStatus'), None)
    if cfs is None: return None
    org = tx(cfs, 'cac-place-ext:LocatedContractingParty/cac:Party/cac:PartyName/cbc:Name')
    pp = cfs.find('cac:ProcurementProject', NS)
    lots = {tx(l, 'cbc:ID'): l for l in cfs.findall('cac:ProcurementProjectLot', NS)}
    notices = [(tx(v, 'cbc-place-ext:NoticeTypeCode'), (d.text or '')[:10])
               for v in cfs.findall('cac-place-ext:ValidNoticeInfo', NS) for d in v.iter('{%s}IssueDate' % NS['cbc'])]
    cn = sorted(d for c, d in notices if c == 'DOC_CN')
    allp = sorted(d for c, d in notices)
    base = {
        'fuente': fuente, 'id': g('id'), 'updated': g('updated'), 'expediente': tx(cfs, 'cbc:ContractFolderID'),
        'organo': org, 'titulo': re.sub(r'\s+', ' ', g('title')).strip(),
        'link': next((c.get('href') for c in e if L(c.tag) == 'link'), ''),
        'estado': tx(cfs, 'cbc-place-ext:ContractFolderStatusCode'),
        'tipo': TIPO.get(tx(pp, 'cbc:TypeCode'), tx(pp, 'cbc:TypeCode')),
        'procedimiento': PROC.get(tx(cfs, 'cac:TenderingProcess/cbc:ProcedureCode'), tx(cfs, 'cac:TenderingProcess/cbc:ProcedureCode')),
        'cpv': ' '.join(sorted({c.text for c in (pp.findall('.//cbc:ItemClassificationCode', NS) if pp is not None else []) if c.text})),
        'provincia': tx(pp, 'cac:RealizedLocation/cbc:CountrySubentity'),
        'presupuesto_sin_iva': num(tx(pp, 'cac:BudgetAmount/cbc:TaxExclusiveAmount')),
        'valor_estimado': num(tx(pp, 'cac:BudgetAmount/cbc:EstimatedOverallContractAmount')),
        'fecha_anuncio': cn[0] if cn else (allp[0] if allp else ''),
        'n_lotes': len(lots),
    }
    return e, cfs, lots, base


def scan_zip(path_fuente):
    path, fuente = path_fuente
    adj, cand = [], []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.endswith('.atom'): continue
            s = z.read(n).decode('utf-8', 'ignore')
            if not A.participacion and PRE:   # solo las entradas que contienen la empresa
                spans = {}
                for m in PRE.finditer(s):
                    a0 = s.rfind('<entry>', 0, m.start()); b0 = s.find('</entry>', m.end())
                    if a0 >= 0 and b0 >= 0: spans[a0] = b0 + 8
                raws = [s[a0:b0] for a0, b0 in sorted(spans.items())]
            else:
                raws = (m.group(0) for m in re.finditer(r'<entry>.*?</entry>', s, re.S))
            for raw in raws:
                try:
                    r = parse_entry(raw, fuente)
                except Exception:
                    continue
                if not r: continue
                e, cfs, lots, b = r
                if ORG and not ORG.search(b['organo']): continue
                if KW and not KW.search(b['titulo']): continue
                if KWX and KWX.search(b['titulo']): continue
                won = False
                for trs in cfs.findall('cac:TenderResult', NS):
                    for wp in trs.findall('cac:WinningParty', NS):
                        nif = tx(wp, 'cac:PartyIdentification/cbc:ID'); name = tx(wp, 'cac:PartyName/cbc:Name')
                        how = match(nif, name)
                        if not how: continue
                        won = True
                        lot = tx(trs, 'cac:AwardedTenderedProject/cbc:ProcurementProjectLotID')
                        lp = lots.get(lot)
                        adj.append({**b, 'relacion': 'Adjudicataria' + (' en UTE' if is_ute(nif, name) else ''),
                                    'coincidencia': how, 'adjudicatario': name, 'nif_adjudicatario': nif,
                                    'lote': lot, 'titulo_lote': re.sub(r'\s+', ' ', tx(lp, 'cac:ProcurementProject/cbc:Name')) if lp is not None else '',
                                    'presupuesto_lote_sin_iva': num(tx(lp, 'cac:ProcurementProject/cac:BudgetAmount/cbc:TaxExclusiveAmount')) if lp is not None else None,
                                    'resultado': RES.get(tx(trs, 'cbc:ResultCode'), tx(trs, 'cbc:ResultCode')),
                                    'fecha_adjudicacion': tx(trs, 'cbc:AwardDate')[:10],
                                    'fecha_formalizacion': tx(trs, 'cac:Contract/cbc:IssueDate')[:10],
                                    'importe_adjudicado_sin_iva': num(tx(trs, 'cac:AwardedTenderedProject/cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount')),
                                    'importe_adjudicado_con_iva': num(tx(trs, 'cac:AwardedTenderedProject/cac:LegalMonetaryTotal/cbc:PayableAmount')),
                                    'n_ofertas': num(tx(trs, 'cbc:ReceivedTenderQuantity')),
                                    'oferta_mas_baja': num(tx(trs, 'cbc:LowerTenderAmount')), 'oferta_mas_alta': num(tx(trs, 'cbc:HigherTenderAmount'))})
                if A.participacion and not won and cfs.findall('cac:TenderResult', NS):
                    docs = []
                    for gd in cfs.findall('cac-place-ext:GeneralDocument/cac-place-ext:GeneralDocumentDocumentReference', NS):
                        fn = tx(gd, 'cac:Attachment/cac:ExternalReference/cbc:FileName'); u = tx(gd, 'cac:Attachment/cac:ExternalReference/cbc:URI')
                        if u and DOCS_PART.search(fn): docs.append((fn, u))
                    winners = sorted({tx(wp, 'cac:PartyName/cbc:Name') for wp in cfs.iter('{%s}WinningParty' % NS['cac'])})
                    if docs: cand.append({**b, 'docs_mesa': docs, 'adjudicatarios': ' | '.join(winners)})
    return adj, cand


def dedupe(rows, key):
    best = {}
    for r in rows:
        k = key(r)
        if k not in best or best[k]['updated'] < r['updated']: best[k] = r
    return list(best.values())


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--zips', nargs='+', required=True, help='ZIPs; prefijo fuente con FUENTE= (ej. Agregadas=z/1044_2025.zip)')
    ap.add_argument('--nif', action='append', default=[]); ap.add_argument('--nombre', action='append', default=[])
    ap.add_argument('--desde', default='0000'); ap.add_argument('--hasta', default='9999')
    ap.add_argument('--kw'); ap.add_argument('--kw-excluir'); ap.add_argument('--organo')
    ap.add_argument('--participacion', action='store_true'); ap.add_argument('--out', required=True)
    ap.add_argument('--procesos', type=int, default=2)
    a = ap.parse_args(); setup(a)
    items = []
    for z in a.zips:
        f, p = (z.split('=', 1) if '=' in z else ('Perfiles PLACSP', z))
        items += [(x, f) for x in sorted(glob.glob(p))]
    adj, cand = [], []
    with ProcessPoolExecutor(a.procesos, initializer=setup, initargs=(a,)) as ex:
        for i, (x, c) in enumerate(ex.map(scan_zip, items)):
            adj += x; cand += c
            print(f'{i + 1}/{len(items)} {items[i][0]}: {len(x)} adjudicaciones, {len(c)} candidatas', file=sys.stderr)
    adj = dedupe(adj, lambda r: (r['id'], r['lote'], r['nif_adjudicatario'], r['adjudicatario']))
    adj = [r for r in adj if a.desde <= (r['fecha_adjudicacion'] or r['fecha_anuncio']) <= a.hasta]
    cand = dedupe(cand, lambda r: r['id'])
    ganadas = {r['id'] for r in adj}
    cand = [r for r in cand if r['id'] not in ganadas and a.desde <= r['fecha_anuncio'] <= a.hasta]
    json.dump({'adjudicaciones': adj, 'candidatas_participacion': cand}, open(a.out, 'w'), ensure_ascii=False)
    print(json.dumps({'adjudicaciones': len(adj), 'expedientes': len({r['id'] for r in adj}),
                      'importe_adjudicado_sin_iva': round(sum(r['importe_adjudicado_sin_iva'] or 0 for r in adj)),
                      'candidatas_participacion': len(cand)}, ensure_ascii=False))
