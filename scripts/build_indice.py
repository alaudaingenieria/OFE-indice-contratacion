#!/usr/bin/env python3
"""Convierte uno o varios ZIP de datos abiertos de la PLACSP en el índice reducido (Parquet).

Uso:
  python3 build_indice.py --fuente perfiles --periodo 2025 --zips descargas/643_2025.zip --out dist/
  python3 build_indice.py --fuente perfiles --periodo 2026 --zips descargas/643_2026*.zip --out dist/

Genera en --out:
  licitaciones_<fuente>_<periodo>.parquet    una fila por expediente (última versión dentro del periodo)
  adjudicaciones_<fuente>_<periodo>.parquet  una fila por lote y adjudicatario
  documentos_<fuente>_<periodo>.parquet      enlaces a pliegos y a documentos de la mesa (actas, informes…)

El mismo expediente puede aparecer en varios periodos (cada vez que se actualiza). Al usar el índice,
une todos los periodos y quédate con la fila de 'updated' más reciente por 'id'.
"""
import argparse, glob, json, os, re, sys, zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
import pandas as pd

NS = {'cac': 'urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2',
      'cbc': 'urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2',
      'cac-place-ext': 'urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2',
      'cbc-place-ext': 'urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2'}
HDR = '<entry xmlns="http://www.w3.org/2005/Atom" ' + ' '.join(f'xmlns:{k}="{v}"' for k, v in NS.items()) + '>'
CBC = '{%s}' % NS['cbc']


# Prefijos largos que se abrevian para que el índice ocupe menos (ver README, "Prefijos").
PREFIJOS = [
    ('D:', 'https://contrataciondelestado.es/wps/wcm/connect/PLACE_es/Site/area/docAccCmpnt?srv=cmpnt&cmpntname=GetDocumentsById&source=library&DocumentIdParam='),
    ('F:', 'https://contrataciondelestado.es/FileSystem/servlet/GetDocumentByIdServlet?DocumentIdParam='),
    ('L:', 'https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl='),
    ('S:', 'https://contrataciondelestado.es/sindicacion/'),
]


def corta(u):
    for k, p in PREFIJOS:
        if u.startswith(p): return k + u[len(p):]
    return u


def L(t): return t.rsplit('}', 1)[-1]


def tx(el, path):
    if el is None: return ''
    return (el.findtext(path, '', NS) or '').strip()


def num(s):
    try: return float(s)
    except Exception: return None


def docs(cfs, path, fname_path):
    out = []
    for d in cfs.findall(path, NS):
        u = tx(d, 'cac:Attachment/cac:ExternalReference/cbc:URI')
        if u: out.append([tx(d, fname_path) or tx(d, 'cbc:ID'), corta(u)])
    return out


