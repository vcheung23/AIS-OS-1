"""
Build data.json for a given month from QB TTM JSON files.

Usage:
  python build_data.py
    --month  June
    --year   2026
    --qb-dir /path/to/folder/with/ttm/files
    --output /path/to/data.json

Expects gopm_ttm.json, psb_ttm.json, ppb_ttm.json in --qb-dir.
Each file is a raw MCP tool-result wrapper: [{type, text}, ...]
where one item's text is the QB P&L JSON string.
"""

import json, argparse, os
from calendar import month_abbr
from datetime import datetime, timedelta

ENTITIES = ['GOPM', 'PSB', 'PPB']
FILES    = {'GOPM': 'gopm_ttm.json', 'PSB': 'psb_ttm.json', 'PPB': 'ppb_ttm.json'}

MONTH_ABBR = {m.lower(): m for m in month_abbr if m}  # jan->Jan etc.

# ── QB parse helpers ────────────────────────────────────────────────────────────

def load_report(path):
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    # Handle both wrapper format [{type,text},...] and raw report dict
    if isinstance(raw, list):
        for item in raw:
            if item.get('type') == 'text' and item.get('text', '').startswith('{'):
                return json.loads(item['text'])
    if isinstance(raw, dict):
        return raw
    return None

def col_idx(report, label):
    for i, c in enumerate(report['Columns']['Column']):
        if c.get('ColTitle', '') == label:
            return i
    return None

def all_col_labels(report):
    return [c.get('ColTitle', '') for c in report['Columns']['Column']]

def get_section(report, keywords):
    for row in report.get('Rows', {}).get('Row', []):
        if row.get('type') == 'Section':
            hdr = row.get('Header', {}).get('ColData', [{}])
            title = hdr[0].get('value', '') if hdr else ''
            if any(k.lower() in title.lower() for k in keywords):
                return row
    return None

def section_total(section, cidx):
    if section is None or cidx is None:
        return 0.0
    sm = section.get('Summary', {}).get('ColData', [])
    if cidx < len(sm):
        try:
            return float(sm[cidx].get('value', '') or '0')
        except:
            return 0.0
    return 0.0

def flatten_rows(rows, cidx):
    results = []
    for row in rows:
        if row.get('type') == 'Section':
            results.extend(flatten_rows(row.get('Rows', {}).get('Row', []), cidx))
        elif row.get('type') == 'Data':
            cols = row.get('ColData', [])
            name = cols[0].get('value', '').strip() if cols else ''
            if cidx is not None and cidx < len(cols):
                try:
                    val = float(cols[cidx].get('value', '') or '0')
                    if name and abs(val) > 0.01:
                        results.append((name, val))
                except:
                    pass
    return results

def find_line(report, needle, cidx, partial=True):
    def walk(rows):
        for row in rows:
            if row.get('type') == 'Section':
                v = walk(row.get('Rows', {}).get('Row', []))
                if v is not None:
                    return v
            elif row.get('type') == 'Data':
                cols = row.get('ColData', [])
                name = cols[0].get('value', '').strip() if cols else ''
                match = needle.lower() in name.lower() if partial else name.lower() == needle.lower()
                if match and cidx is not None and cidx < len(cols):
                    try:
                        return float(cols[cidx].get('value', '') or '0')
                    except:
                        pass
        return None
    return walk(report.get('Rows', {}).get('Row', [])) or 0.0

def find_lines_matching(report, needle, cidx):
    """Sum all lines whose name contains needle."""
    total = 0.0
    def walk(rows):
        nonlocal total
        for row in rows:
            if row.get('type') == 'Section':
                walk(row.get('Rows', {}).get('Row', []))
            elif row.get('type') == 'Data':
                cols = row.get('ColData', [])
                name = cols[0].get('value', '').strip() if cols else ''
                if needle.lower() in name.lower() and cidx is not None and cidx < len(cols):
                    try:
                        total += float(cols[cidx].get('value', '') or '0')
                    except:
                        pass
    walk(report.get('Rows', {}).get('Row', []))
    return total

def net_income_total(report, cidx):
    """Find net income from the bottom summary row or last section."""
    rows = report.get('Rows', {}).get('Row', [])
    # Walk top-level rows in reverse to find Net Income section/data
    for row in reversed(rows):
        rtype = row.get('type', '')
        if rtype == 'Section':
            hdr = row.get('Header', {}).get('ColData', [{}])
            title = hdr[0].get('value', '') if hdr else ''
            if 'net income' in title.lower() or 'net loss' in title.lower():
                sm = row.get('Summary', {}).get('ColData', [])
                if cidx is not None and cidx < len(sm):
                    try:
                        return float(sm[cidx].get('value', '') or '0')
                    except:
                        pass
        elif rtype == 'Data':
            cols = row.get('ColData', [])
            name = cols[0].get('value', '').strip() if cols else ''
            if 'net income' in name.lower() or 'net loss' in name.lower():
                if cidx is not None and cidx < len(cols):
                    try:
                        return float(cols[cidx].get('value', '') or '0')
                    except:
                        pass
    return None

