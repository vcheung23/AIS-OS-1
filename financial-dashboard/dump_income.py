"""Print every income line item for GOPM May vs June with no filtering."""
import json, re

def load_qb(path):
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    for item in raw:
        if item.get('type') == 'text' and item.get('text','').startswith('{'):
            return json.loads(item['text'])

def get_col(report, label):
    for i, c in enumerate(report['Columns']['Column']):
        if c.get('ColTitle','') == label:
            return i
    return None

def walk(rows, may_i, jun_i, depth=0, results=None):
    if results is None:
        results = []
    for row in rows:
        rt = row.get('type','')
        if rt == 'Section':
            hdr = row.get('Header',{}).get('ColData',[{}])
            title = hdr[0].get('value','') if hdr else ''
            sub = row.get('Rows',{}).get('Row',[])
            walk(sub, may_i, jun_i, depth+1, results)
            # section summary row
            sm = row.get('Summary',{}).get('ColData',[])
            if title and len(sm) > max(may_i, jun_i):
                try:
                    m = float(sm[may_i].get('value','') or 0)
                    j = float(sm[jun_i].get('value','') or 0)
                    results.append(('  '*depth + f'[SUBTOTAL] {title}', m, j))
                except: pass
        elif rt == 'Data':
            cols = row.get('ColData',[])
            name = cols[0].get('value','').strip() if cols else ''
            if len(cols) > max(may_i, jun_i):
                try:
                    m = float(cols[may_i].get('value','') or 0)
                    j = float(cols[jun_i].get('value','') or 0)
                    if abs(m) > 0.01 or abs(j) > 0.01:
                        results.append(('  '*depth + name, m, j))
                except: pass
    return results

def get_section(report, keywords):
    for row in report.get('Rows',{}).get('Row',[]):
        if row.get('type') == 'Section':
            hdr = row.get('Header',{}).get('ColData',[{}])
            title = hdr[0].get('value','') if hdr else ''
            if any(k.lower() in title.lower() for k in keywords):
                return row
    return None

report = load_qb('C:/Users/vcheu/Claude Folder/financial_dashboard/qb_june_2026/gopm_ttm.json')
may_i = get_col(report, 'May 2026')
jun_i = get_col(report, 'Jun 2026')

print(f"May col index: {may_i}, Jun col index: {jun_i}")

# Walk the ENTIRE report (not just income section) so nothing is missed
all_rows = report.get('Rows',{}).get('Row',[])
lines = walk(all_rows, may_i, jun_i)

print(f"\n{'Line Item':<70} {'May':>12} {'Jun':>12} {'Delta':>12}")
print('-'*108)
for name, m, j in lines:
    d = j - m
    flag = '  <<<<' if abs(d) > 1000 else ''
    print(f"{name:<70} {m:>12,.0f} {j:>12,.0f} {d:>+12,.0f}{flag}")
