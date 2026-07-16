"""
Build data.json for May 2026 (and a minimal April prior) from the June TTM files.
Strategy:
  - May YTD  = June YTD (from data.json) minus June month_pnl
  - May/Apr month P&L = parsed from TTM columns
  - Labor, TTM income, analytics copied/adapted from June data.json
"""
import json, os, shutil

QB_JUNE = r"C:\Users\vcheu\Claude Folder\financial_dashboard\qb_june_2026"
MAY_DIR = r"C:\Users\vcheu\Claude Folder\financial_dashboard\qb_may_2026"
APR_DIR = r"C:\Users\vcheu\Claude Folder\financial_dashboard\qb_april_2026"

os.makedirs(MAY_DIR, exist_ok=True)
os.makedirs(APR_DIR, exist_ok=True)

june = json.load(open(os.path.join(QB_JUNE, 'data.json'), encoding='utf-8'))

# May TTM files (real May pull, dated June 3) — use THESE for May P&L extraction
# Do NOT use June TTM for May figures (QB revised May after close)
MAY_TTM_DIR = MAY_DIR  # qb_may_2026 already has the real May TTM files

ENTITIES = ['GOPM', 'PSB', 'PPB']
FILES = {'GOPM': 'gopm_ttm.json', 'PSB': 'psb_ttm.json', 'PPB': 'ppb_ttm.json'}

# ── TTM helpers ────────────────────────────────────────────────────────────────
def load_qb(path):
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    for item in raw:
        if item.get('type') == 'text' and item.get('text', '').startswith('{'):
            return json.loads(item['text'])
    return None

def col_idx(report, label):
    for i, c in enumerate(report['Columns']['Column']):
        if c.get('ColTitle', '') == label:
            return i
    return None

def section_total(report, keywords, cidx):
    for row in report.get('Rows', {}).get('Row', []):
        if row.get('type') != 'Section':
            continue
        hdr = row.get('Header', {}).get('ColData', [{}])
        title = hdr[0].get('value', '') if hdr else ''
        if any(k.lower() in title.lower() for k in keywords):
            sm = row.get('Summary', {}).get('ColData', [])
            if cidx is not None and cidx < len(sm):
                try:
                    return float(sm[cidx].get('value', '') or '0')
                except:
                    return 0.0
    return 0.0

def net_income_val(report, cidx):
    """Walk all top-level rows for a Net Income summary or data row."""
    for row in report.get('Rows', {}).get('Row', []):
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

def find_line(report, needle, cidx):
    """Find first data row containing needle (case-insensitive)."""
    def walk(rows):
        for row in rows:
            if row.get('type') == 'Section':
                v = walk(row.get('Rows', {}).get('Row', []))
                if v is not None:
                    return v
            elif row.get('type') == 'Data':
                cols = row.get('ColData', [])
                name = cols[0].get('value', '').strip() if cols else ''
                if needle.lower() in name.lower() and cidx is not None and cidx < len(cols):
                    try:
                        return float(cols[cidx].get('value', '') or '0')
                    except:
                        pass
        return None
    return walk(report.get('Rows', {}).get('Row', [])) or 0.0

# ── Extract month P&L from TTM ─────────────────────────────────────────────────
def month_pnl(report, month_label):
    cidx = col_idx(report, month_label)
    rev  = section_total(report, ['Income', 'Revenue'], cidx)
    cogs = section_total(report, ['Cost of Goods', 'Direct Cost'], cidx)
    exp  = section_total(report, ['Expense', 'Operating'], cidx)
    gp   = rev - cogs
    noi  = gp - exp
    ni   = net_income_val(report, cidx)
    if ni is None:
        ni = noi
    return {
        'total_revenue': rev,
        'cogs':          cogs,
        'gross_profit':  gp,
        'total_expenses': exp,
        'noi':           noi,
        'net_income':    ni,
        'direct_labor':  0.0
    }

def ytd_pnl(report, month_labels):
    """Sum P&L across multiple month columns."""
    totals = {k: 0.0 for k in ['total_revenue','cogs','gross_profit','total_expenses','noi','net_income','direct_labor']}
    for label in month_labels:
        p = month_pnl(report, label)
        for k in totals:
            totals[k] += p[k]
    totals['gross_profit'] = totals['total_revenue'] - totals['cogs']
    totals['noi']          = totals['gross_profit']  - totals['total_expenses']
    totals['net_income']   = totals['noi']
    return totals

YTD_MAY_LABELS = ['Jan 2026','Feb 2026','Mar 2026','Apr 2026','May 2026']

# ── May and April month P&L from the REAL May TTM files (June 3 pull) ─────────
# These reflect May as it was when closed — includes renewal fee in May revenue
may_month_pnl = {}
apr_month_pnl = {}
may_gm        = {}
for e in ENTITIES:
    report = load_qb(os.path.join(MAY_TTM_DIR, FILES[e]))
    may_month_pnl[e] = month_pnl(report, 'May 2026')
    apr_month_pnl[e] = month_pnl(report, 'Apr 2026')
    may_gm[e]        = ytd_pnl(report, YTD_MAY_LABELS)
    may_gm[e]['net_margin_pct']   = (may_gm[e]['net_income'] / may_gm[e]['total_revenue'] * 100
                                     if may_gm[e]['total_revenue'] else 0)
    may_gm[e]['gross_margin_pct'] = (may_gm[e]['gross_profit'] / may_gm[e]['total_revenue'] * 100
                                     if may_gm[e]['total_revenue'] else 0)