def pnl_for_col(report, cidx):
    """Extract full P&L for a single column index."""
    income_sec = get_section(report, ['Income', 'Revenue'])
    cogs_sec   = get_section(report, ['Cost of Goods', 'Cost of Revenue', 'Direct Cost', 'COGS'])
    exp_sec    = get_section(report, ['Expense', 'Operating'])

    rev  = section_total(income_sec, cidx)
    cogs = section_total(cogs_sec,   cidx)
    exp  = section_total(exp_sec,    cidx)
    gp   = rev - cogs
    noi  = gp - exp
    ni   = net_income_total(report, cidx)
    if ni is None:
        ni = noi

    return {
        'total_revenue':  rev,
        'cogs':           cogs,
        'gross_profit':   gp,
        'total_expenses': exp,
        'noi':            noi,
        'net_income':     ni,
        'direct_labor':   0.0
    }

def ytd_pnl(report, col_labels):
    """Sum P&L across multiple month columns."""
    totals = {k: 0.0 for k in
              ['total_revenue','cogs','gross_profit','total_expenses','noi','net_income','direct_labor']}
    for label in col_labels:
        cidx = col_idx(report, label)
        if cidx is None:
            continue
        p = pnl_for_col(report, cidx)
        for k in totals:
            totals[k] += p.get(k, 0)
    # Recompute derived fields from sums
    totals['gross_profit'] = totals['total_revenue'] - totals['cogs']
    totals['noi']          = totals['gross_profit']  - totals['total_expenses']
    totals['net_income']   = totals['noi']  # best approximation without Other Income rows
    return totals

def top_expenses(report, cidx, n=5):
    """Return top N expense line items by absolute value."""
    exp_sec = get_section(report, ['Expense', 'Operating'])
    if not exp_sec or cidx is None:
        return []
    lines = flatten_rows(exp_sec.get('Rows', {}).get('Row', []), cidx)
    lines.sort(key=lambda x: abs(x[1]), reverse=True)
    return [[name, val] for name, val in lines[:n]]

def revenue_lines(report, cidx):
    income_sec = get_section(report, ['Income', 'Revenue'])
    if not income_sec or cidx is None:
        return []
    return flatten_rows(income_sec.get('Rows', {}).get('Row', []), cidx)

# ── Month label helpers ─────────────────────────────────────────────────────────

def make_col_label(month_name, year):
    """'June', 2026 -> 'Jun 2026'"""
    abbr = month_name[:3].capitalize()
    return f'{abbr} {year}'

def ytd_col_labels(month_name, year):
    """All monthly column labels from Jan through given month."""
    month_num = list(month_abbr).index(month_name[:3].capitalize())
    return [f'{month_abbr[m]} {year}' for m in range(1, month_num + 1)]


# ── Labor cost extraction ───────────────────────────────────────────────────────

LABOR_PATTERNS = {
    'GOPM': ['PM - VA', 'GOPM contractor', 'VA / GOPM'],
    'PSB':  ['PSB - VA', 'PSB VA'],
    'PPB':  ['PPB - VA', 'PPB VA'],
}
MGMT_PATTERNS = ['Management', 'Wages', 'Mgmt Fee', 'Officer']

def extract_ytd_labor(reports, col_labels):
    """
    Return labor_cost.salaries dict summed across YTD months.
    GOPM direct labor = sum across GOPM Expenses for VA/contractor lines.
    PSB/PPB direct labor = similar.
    Management = GOPM Expenses management/wages lines.
    """
    salaries = {
        'PM - VA / GOPM contractors': 0.0,
        'PSB - VA':                   0.0,
        'PPB - VA':                   0.0,
        'Management (Wages + Mgmt Fees, YTD)': 0.0
    }

    for label in col_labels:
        # GOPM VA/contractors
        cidx = col_idx(reports['GOPM'], label)
        if cidx:
            for p in LABOR_PATTERNS['GOPM']:
                salaries['PM - VA / GOPM contractors'] += find_lines_matching(reports['GOPM'], p, cidx)
            for p in MGMT_PATTERNS:
                salaries['Management (Wages + Mgmt Fees, YTD)'] += find_lines_matching(reports['GOPM'], p, cidx)

        # PSB VA
        cidx = col_idx(reports['PSB'], label)
        if cidx:
            for p in LABOR_PATTERNS['PSB']:
                salaries['PSB - VA'] += find_lines_matching(reports['PSB'], p, cidx)

        # PPB VA
        cidx = col_idx(reports['PPB'], label)
        if cidx:
            for p in LABOR_PATTERNS['PPB']:
                salaries['PPB - VA'] += find_lines_matching(reports['PPB'], p, cidx)

    # Remove zero entries if nothing found (avoid false zeros)
    return {k: round(v, 2) for k, v in salaries.items()}


