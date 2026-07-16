"""
Analyze WHY revenue changed MoM. Diffs all income line items between May and June.
"""
import json

def load_qb_report(path):
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    for item in raw:
        if item['type'] == 'text' and item['text'].startswith('{'):
            return json.loads(item['text'])
    return None

def get_col_index(report, month_label):
    cols = report['Columns']['Column']
    for i, col in enumerate(cols):
        if col.get('ColTitle', '') == month_label:
            return i
    return None

def flatten_rows(rows, col_idx, depth=0, results=None):
    """Recursively walk all rows and collect leaf data lines."""
    if results is None:
        results = []
    for row in rows:
        rtype = row.get('type', '')
        if rtype == 'Section':
            sub = row.get('Rows', {}).get('Row', [])
            flatten_rows(sub, col_idx, depth+1, results)
        elif rtype == 'Data':
            cols = row.get('ColData', [])
            if not cols:
                continue
            name = cols[0].get('value', '').strip()
            if col_idx < len(cols):
                val_str = cols[col_idx].get('value', '') or '0'
                try:
                    val = float(val_str)
                    if name and abs(val) > 0.01:
                        results.append((name, val))
                except:
                    pass
    return results

def get_section(report, keywords):
    """Find a top-level section whose title contains any keyword."""
    rows = report.get('Rows', {}).get('Row', [])
    for row in rows:
        if row.get('type') == 'Section':
            header = row.get('Header', {}).get('ColData', [{}])
            title = header[0].get('value', '') if header else ''
            if any(k.lower() in title.lower() for k in keywords):
                return row
    return None

def section_total(section, col_idx):
    summary = section.get('Summary', {}).get('ColData', [])
    if col_idx < len(summary):
        try:
            return float(summary[col_idx].get('value', '0') or '0')
        except:
            return 0
    return 0

def analyze_entity(qb_path, entity_name, may_label='May 2026', jun_label='Jun 2026'):
    report = load_qb_report(qb_path)
    if not report:
        print(f"Could not load {entity_name}")
        return

    may_idx = get_col_index(report, may_label)
    jun_idx = get_col_index(report, jun_label)
    if may_idx is None or jun_idx is None:
        cols = report['Columns']['Column']
        print(f"{entity_name}: columns not found. Available: {[c.get('ColTitle') for c in cols]}")
        return

    income_sec = get_section(report, ['Income', 'Revenue'])
    cogs_sec   = get_section(report, ['Cost of', 'COGS', 'Direct'])
    exp_sec    = get_section(report, ['Expense', 'Operating'])

    print(f"\n{'='*70}")
    print(f"  {entity_name} -- Revenue Change Analysis (May vs Jun 2026)")
    print(f"{'='*70}")

    def diff_section(label, section):
        if not section:
            return
        may_lines = flatten_rows(section.get('Rows', {}).get('Row', []), may_idx)
        jun_lines = flatten_rows(section.get('Rows', {}).get('Row', []), jun_idx)

        may_dict = {}
        for name, val in may_lines:
            may_dict[name] = may_dict.get(name, 0) + val
        jun_dict = {}
        for name, val in jun_lines:
            jun_dict[name] = jun_dict.get(name, 0) + val

        all_names = sorted(set(list(may_dict.keys()) + list(jun_dict.keys())))
        diffs = []
        for name in all_names:
            m = may_dict.get(name, 0)
            j = jun_dict.get(name, 0)
            delta = j - m
            if abs(delta) > 50:
                diffs.append((name, m, j, delta))

        if not diffs:
            return

        diffs.sort(key=lambda x: x[3])  # biggest drops first

        may_total = section_total(section, may_idx)
        jun_total = section_total(section, jun_idx)
        delta_total = jun_total - may_total

        print(f"\n  {label}  (Total: {may_total:,.0f} -> {jun_total:,.0f}  delta {'+' if delta_total>=0 else ''}{delta_total:,.0f})")
        print(f"  {'Line Item':<50} {'May':>12} {'Jun':>12} {'Change':>12}")
        print(f"  {'-'*50} {'-'*12} {'-'*12} {'-'*12}")
        for name, m, j, delta in diffs:
            direction = 'v' if delta < 0 else '^'
            print(f"  {name:<50} {m:>12,.0f} {j:>12,.0f} {direction}{abs(delta):>11,.0f}")

    diff_section("INCOME", income_sec)
    diff_section("COST OF GOODS SOLD", cogs_sec)
    diff_section("EXPENSES", exp_sec)


base = 'C:/Users/vcheu/Claude Folder/financial_dashboard/qb_june_2026/'
analyze_entity(base + 'gopm_ttm.json', 'Green Ocean PM (GOPM)')
analyze_entity(base + 'psb_ttm.json', 'Pro Services Boston (PSB)')
analyze_entity(base + 'ppb_ttm.json', 'Profitable Properties (PPB)')