print("May month P&L:")
for e in ENTITIES:
    p = may_month_pnl[e]
    print(f"  {e}: rev={p['total_revenue']:,.0f}  ni={p['net_income']:,.0f}")

print("April month P&L:")
for e in ENTITIES:
    p = apr_month_pnl[e]
    print(f"  {e}: rev={p['total_revenue']:,.0f}  ni={p['net_income']:,.0f}")

# ── Labor cost: May YTD = June YTD - June month labor (approx 1/6 of YTD) ───
# Approx by using (5/6) of June YTD labor
j_sal = june['labor_cost']['salaries']
may_sal = {k: v * 5 / 6 for k, v in j_sal.items()}

# ── April YTD revenue (for salary load comparison) ────────────────────────────
apr_ytd_rev = {e: may_gm[e]['total_revenue'] - may_month_pnl[e]['total_revenue'] for e in ENTITIES}
apr_sal = {k: v * 4 / 6 for k, v in j_sal.items()}

# ── non_labor_expense (scale June by 5/6) ─────────────────────────────────────
def scale_nl(nl_entity, factor):
    return {k: v * factor if isinstance(v, (int, float)) else v
            for k, v in nl_entity.items()}

may_nl_per_entity = {}
for e in ENTITIES:
    base = june['non_labor_expense']['per_entity'][e]
    may_nl_per_entity[e] = {
        'revenue':    may_gm[e]['total_revenue'],
        'total_exp':  base['total_exp']  * 5/6,
        'labor':      base['labor']      * 5/6,
        'non_labor':  base['non_labor']  * 5/6,
        'ratio':      base['non_labor'] * 5/6 / may_gm[e]['total_revenue'] if may_gm[e]['total_revenue'] else 0
    }
may_nl_combined = {
    'revenue':   sum(may_gm[e]['total_revenue'] for e in ENTITIES),
    'total_exp': sum(may_nl_per_entity[e]['total_exp'] for e in ENTITIES),
    'labor':     sum(may_nl_per_entity[e]['labor']     for e in ENTITIES),
    'non_labor': sum(may_nl_per_entity[e]['non_labor'] for e in ENTITIES),
}
may_nl_combined['ratio'] = (may_nl_combined['non_labor'] / may_nl_combined['revenue']
                            if may_nl_combined['revenue'] else 0)

# ── Build May data.json ────────────────────────────────────────────────────────
may_data = {
    'period_label':       'May 2026',
    'ttm_window_label':   june['ttm_window_label'],
    'ytd_window_label':   'January 1, to May 31, 2026',
    'ttm_monthly_income': june['ttm_monthly_income'],
    'ttm_notes':          june.get('ttm_notes', {}),
    'gm_per_entity':      may_gm,
    'revenue_mix':        june.get('revenue_mix', {}),
    'ps_monthly':         {k: v for k, v in june.get('ps_monthly', {}).items()
                           if k not in ['Jun 2026']},
    'labor_cost': {
        'targets':    june['labor_cost']['targets'],
        'salaries':   may_sal,
        'months_ytd': 5
    },
    'non_labor_expense': {
        'per_entity':  dict(may_nl_per_entity, combined=may_nl_combined),
        'top_expenses': june['non_labor_expense']['top_expenses']
    },
    'month_pnl':  may_month_pnl,
    'analytics': {
        'insights': june['analytics']['insights'],
        'actions':  june['analytics']['actions']
    }
}

out_path = os.path.join(MAY_DIR, 'data.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(may_data, f, indent=2)
print(f"\nSaved: {out_path}")

# ── Build minimal April data.json (used as --prior for May report) ─────────────
apr_gm = {}
for e in ENTITIES:
    apr_gm[e] = {
        'total_revenue': apr_ytd_rev[e],
        'net_income':    may_gm[e]['net_income'] - may_month_pnl[e]['net_income'],
        'cogs':          0, 'gross_profit': 0,
        'total_expenses': 0, 'noi': 0, 'direct_labor': 0
    }

apr_data = {
    'period_label':       'April 2026',
    'ttm_monthly_income': june['ttm_monthly_income'],
    'gm_per_entity':      apr_gm,
    'month_pnl':          apr_month_pnl,
    'labor_cost': {
        'targets':    june['labor_cost']['targets'],
        'salaries':   apr_sal,
        'months_ytd': 4
    },
    'non_labor_expense': june['non_labor_expense'],
    'analytics':         june['analytics']
}

apr_path = os.path.join(APR_DIR, 'data.json')
with open(apr_path, 'w', encoding='utf-8') as f:
    json.dump(apr_data, f, indent=2)
print(f"Saved: {apr_path}")