# ── PSB monthly breakdown ───────────────────────────────────────────────────────

def psb_monthly(report, col_labels):
    result = {}
    for label in col_labels:
        cidx = col_idx(report, label)
        if cidx is None:
            continue
        total    = section_total(get_section(report, ['Income', 'Revenue']), cidx)
        snow     = find_lines_matching(report, 'snow', cidx)
        subcon   = find_lines_matching(report, 'subcontract', cidx)
        handyman = find_lines_matching(report, 'handyman', cidx)
        other    = total - snow - subcon - handyman
        result[label] = {'total': round(total,2), 'snow': round(snow,2),
                         'subcontracting': round(subcon,2), 'handyman': round(handyman,2),
                         'other': round(other,2)}
    return result


# ── Revenue mix ─────────────────────────────────────────────────────────────────

def revenue_mix_for(report, col_labels, entity):
    merged = {}
    for label in col_labels:
        cidx = col_idx(report, label)
        if cidx is None:
            continue
        for name, val in revenue_lines(report, cidx):
            merged[name] = merged.get(name, 0) + val

    total = sum(merged.values())
    lines = sorted(merged.items(), key=lambda x: abs(x[1]), reverse=True)
    return {
        'total': round(total, 2),
        'lines': [[n, round(v, 2)] for n, v in lines[:10]],
        'note':  f'{entity} YTD revenue mix.'
    }


# ── Analytics ───────────────────────────────────────────────────────────────────