def parse(raw):
    e = ET.fromstring(HDR + raw[len('<entry>'):])
    g = lambda tag: next((c.text for c in e if L(c.tag) == tag), '') or ''
    cfs = next((c for c in e if L(c.tag) == 'ContractFolderStatus'), None)
    if cfs is None: return None, []
    pp = cfs.find('cac:ProcurementProject', NS)
    lcp = cfs.find('cac-place-ext:LocatedContractingParty', NS)
    lots = {tx(l, 'cbc:ID'): l for l in cfs.findall('cac:ProcurementProjectLot', NS)}
    notices = [(tx(v, 'cbc-place-ext:NoticeTypeCode'), (d.text or '')[:10])
               for v in cfs.findall('cac-place-ext:ValidNoticeInfo', NS) for d in v.iter(CBC + 'IssueDate')]
    cn = sorted(d for c, d in notices if c == 'DOC_CN'); allp = sorted(d for c, d in notices if d)
    pdur = pp.find('cac:PlannedPeriod', NS) if pp is not None else None
    dm = pdur.find('cbc:DurationMeasure', NS) if pdur is not None else None
    lic = {
        'id': corta(g('id')), 'updated': g('updated'), 'expediente': tx(cfs, 'cbc:ContractFolderID'),
        'organo': tx(lcp, 'cac:Party/cac:PartyName/cbc:Name'),
        'organo_nif': next((tx(i, 'cbc:ID') for i in (lcp.findall('cac:Party/cac:PartyIdentification', NS) if lcp is not None else [])
                            if 'NIF' in (i.find('cbc:ID', NS).get('schemeName', '') if i.find('cbc:ID', NS) is not None else '')), ''),
        'organo_superior': tx(lcp, 'cac-place-ext:ParentLocatedParty/cac:PartyName/cbc:Name'),
        'titulo': re.sub(r'\s+', ' ', g('title')).strip(),
        'link': corta(next((c.get('href') for c in e if L(c.tag) == 'link'), '')),
        'estado': tx(cfs, 'cbc-place-ext:ContractFolderStatusCode'),
        'tipo': tx(pp, 'cbc:TypeCode'), 'subtipo': tx(pp, 'cbc:SubTypeCode'),
        'procedimiento': tx(cfs, 'cac:TenderingProcess/cbc:ProcedureCode'),
        'urgencia': tx(cfs, 'cac:TenderingProcess/cbc:UrgencyCode'),
        'cpv': ' '.join(sorted({c.text for c in (pp.findall('.//cbc:ItemClassificationCode', NS) if pp is not None else []) if c.text})),
        'provincia': tx(pp, 'cac:RealizedLocation/cbc:CountrySubentity'), 'nuts': tx(pp, 'cac:RealizedLocation/cbc:CountrySubentityCode'),
        'presupuesto_sin_iva': num(tx(pp, 'cac:BudgetAmount/cbc:TaxExclusiveAmount')),
        'presupuesto_con_iva': num(tx(pp, 'cac:BudgetAmount/cbc:TotalAmount')),
        'valor_estimado': num(tx(pp, 'cac:BudgetAmount/cbc:EstimatedOverallContractAmount')),
        'duracion': num(dm.text) if dm is not None else None, 'duracion_unidad': dm.get('unitCode') if dm is not None else '',
        'prorroga_texto': ' '.join(t.strip() for t in pp.find('cac:ContractExtension', NS).itertext() if t.strip())[:500]
                          if pp is not None and pp.find('cac:ContractExtension', NS) is not None else '',
        'fecha_anuncio': cn[0] if cn else '', 'fecha_primera_publicacion': allp[0] if allp else '',
        'n_lotes': len(lots),
        'docs_pliegos': json.dumps(docs(cfs, 'cac:LegalDocumentReference', 'cbc:ID') + docs(cfs, 'cac:TechnicalDocumentReference', 'cbc:ID')
                                   + docs(cfs, 'cac:AdditionalDocumentReference', 'cbc:ID'), ensure_ascii=False),
        'docs_mesa': json.dumps(docs(cfs, 'cac-place-ext:GeneralDocument/cac-place-ext:GeneralDocumentDocumentReference',
                                     'cac:Attachment/cac:ExternalReference/cbc:FileName'), ensure_ascii=False),
    }
    adj = []
    for trs in cfs.findall('cac:TenderResult', NS):
        lot = tx(trs, 'cac:AwardedTenderedProject/cbc:ProcurementProjectLotID'); lp = lots.get(lot)
        base = {'id': lic['id'], 'updated': lic['updated'], 'lote': lot,
                'titulo_lote': re.sub(r'\s+', ' ', tx(lp, 'cac:ProcurementProject/cbc:Name')) if lp is not None else '',
                'presupuesto_lote_sin_iva': num(tx(lp, 'cac:ProcurementProject/cac:BudgetAmount/cbc:TaxExclusiveAmount')) if lp is not None else None,
                'resultado': tx(trs, 'cbc:ResultCode'), 'fecha_adjudicacion': tx(trs, 'cbc:AwardDate')[:10],
                'fecha_formalizacion': tx(trs, 'cac:Contract/cbc:IssueDate')[:10],
                'importe_adjudicado_sin_iva': num(tx(trs, 'cac:AwardedTenderedProject/cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount')),
                'importe_adjudicado_con_iva': num(tx(trs, 'cac:AwardedTenderedProject/cac:LegalMonetaryTotal/cbc:PayableAmount')),
                'n_ofertas': num(tx(trs, 'cbc:ReceivedTenderQuantity')),
                'oferta_mas_baja': num(tx(trs, 'cbc:LowerTenderAmount')), 'oferta_mas_alta': num(tx(trs, 'cbc:HigherTenderAmount'))}
        wps = trs.findall('cac:WinningParty', NS)
        if not wps:
            adj.append({**base, 'adjudicatario': '', 'nif_adjudicatario': ''})
        for wp in wps:
            adj.append({**base, 'adjudicatario': tx(wp, 'cac:PartyName/cbc:Name'), 'nif_adjudicatario': tx(wp, 'cac:PartyIdentification/cbc:ID')})
    return lic, adj