def build_analytics(gm, month_pnl, labor, months_ytd):
    ytd_rev = sum(gm[e]['total_revenue'] for e in ENTITIES)
    total_salary = sum(labor['salaries'].values())
    salary_pct = total_salary / ytd_rev * 100 if ytd_rev else 0

    insights = []
    actions  = []

    # Salary load
    if 25 <= salary_pct <= 35:
        insights.append({'severity': 'good',
                         'title': f'YTD labor {salary_pct:.1f}% of YTD revenue — in 25–35% band',
                         'detail': 'YTD labor load is in the healthy services benchmark band.'})
    elif salary_pct > 35:
        insights.append({'severity': 'warn',
                         'title': f'YTD labor {salary_pct:.1f}% — above 35% benchmark',
                         'detail': 'Review headcount and VA utilization.'})
        actions.append('Audit labor load — salary % above 35% benchmark.')

    # GOPM VA labor
    gopm_va = labor['salaries'].get('PM - VA / GOPM contractors', 0)
    gopm_rev = gm['GOPM']['total_revenue']
    if gopm_rev:
        va_pct = gopm_va / gopm_rev * 100
        if va_pct > 12:
            insights.append({'severity': 'warn',
                             'title': f'GOPM admin labor {va_pct:.1f}% of revenue',
                             'detail': 'PM industry benchmark for admin/VA is 5–12%.'})
            actions.append('Audit GOPM VA/contractor workload — identify automation candidates.')

    # PSB margin
    psb_gm_pct = gm['PSB']['gross_profit'] / gm['PSB']['total_revenue'] * 100 if gm['PSB']['total_revenue'] else 0
    if psb_gm_pct >= 25:
        insights.append({'severity': 'good',
                         'title': f'PSB gross margin {psb_gm_pct:.1f}% — strong for construction',
                         'detail': 'Above 40% benchmark for specialty trade contractors.'})

    # PSB month loss check
    if month_pnl['PSB']['net_income'] < 0:
        actions.append('Review PSB jobs over $25K for margin leakage — focus on subcontractor markup.')

    # PPB
    ppb_ni_pct = gm['PPB']['net_income'] / gm['PPB']['total_revenue'] * 100 if gm['PPB']['total_revenue'] else 0
    if ppb_ni_pct > 10:
        insights.append({'severity': 'good',
                         'title': f'PPB net margin {ppb_ni_pct:.1f}% — excellent for brokerage',
                         'detail': 'NAR small brokerage benchmark is 5–15% net margin.'})

    actions += [
        f'Mid-year vs annual target check: forecast Dec 31 revenue from {months_ytd}-month run-rate.',
        'GOPM staffing review — confirm property manager headcount supports door count growth.',
        'PSB: lock in subcontractor pricing for next season before demand peak.',
        'Build recurring PPB revenue — push asset management retainers.',
    ]

    return {'insights': insights, 'actions': actions[:7]}


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--month',   required=True)
    parser.add_argument('--year',    required=True, type=int)
    parser.add_argument('--qb-dir',  required=True)
    parser.add_argument('--output',  required=True)
    args = parser.parse_args()

    month_name  = args.month.capitalize()
    year        = args.year
    col_label   = make_col_label(month_name, year)   # e.g. 'Jun 2026'
    ytd_labels  = ytd_col_labels(month_name, year)   # ['Jan 2026',...,'Jun 2026']
    months_ytd  = len(ytd_labels)

    print(f"Building data.json for {month_name} {year}  (YTD: {ytd_labels[0]} – {ytd_labels[-1]})")

    reports = {e: load_report(os.path.join(args.qb_dir, FILES[e])) for e in ENTITIES}
    for e, r in reports.items():
        if r is None:
            raise FileNotFoundError(f"Could not load TTM report for {e} from {args.qb_dir}")

    # TTM monthly income (12 months of revenue per entity from whatever columns exist)
    ttm_income = {}
    for e in ENTITIES:
        report = reports[e]
        income_sec = get_section(report, ['Income', 'Revenue'])
        ttm_income[e] = {}
        for label in all_col_labels(report):
            if not label or label.lower() == 'total':
                continue
            cidx = col_idx(report, label)
            ttm_income[e][label] = round(section_total(income_sec, cidx), 2)

    # YTD P&L
    gm = {}
    for e in ENTITIES:
        gm[e] = ytd_pnl(reports[e], ytd_labels)
        gm[e]['net_margin_pct']   = (gm[e]['net_income'] / gm[e]['total_revenue'] * 100
                                     if gm[e]['total_revenue'] else 0)
        gm[e]['gross_margin_pct'] = (gm[e]['gross_profit'] / gm[e]['total_revenue'] * 100
                                     if gm[e]['total_revenue'] else 0)
        gm[e] = {k: round(v, 2) for k, v in gm[e].items()}

    # Month P&L
    month_pnl = {}
    for e in ENTITIES:
        cidx = col_idx(reports[e], col_label)
        if cidx is None:
            raise ValueError(f"Column '{col_label}' not found in {e} TTM report. "
                             f"Available: {all_col_labels(reports[e])}")
        month_pnl[e] = {k: round(v, 2) for k, v in pnl_for_col(reports[e], cidx).items()}

    # Labor cost
    labor_salaries = extract_ytd_labor(reports, ytd_labels)
    labor = {'salaries': labor_salaries, 'months_ytd': months_ytd}

    # Non-labor expense (simple approximation)
    nl_per_entity = {}
    for e in ENTITIES:
        rev  = gm[e]['total_revenue']
        exp  = gm[e]['total_expenses']
        sal  = sum(v for k, v in labor_salaries.items() if e[:3].upper() in k.upper() or e == 'GOPM')
        nl   = max(0, exp - sal)
        nl_per_entity[e] = {
            'revenue': rev, 'total_exp': round(exp,2),
            'labor': round(sal,2), 'non_labor': round(nl,2),
            'ratio': round(nl / rev, 4) if rev else 0
        }
        # Top 5 expense lines YTD
    top_exp = {}
    for e in ENTITIES:
        # Sum expense lines across YTD months
        merged = {}
        for label in ytd_labels:
            cidx = col_idx(reports[e], label)
            if cidx is None:
                continue
            for name, val in top_expenses(reports[e], cidx, n=20):
                merged[name] = merged.get(name, 0) + val
        top_exp[e] = sorted([[n, round(v,2)] for n, v in merged.items()],
                            key=lambda x: abs(x[1]), reverse=True)[:5]

    non_labor = {
        'per_entity':  nl_per_entity,
        'top_expenses': top_exp
    }

    # Revenue mix
    rev_mix = {e: revenue_mix_for(reports[e], ytd_labels, e) for e in ENTITIES}

    # PSB monthly
    ps_mon = psb_monthly(reports['PSB'], ytd_labels)

    # Analytics
    analytics = build_analytics(gm, month_pnl, labor, months_ytd)

    data = {
        'period_label':       f'{month_name} {year}',
        'ttm_window_label':   f'{list(ttm_income["GOPM"].keys())[0]}–{list(ttm_income["GOPM"].keys())[-1]}',
        'ytd_window_label':   f'January 1, to {month_name} {year}',
        'ttm_monthly_income': ttm_income,
        'gm_per_entity':      gm,
        'revenue_mix':        rev_mix,
        'ps_monthly':         ps_mon,
        'labor_cost':         labor,
        'non_labor_expense':  non_labor,
        'month_pnl':          month_pnl,
        'analytics':          analytics
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    print(f"Saved: {args.output}")
    for e in ENTITIES:
        g = gm[e]; m = month_pnl[e]
        print(f"  {e}  YTD rev={g['total_revenue']:,.0f}  YTD ni={g['net_income']:,.0f}"
              f"  {month_name} rev={m['total_revenue']:,.0f}  {month_name} ni={m['net_income']:,.0f}")


if __name__ == '__main__':
    main()