def work(job):
    """Procesa un .atom dentro de un ZIP; devuelve las últimas versiones encontradas en él."""
    zpath, name = job
    lics, adjs = {}, {}
    with zipfile.ZipFile(zpath) as z:
        s = z.read(name).decode('utf-8', 'ignore')
    pos = 0
    while True:
        a = s.find('<entry>', pos)
        if a < 0: break
        b = s.find('</entry>', a); pos = b + 8
        try:
            lic, adj = parse(s[a:b + 8])
        except Exception:
            continue
        if not lic: continue
        k = lic['id']
        if k not in lics or lics[k]['updated'] < lic['updated']:
            lics[k] = lic; adjs[k] = adj
    return list(lics.values()), [r for v in adjs.values() for r in v]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--fuente', required=True); ap.add_argument('--periodo', required=True)
    ap.add_argument('--zips', nargs='+', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--procesos', type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    jobs = []
    for p in a.zips:
        for zp in sorted(glob.glob(p)):
            with zipfile.ZipFile(zp) as z:
                jobs += [(zp, n) for n in z.namelist() if n.endswith('.atom')]
    lics, adjs = {}, {}
    with ProcessPoolExecutor(a.procesos) as ex:
        for i, (L1, A1) in enumerate(ex.map(work, jobs, chunksize=4)):
            ids = {}
            for r in L1:
                if r['id'] not in lics or lics[r['id']]['updated'] < r['updated']:
                    lics[r['id']] = r; ids[r['id']] = r['updated']
            for r in A1:
                if r['id'] in ids and r['updated'] == ids[r['id']]:
                    adjs.setdefault(r['id'], {})
                    if adjs[r['id']].get('_u') != r['updated']: adjs[r['id']] = {'_u': r['updated'], 'rows': []}
                    adjs[r['id']]['rows'].append(r)
            if (i + 1) % 200 == 0: print(f'  {i + 1}/{len(jobs)} ficheros .atom', file=sys.stderr)
    L_df = pd.DataFrame(list(lics.values()))
    A_df = pd.DataFrame([r for v in adjs.values() for r in v['rows']])
    D_df = L_df[['id', 'updated', 'docs_pliegos', 'docs_mesa']]
    L_df = L_df.drop(columns=['docs_pliegos', 'docs_mesa'])
    for df, nm in ((L_df, 'licitaciones'), (A_df, 'adjudicaciones'), (D_df, 'documentos')):
        f = os.path.join(a.out, f'{nm}_{a.fuente}_{a.periodo}.parquet')
        df.to_parquet(f, compression='zstd', compression_level=15, index=False)
        print(f'{f}: {len(df)} filas, {os.path.getsize(f) / 1e6:.1f} MB')
