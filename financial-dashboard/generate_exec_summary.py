"""
Generate executive summary HTML from financial dashboard data.json + QuickBooks TTM files.
Usage:
  python generate_exec_summary.py
    --data   <path/to/current/data.json>
    --prior  <path/to/prior/data.json>
    --qb-dir <path/to/qb_month_year/>
    --output <path/to/output.html>
    --month  <Month>  --year <YYYY>
"""

import json
import argparse
import os
from datetime import datetime, timedelta


# ── helpers ──────────────────────────────────────────────────────────────────

def fmt_dollar(v, decimals=0):
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v:,.{decimals}f}"
    return f"${v:.{decimals}f}"

def fmt_pct(v):
    return f"{v:.1f}%"

def mom_pct(current, prior):
    if prior == 0:
        return 0
    return (current - prior) / prior * 100


# ── QuickBooks TTM analysis ───────────────────────────────────────────────────

def load_qb_report(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    for item in raw:
        if item.get('type') == 'text' and item.get('text', '').startswith('{'):
            return json.loads(item['text'])
    return None

def get_col_index(report, month_label):
    for i, col in enumerate(report['Columns']['Column']):
        if col.get('ColTitle', '') == month_label:
            return i
    return None

def flatten_rows(rows, col_idx, results=None):
    if results is None:
        results = []
    for row in rows:
        rtype = row.get('type', '')
        if rtype == 'Section':
            flatten_rows(row.get('Rows', {}).get('Row', []), col_idx, results)
        elif rtype == 'Data':
            cols = row.get('ColData', [])
            name = cols[0].get('value', '').strip() if cols else ''
            if col_idx < len(cols):
                try:
                    val = float(cols[col_idx].get('value', '') or '0')
                    if name and abs(val) > 0.01:
                        results.append((name, val))
                except:
                    pass
    return results

def get_section(report, keywords):
    for row in report.get('Rows', {}).get('Row', []):
        if row.get('type') == 'Section':
            header = row.get('Header', {}).get('ColData', [{}])
            title = header[0].get('value', '') if header else ''
            if any(k.lower() in title.lower() for k in keywords):
                return row
    return None

def section_total(section, col_idx):
    if not section:
        return 0
    summary = section.get('Summary', {}).get('ColData', [])
    if col_idx < len(summary):
        try:
            return float(summary[col_idx].get('value', '0') or '0')
        except:
            return 0
    return 0

def top_movers(section, may_idx, jun_idx, n=3, direction='down'):
    if not section:
        return []
    rows = section.get('Rows', {}).get('Row', [])
    may_lines = {}
    for name, val in flatten_rows(rows, may_idx):
        may_lines[name] = may_lines.get(name, 0) + val
    jun_lines = {}
    for name, val in flatten_rows(rows, jun_idx):
        jun_lines[name] = jun_lines.get(name, 0) + val

    all_names = set(list(may_lines.keys()) + list(jun_lines.keys()))
    diffs = []
    for name in all_names:
        m = may_lines.get(name, 0)
        j = jun_lines.get(name, 0)
        delta = j - m
        if abs(delta) > 200:
            diffs.append((name, m, j, delta))

    if direction == 'down':
        diffs.sort(key=lambda x: x[3])
    else:
        diffs.sort(key=lambda x: -x[3])

    return diffs[:n]


def gone_quiet_lines(section, all_col_indices, current_col_idx, min_ttm_avg=300):
    """
    Find income lines that are $0 in the current month but had a non-zero
    TTM average — revenue streams that have quietly disappeared.
    """
    if not section:
        return []
    rows = section.get('Rows', {}).get('Row', [])

    # Build TTM totals and current month value per line
    ttm_lines = {}
    cur_lines = {}
    for col_i in all_col_indices:
        for name, val in flatten_rows(rows, col_i):
            ttm_lines[name] = ttm_lines.get(name, 0) + val
    for name, val in flatten_rows(rows, current_col_idx):
        cur_lines[name] = cur_lines.get(name, 0) + val

    n_months = len(all_col_indices)
    quiet = []
    for name, ttm_total in ttm_lines.items():
        avg = ttm_total / n_months
        cur = cur_lines.get(name, 0)
        if cur == 0 and avg >= min_ttm_avg:
            quiet.append((name, avg, ttm_total))

    quiet.sort(key=lambda x: -x[1])  # highest avg first
    return quiet

def shorten(name, maxlen=45):
    """Strip QB account codes (e.g. '104007 ') and truncate."""
    import re
    name = re.sub(r'^\d{5,6}\s+', '', name).strip()
    if len(name) > maxlen:
        name = name[:maxlen-1] + '…'
    return name

def analyze_why_revenue(qb_dir, entity_file, may_label, jun_label, prior_qb_dir=None):
    """
    Explains revenue changes only — income section movers plus lines that
    have gone quiet (zero this month, non-zero TTM avg).

    If prior_qb_dir is provided, does a cross-report comparison: prior TTM's
    prior-month column vs current TTM's current-month column. This captures
    revenue that was reported in the prior month's QB file but revised away
    by the time the current month's file was pulled (e.g. renewal fee batches).

    Returns (bullets_list, has_cogs_story).
    """
    path = os.path.join(qb_dir, entity_file)
    report = load_qb_report(path)
    if not report:
        return ["Raw QuickBooks data not available."], False

    jun_idx = get_col_index(report, jun_label)
    if jun_idx is None:
        return [f"Could not locate {jun_label} in QuickBooks TTM report."], False

    income_sec = get_section(report, ['Income', 'Revenue'])
    cogs_sec   = get_section(report, ['Cost of', 'COGS', 'Direct'])
    cogs_jun   = section_total(cogs_sec, jun_idx)

    # All TTM column indices (skip col 0 = label, last = Total)
    all_cols = report['Columns']['Column']
    ttm_indices = [i for i, c in enumerate(all_cols)
                   if c.get('ColTitle', '') not in ('', 'Total')]

    bullets = []

    if prior_qb_dir:
        # Cross-report: prior TTM's may_label column vs current TTM's jun_label column
        prior_path = os.path.join(prior_qb_dir, entity_file)
        prior_report = load_qb_report(prior_path)
        if prior_report:
            may_idx_in_prior = get_col_index(prior_report, may_label)
            if may_idx_in_prior is not None:
                prior_income_sec = get_section(prior_report, ['Income', 'Revenue'])
                income_may = section_total(prior_income_sec, may_idx_in_prior)
                income_jun = section_total(income_sec, jun_idx)
                income_delta = income_jun - income_may

                # Build movers from cross-report line comparison
                prior_rows = prior_income_sec.get('Rows', {}).get('Row', []) if prior_income_sec else []
                cur_rows   = income_sec.get('Rows', {}).get('Row', []) if income_sec else []
                prior_lines = {}
                for n, v in flatten_rows(prior_rows, may_idx_in_prior):
                    prior_lines[n] = prior_lines.get(n, 0) + v
                cur_lines = {}
                for n, v in flatten_rows(cur_rows, jun_idx):
                    cur_lines[n] = cur_lines.get(n, 0) + v

                all_names = set(list(prior_lines.keys()) + list(cur_lines.keys()))
                cross_diffs = []
                for name in all_names:
                    m = prior_lines.get(name, 0)
                    j = cur_lines.get(name, 0)
                    d = j - m
                    if abs(d) > 200:
                        cross_diffs.append((name, m, j, d))
                cross_diffs.sort(key=lambda x: x[3])

                if income_delta < -500:
                    bullets.append(
                        f"Revenue fell {fmt_dollar(abs(income_delta))} MoM "
                        f"({fmt_dollar(income_may)} → {fmt_dollar(income_jun)})."
                    )
                    for name, m, j, d in cross_diffs[:4]:
                        if d < -300:
                            bullets.append(f"• {shorten(name)}: {fmt_dollar(m)} → {fmt_dollar(j)} ({fmt_dollar(d)})")
                elif income_delta > 500:
                    bullets.append(
                        f"Revenue rose {fmt_dollar(income_delta)} MoM "
                        f"({fmt_dollar(income_may)} → {fmt_dollar(income_jun)})."
                    )
                    for name, m, j, d in reversed(cross_diffs[-3:]):
                        if d > 300:
                            bullets.append(f"• {shorten(name)}: {fmt_dollar(m)} → {fmt_dollar(j)} (+{fmt_dollar(d)})")
            else:
                prior_qb_dir = None  # fall through to within-report comparison

    if not prior_qb_dir:
        # Within-report comparison: May vs June columns inside the current TTM
        may_idx = get_col_index(report, may_label)
        if may_idx is None:
            return [f"Could not locate {may_label} / {jun_label} in QuickBooks TTM report."], False

        income_may = section_total(income_sec, may_idx)
        income_jun = section_total(income_sec, jun_idx)
        cogs_may   = section_total(cogs_sec,   may_idx)
        income_delta = income_jun - income_may

        rev_drops = top_movers(income_sec, may_idx, jun_idx, n=4, direction='down')
        rev_gains = top_movers(income_sec, may_idx, jun_idx, n=3, direction='up')

        if income_delta < -500:
            bullets.append(
                f"Revenue fell {fmt_dollar(abs(income_delta))} MoM "
                f"({fmt_dollar(income_may)} → {fmt_dollar(income_jun)})."
            )
            for name, m, j, d in rev_drops:
                if d < -300:
                    bullets.append(f"• {shorten(name)}: {fmt_dollar(m)} → {fmt_dollar(j)} ({fmt_dollar(d)})")
        elif income_delta > 500:
            bullets.append(
                f"Revenue rose {fmt_dollar(income_delta)} MoM "
                f"({fmt_dollar(income_may)} → {fmt_dollar(income_jun)})."
            )
            for name, m, j, d in rev_gains:
                if d > 300:
                    bullets.append(f"• {shorten(name)}: {fmt_dollar(m)} → {fmt_dollar(j)} (+{fmt_dollar(d)})")

    # Revenue lines that have gone quiet (zero this month, non-zero TTM avg)
    quiet = gone_quiet_lines(income_sec, ttm_indices, jun_idx, min_ttm_avg=250)
    if quiet:
        bullets.append("Revenue lines now at $0 (were active earlier in TTM):")
        for name, avg, total in quiet[:4]:
            bullets.append(f"• {shorten(name)}: TTM avg {fmt_dollar(avg)}/mo — $0 this month")

    has_cogs_story = cogs_jun > 500

    return bullets if bullets else ["No significant revenue line-item changes."], has_cogs_story


def analyze_why_margin(qb_dir, entity_file, may_label, jun_label):
    """
    Explains margin / loss month — COGS movers only. No revenue, no expenses.
    """
    path = os.path.join(qb_dir, entity_file)
    report = load_qb_report(path)
    if not report:
        return []

    may_idx = get_col_index(report, may_label)
    jun_idx = get_col_index(report, jun_label)
    if may_idx is None or jun_idx is None:
        return []

    cogs_sec = get_section(report, ['Cost of', 'COGS', 'Direct'])
    cogs_may = section_total(cogs_sec, may_idx)
    cogs_jun = section_total(cogs_sec, jun_idx)
    cogs_delta = cogs_jun - cogs_may

    if cogs_delta < 500:
        return []

    bullets = [
        f"COGS increased {fmt_dollar(cogs_delta)} MoM "
        f"({fmt_dollar(cogs_may)} → {fmt_dollar(cogs_jun)}), compressing margin."
    ]
    for name, m, j, d in top_movers(cogs_sec, may_idx, jun_idx, n=4, direction='up'):
        if d > 300:
            bullets.append(f"• {shorten(name)}: {fmt_dollar(m)} → {fmt_dollar(j)} (+{fmt_dollar(d)})")

    return bullets


# ── TTM chart + analysis ─────────────────────────────────────────────────────

def build_ttm_section(ttm_data, entities, entity_labels, entity_dots, month, year):
    """
    Returns HTML for a TTM revenue chart (Chart.js) + written analysis bullets.
    ttm_data: dict of {entity: {month_label: value, ...}} — 12 months in order.
    """
    months = list(ttm_data[entities[0]].keys())

    # Per-entity stats
    stats = {}
    for e in entities:
        vals = [ttm_data[e].get(m, 0) for m in months]
        ttm_total = sum(vals)
        peak_i  = vals.index(max(vals))
        trough_i = vals.index(min(vals))
        recent_avg = sum(vals[-3:]) / 3
        mid_avg    = sum(vals[-6:-3]) / 3
        trend_pct  = (recent_avg - mid_avg) / mid_avg * 100 if mid_avg else 0
        stats[e] = {
            'vals': vals,
            'ttm_total': ttm_total,
            'monthly_avg': ttm_total / len(vals),
            'peak_month': months[peak_i],
            'peak_val': vals[peak_i],
            'trough_month': months[trough_i],
            'trough_val': vals[trough_i],
            'trend_pct': trend_pct,
            'recent_avg': recent_avg,
        }

    portfolio_vals = [sum(ttm_data[e].get(m, 0) for e in entities) for m in months]
    port_recent = sum(portfolio_vals[-3:]) / 3
    port_mid    = sum(portfolio_vals[-6:-3]) / 3
    port_trend  = (port_recent - port_mid) / port_mid * 100 if port_mid else 0

    # Chart.js dataset JSON
    import json as _json
    labels_js = _json.dumps(months)
    colors = {'GOPM': '#2563eb', 'PSB': '#16a34a', 'PPB': '#d97706'}
    datasets_js = ',\n'.join(
        f'{{"label":"{entity_labels[e]}","data":{_json.dumps(stats[e]["vals"])},'
        f'"borderColor":"{colors[e]}","backgroundColor":"{colors[e]}22",'
        f'"borderWidth":2,"pointRadius":3,"tension":0.3,"fill":false}}'
        for e in entities
    )

    # Analysis bullets
    bullets = []
    trend_word = 'accelerating' if port_trend > 5 else ('softening' if port_trend < -5 else 'stable')
    bullets.append(
        f"Portfolio revenue is <strong>{trend_word}</strong> — "
        f"last 3-month avg {fmt_dollar(port_recent)}/mo vs prior 3-month avg {fmt_dollar(port_mid)}/mo "
        f"({'▲' if port_trend >= 0 else '▼'} {abs(port_trend):.1f}%)."
    )

    for e in entities:
        s = stats[e]
        td = s['trend_pct']
        direction = '▲' if td >= 0 else '▼'
        bullets.append(
            f"<span style='font-weight:600;color:{colors[e]};'>{entity_labels[e]}:</span> "
            f"TTM total {fmt_dollar(s['ttm_total'])} &middot; avg {fmt_dollar(s['monthly_avg'])}/mo &middot; "
            f"peak {s['peak_month']} ({fmt_dollar(s['peak_val'])}) &middot; "
            f"recent trend {direction} {abs(td):.1f}%"
        )

    bullets_html = ''.join(f'<li style="padding:5px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155;">{b}</li>' for b in bullets)

    # Monthly data table
    table_rows = ""
    for i, m in enumerate(months):
        row_vals = [ttm_data[e].get(m, 0) for e in entities]
        row_total = sum(row_vals)
        cells = "".join(f'<td style="text-align:right;">{fmt_dollar(v)}</td>' for v in row_vals)
        table_rows += f'<tr><td style="font-weight:500;color:#475569;">{m}</td>{cells}<td style="text-align:right;font-weight:600;color:#0f172a;">{fmt_dollar(row_total)}</td></tr>'

    # Totals row
    entity_totals = [stats[e]['ttm_total'] for e in entities]
    grand_total = sum(entity_totals)
    total_cells = "".join(f'<td style="text-align:right;">{fmt_dollar(t)}</td>' for t in entity_totals)
    table_rows += f'<tr class="total"><td>TTM Total</td>{total_cells}<td style="text-align:right;">{fmt_dollar(grand_total)}</td></tr>'

    header_cells = "".join(
        f'<th style="text-align:right;color:{colors[e]};">{entity_labels[e]}</th>'
        for e in entities
    )

    return f'''
  <div class="card">
    <div class="card-header">TTM Revenue by Entity &mdash; {months[0]} through {months[-1]}</div>
    <div style="padding:16px 18px;">
      <canvas id="ttmChart" height="90"></canvas>
    </div>
    <div style="overflow-x:auto;">
      <table>
        <thead><tr><th>Month</th>{header_cells}<th style="text-align:right;">Portfolio Total</th></tr></thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
    <div style="padding:12px 18px 16px;border-top:1px solid #f8fafc;">
      <ul style="list-style:none;margin:0;padding:0;">{bullets_html}</ul>
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script>
    new Chart(document.getElementById('ttmChart'), {{
      type: 'line',
      data: {{
        labels: {labels_js},
        datasets: [{datasets_js}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position: 'top', labels: {{ font: {{ size: 12 }}, boxWidth: 12 }} }},
          tooltip: {{
            callbacks: {{
              label: ctx => ' $' + ctx.parsed.y.toLocaleString('en-US', {{maximumFractionDigits:0}})
            }}
          }}
        }},
        scales: {{
          y: {{
            ticks: {{
              callback: v => '$' + (v >= 1000 ? (v/1000).toFixed(0)+'K' : v)
            }},
            grid: {{ color: '#f1f5f9' }}
          }},
          x: {{ grid: {{ display: false }} }}
        }}
      }}
    }});
  </script>'''


# ── RAG status ────────────────────────────────────────────────────────────────

def rag_status(entity, mp_cur, mp_prior, gopm_va_pct=None):
    rev_mom     = mom_pct(mp_cur['total_revenue'], mp_prior['total_revenue'])
    ni_pct_cur  = mp_cur['net_income']  / mp_cur['total_revenue']  * 100 if mp_cur['total_revenue']  else 0
    ni_pct_pri  = mp_prior['net_income'] / mp_prior['total_revenue'] * 100 if mp_prior['total_revenue'] else 0
    ni_compress = ni_pct_pri - ni_pct_cur

    alerts_red, alerts_amber = [], []

    if mp_cur['net_income'] < 0:
        alerts_red.append(('loss_month', ni_pct_cur, rev_mom))
    if rev_mom < -30:
        alerts_red.append(('rev_drop_red', ni_pct_cur, rev_mom))
    if ni_compress > 15 and mp_cur['net_income'] >= 0:
        alerts_amber.append(('margin_compress', ni_compress, rev_mom))
    if -30 <= rev_mom < -15:
        alerts_amber.append(('rev_drop_amber', ni_pct_cur, rev_mom))
    if entity == 'GOPM' and gopm_va_pct and gopm_va_pct > 12:
        alerts_amber.append(('va_labor', gopm_va_pct, rev_mom))

    if alerts_red:
        return ('red', 'ACTION', alerts_red + alerts_amber)
    elif alerts_amber:
        return ('amber', 'WATCH', alerts_amber)
    else:
        return ('green', 'HEALTHY', [])


# ── HTML generation ───────────────────────────────────────────────────────────

def generate_html(data, prior_data, month, year, report_date, qb_dir=None, prior_qb_dir=None, ar_aging=None, prev_months=None):
    gm = data['gm_per_entity']
    mp = data['month_pnl']
    mp_prior = prior_data['month_pnl']
    lc = data['labor_cost']
    nl = data['non_labor_expense']
    an = data['analytics']

    entities = ['GOPM', 'PSB', 'PPB']
    entity_labels = {
        'GOPM': 'Green Ocean PM',
        'PSB':  'Pro Services Boston',
        'PPB':  'Profitable Properties'
    }
    entity_dots = {
        'GOPM': '#2563eb',
        'PSB':  '#16a34a',
        'PPB':  '#d97706'
    }
    qb_files = {
        'GOPM': 'gopm_ttm.json',
        'PSB':  'psb_ttm.json',
        'PPB':  'ppb_ttm.json'
    }

    # Column labels come from the REPORT month args, not today
    from calendar import month_abbr
    month_num      = list(month_abbr).index(month[:3].capitalize())
    report_month_dt = datetime(year, month_num, 1)
    prior_month_dt  = (report_month_dt - timedelta(days=1)).replace(day=1)
    jun_label = report_month_dt.strftime('%b %Y')   # e.g. "Jun 2026"
    may_label = prior_month_dt.strftime('%b %Y')    # e.g. "May 2026"

    # Portfolio YTD
    ytd_rev = sum(gm[e]['total_revenue'] for e in entities)
    ytd_ni  = sum(gm[e]['net_income']    for e in entities)
    ytd_net_margin = ytd_ni / ytd_rev * 100 if ytd_rev else 0

    # Salary load (current + prior month for comparison)
    prior_lc      = prior_data['labor_cost']
    total_salary  = sum(lc['salaries'].values())
    prior_salary  = sum(prior_lc['salaries'].values())
    salary_pct    = total_salary / ytd_rev * 100 if ytd_rev else 0
    prior_ytd_rev = sum(prior_data['gm_per_entity'][e]['total_revenue'] for e in entities)
    prior_salary_pct = prior_salary / prior_ytd_rev * 100 if prior_ytd_rev else 0
    salary_verdict = ('On track' if 25 <= salary_pct <= 35
                      else ('High — review' if salary_pct > 35 else 'Below benchmark'))
    salary_color  = 'green' if 25 <= salary_pct <= 35 else 'red'

    # Current month combined
    month_rev_cur   = sum(mp[e]['total_revenue']  for e in entities)
    month_rev_prior = sum(mp_prior[e]['total_revenue'] for e in entities)
    month_rev_mom   = mom_pct(month_rev_cur, month_rev_prior)

    # GOPM VA labor %
    gopm_va     = lc['salaries'].get('PM - VA / GOPM contractors', 0)
    gopm_va_pct = gopm_va / gm['GOPM']['total_revenue'] * 100 if gm['GOPM']['total_revenue'] else 0

    # Entity status
    statuses   = {}
    rev_moms   = {}
    ni_pcts    = {}
    for e in entities:
        va_pct      = gopm_va_pct if e == 'GOPM' else None
        status      = rag_status(e, mp[e], mp_prior[e], va_pct)
        statuses[e] = status
        rev_moms[e] = mom_pct(mp[e]['total_revenue'], mp_prior[e]['total_revenue'])
        ni_pcts[e]  = (mp[e]['net_income'] / mp[e]['total_revenue'] * 100
                       if mp[e]['total_revenue'] else 0)

    # EOY forecast
    months_elapsed = lc.get('months_ytd', 6)
    eoy = {e: gm[e]['total_revenue'] * 12 / months_elapsed for e in entities}
    eoy_total = sum(eoy.values())

    # Actions & insights
    actions  = an.get('actions', [])

    # Due dates
    due_red   = (report_date + timedelta(days=15)).strftime('%b %d')
    due_amber = (report_date + timedelta(days=30)).strftime('%b %d')

    # ── WHY analysis from QB TTM files ──
    why_rev    = {}   # revenue-only bullets
    why_margin = {}   # COGS-only bullets
    if qb_dir:
        for e in entities:
            rev_bullets, _ = analyze_why_revenue(
                qb_dir, qb_files[e], may_label, jun_label,
                prior_qb_dir=prior_qb_dir
            )
            why_rev[e]     = rev_bullets
            why_margin[e]  = analyze_why_margin(qb_dir, qb_files[e], may_label, jun_label)
    else:
        for e in entities:
            why_rev[e]    = []
            why_margin[e] = []

    # ── Alert banners with WHY ──
    banner_html = ""
    for e in entities:
        color, label, alert_list = statuses[e]
        if not alert_list:
            continue

        rev = mp[e]['total_revenue']
        rev_p = mp_prior[e]['total_revenue']
        ni_p = (mp[e]['net_income'] / rev * 100) if rev else 0
        ni_pp = (mp_prior[e]['net_income'] / rev_p * 100) if rev_p else 0

        # Build title from alert type
        titles = []
        for alert in alert_list:
            atype = alert[0]
            if atype == 'loss_month':
                titles.append(f"{entity_labels[e]} recorded a loss month "
                               f"(net margin {fmt_pct(ni_p)}, net income {fmt_dollar(mp[e]['net_income'])})")
            elif atype == 'rev_drop_red':
                titles.append(f"{entity_labels[e]} revenue dropped {abs(rev_moms[e]):.0f}% MoM "
                               f"({fmt_dollar(rev_p)} → {fmt_dollar(rev)})")
            elif atype == 'rev_drop_amber':
                titles.append(f"{entity_labels[e]} revenue down {abs(rev_moms[e]):.0f}% MoM "
                               f"({fmt_dollar(rev_p)} → {fmt_dollar(rev)})")
            elif atype == 'margin_compress':
                titles.append(f"{entity_labels[e]} net margin compressed "
                               f"{alert[1]:.0f}pp MoM ({fmt_pct(ni_pp)} → {fmt_pct(ni_p)})")
            elif atype == 'va_labor':
                titles.append(f"{entity_labels[e]} admin/VA labor at {fmt_pct(alert[1])} of revenue "
                               f"(benchmark 5–12%)")

        icon = "&#9888;" if color == 'red' else "&#9650;"

        def bullets_to_html(bullets):
            if not bullets:
                return ""
            items = ""
            for b in bullets:
                if b.startswith("•"):
                    items += f'<li style="margin-left:16px;list-style:disc;">{b[1:].strip()}</li>'
                else:
                    items += f'<li style="margin-top:6px;font-weight:600;">{b}</li>'
            return f'<ul style="margin:8px 0 0 0;padding:0;list-style:none;font-size:12px;line-height:1.7;">{items}</ul>'

        for alert in alert_list:
            atype = alert[0]
            # Choose the right explanation based on what the alert is about
            if atype in ('rev_drop_red', 'rev_drop_amber'):
                title_str = titles[alert_list.index(alert)] if alert in alert_list else ""
                why_html = bullets_to_html(why_rev.get(e, []))
            elif atype in ('loss_month', 'margin_compress'):
                title_str = titles[alert_list.index(alert)] if alert in alert_list else ""
                why_html = bullets_to_html(why_margin.get(e, []))
            else:
                title_str = titles[alert_list.index(alert)] if alert in alert_list else ""
                why_html = ""

            if alert_list.index(alert) < len(titles):
                title_str = titles[alert_list.index(alert)]
            else:
                continue

            banner_html += f'''
      <div class="alert-banner {color}">
        <div class="alert-icon">{icon}</div>
        <div>
          <strong>{title_str}</strong>
          {why_html}
        </div>
      </div>'''

    # ── Month story ──
    story_parts = []
    all_down = all(rev_moms[e] < 0 for e in entities)
    if all_down:
        story_parts.append(
            f"{month} was a soft month across all three entities. Combined portfolio revenue "
            f"dropped {abs(mom_pct(month_rev_cur, month_rev_prior)):.0f}% MoM "
            f"({fmt_dollar(month_rev_prior)} → {fmt_dollar(month_rev_cur)})."
        )

    red_entities   = [e for e in entities if statuses[e][0] == 'red']
    amber_entities = [e for e in entities if statuses[e][0] == 'amber']

    for e in red_entities:
        ni = mp[e]['net_income']
        rev = mp[e]['total_revenue']
        ni_pct = ni / rev * 100 if rev else 0
        if ni < 0:
            story_parts.append(
                f"{entity_labels[e]} recorded a loss month — net margin {fmt_pct(ni_pct)} "
                f"on {fmt_dollar(rev)} of revenue. COGS investigation needed before "
                f"{(report_date + timedelta(days=15)).strftime('%b %d')} close."
            )

    for e in amber_entities:
        story_parts.append(
            f"{entity_labels[e]} revenue was down {abs(rev_moms[e]):.0f}% MoM "
            f"({fmt_dollar(mp_prior[e]['total_revenue'])} → {fmt_dollar(mp[e]['total_revenue'])}) "
            f"with net margin at {fmt_pct(ni_pcts[e])}."
        )

    if red_entities:
        open_q = (f"Open question: What drove the COGS spike at "
                  f"{entity_labels[red_entities[0]]} in {month} — "
                  f"is this a one-time job overrun or a structural margin issue?")
    elif amber_entities:
        open_q = (f"Open question: Is the {month} revenue dip at "
                  f"{entity_labels[amber_entities[0]]} seasonal, a timing issue, "
                  f"or a sign of pipeline weakness?")
    else:
        open_q = ("Open question: Can the portfolio sustain this margin profile "
                  "through Q3 without the snow revenue boost?")

    story_html = "".join(f"<p>{p}</p>" for p in story_parts)
    story_html += f'<p class="open-q"><strong>Open question:</strong> {open_q.replace("Open question: ", "")}</p>'

    # ── Entity table rows ──
    entity_rows = ""
    for e in entities:
        g = gm[e]
        m = mp[e]
        ytd_ni_e   = g['net_income']
        ytd_ni_pct = ytd_ni_e / g['total_revenue'] * 100 if g['total_revenue'] else 0
        ni_month_pct = m['net_income'] / m['total_revenue'] * 100 if m['total_revenue'] else 0
        rev_mom   = rev_moms[e]
        arrow     = "&#9650;" if rev_mom >= 0 else "&#9660;"
        rev_cls   = "up" if rev_mom >= 0 else "down"
        ni_cls    = "up" if ni_month_pct > 10 else ("down" if ni_month_pct < 0 else "flat")
        s_color, s_label, _ = statuses[e]
        entity_rows += f'''
        <tr>
          <td class="entity-name">
            <span class="dot" style="background:{entity_dots[e]}"></span>{entity_labels[e]}
          </td>
          <td>{fmt_dollar(g['total_revenue'])}</td>
          <td class="{'up' if ytd_ni_pct > 15 else 'flat'}">{fmt_pct(ytd_ni_pct)}</td>
          <td>{fmt_dollar(m['total_revenue'])}</td>
          <td class="{ni_cls}">{fmt_pct(ni_month_pct)}</td>
          <td class="{rev_cls}">{arrow} {abs(rev_mom):.1f}%</td>
          <td><span class="badge {s_color}">{s_label}</span></td>
        </tr>'''

    # ── EOY forecast rows ──
    eoy_rows = ""
    for e in entities:
        eoy_rows += f'''
        <tr>
          <td><span class="dot" style="background:{entity_dots[e]}"></span>{entity_labels[e]}</td>
          <td>{fmt_dollar(gm[e]['total_revenue'])}</td>
          <td>{months_elapsed}</td>
          <td><strong>{fmt_dollar(eoy[e])}</strong></td>
        </tr>'''
    eoy_rows += f'''
        <tr class="total">
          <td>All Entities</td>
          <td>{fmt_dollar(ytd_rev)}</td>
          <td>{months_elapsed}</td>
          <td><strong>{fmt_dollar(eoy_total)}</strong></td>
        </tr>'''

    psb_snow_note = ""
    if months_elapsed <= 3:
        psb_snow_note = ("<div style='padding:10px 14px;font-size:11px;color:#94a3b8;"
                         "border-top:1px solid #f1f5f9;'>Note: PSB projection overstates H2 "
                         "— snow removal revenue does not recur.</div>")

    # ── Action items rows ──
    action_rows = ""
    for i, action in enumerate(actions[:7], 1):
        e_tag   = 'GOPM' if 'GOPM' in action else ('PSB' if 'PSB' in action else 'PPB')
        tag_cls = 'amber' if e_tag == 'GOPM' else 'green'
        is_red  = i <= len(red_entities)
        due     = due_red if is_red else due_amber
        num_color = '#dc2626' if is_red else '#d97706'
        action_rows += f'''
        <tr>
          <td style="text-align:center;font-weight:700;color:{num_color};">{i}</td>
          <td class="action-text">{action}</td>
          <td><span class="badge {tag_cls}">{e_tag}</span></td>
          <td class="owner-cell placeholder">&#9998; Assign</td>
          <td class="due-cell">{due}</td>
        </tr>'''

    # ── TTM chart ──
    ttm_data = data.get('ttm_monthly_income', {})
    if ttm_data and all(e in ttm_data for e in entities):
        ttm_section_html = build_ttm_section(
            ttm_data, entities, entity_labels, entity_dots, month, year
        )
    else:
        ttm_section_html = ''

    # ── A/R Aging card ──
    if ar_aging:
        ar_current  = ar_aging.get('current',  0)
        ar_net30    = ar_aging.get('net30',     0)
        ar_net30p   = ar_aging.get('net30plus', 0)
        ar_total    = ar_current + ar_net30 + ar_net30p
        ar_overdue_pct = (ar_net30p / ar_total * 100) if ar_total else 0
        ar_as_of    = ar_aging.get('as_of', report_date.strftime('%b %d, %Y'))
        ar_entity   = ar_aging.get('entity', 'Pro Services Boston')
        ar_note     = ar_aging.get('note', '')
        ar_net30p_cls = 'red' if ar_overdue_pct > 40 else 'amber' if ar_overdue_pct > 20 else 'green'
        ar_section_html = f'''
  <div>
    <div class="section-title">A/R Aging &mdash; {ar_entity} &nbsp;&middot;&nbsp; As of {ar_as_of}</div>
    <div class="kpi-grid">
      <div class="kpi-card green">
        <div class="val">{fmt_dollar(ar_current)}</div>
        <div class="name">Current</div>
        <div class="sub">Not yet due</div>
      </div>
      <div class="kpi-card amber">
        <div class="val">{fmt_dollar(ar_net30)}</div>
        <div class="name">Net 30</div>
        <div class="sub">1&ndash;30 days past due</div>
      </div>
      <div class="kpi-card {ar_net30p_cls}">
        <div class="val">{fmt_dollar(ar_net30p)}</div>
        <div class="name">Net 30+</div>
        <div class="sub">{fmt_pct(ar_overdue_pct)} of total A/R &nbsp;&middot;&nbsp; 31+ days past due</div>
      </div>
      <div class="kpi-card {'red' if ar_overdue_pct > 50 else 'amber'}">
        <div class="val">{fmt_dollar(ar_total)}</div>
        <div class="name">Total A/R</div>
        <div class="sub">{fmt_pct(ar_overdue_pct)} overdue &nbsp;&middot;&nbsp; {ar_note}</div>
      </div>
    </div>
  </div>'''
    else:
        ar_section_html = ''

    ytd_window = f"Jan–{month} {year}"

    month_options_html = ''.join(
        f'<option value="{m["url"]}">{m["label"]}</option>'
        for m in (prev_months or [])
    )

    # ── Full HTML ──
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GOPM | Financial Dashboard — Executive Summary {month} {year}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f1f5f9; color: #1e293b; font-size: 14px; }}
    .header {{ background: #0f172a; color: white; padding: 24px 32px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }}
    .header-left h1 {{ font-size: 20px; font-weight: 700; letter-spacing: 0.01em; }}
    .header-left p {{ font-size: 12px; color: #94a3b8; margin-top: 2px; }}
    .header-right {{ display: flex; gap: 32px; }}
    .header-stat {{ text-align: right; }}
    .header-stat .val {{ font-size: 28px; font-weight: 700; color: #f8fafc; }}
    .header-stat .lbl {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }}
    .page {{ max-width: 1200px; margin: 0 auto; padding: 24px 20px; display: flex; flex-direction: column; gap: 24px; }}
    .section-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #64748b; margin-bottom: 10px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .kpi-card {{ background: white; border-radius: 10px; padding: 16px; border-top: 4px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
    .kpi-card.green   {{ border-top-color: #16a34a; }}
    .kpi-card.red     {{ border-top-color: #dc2626; }}
    .kpi-card.amber   {{ border-top-color: #d97706; }}
    .kpi-card.neutral {{ border-top-color: #6366f1; }}
    .kpi-card .val  {{ font-size: 26px; font-weight: 700; color: #0f172a; line-height: 1; }}
    .kpi-card .name {{ font-size: 12px; color: #64748b; margin-top: 6px; line-height: 1.3; }}
    .kpi-card .sub  {{ font-size: 11px; color: #94a3b8; margin-top: 4px; }}
    .card {{ background: white; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.06); overflow: hidden; }}
    .card-header {{ padding: 14px 18px; border-bottom: 1px solid #f1f5f9; font-weight: 600; font-size: 13px; color: #0f172a; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ background: #f8fafc; padding: 9px 14px; text-align: left; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; color: #64748b; border-bottom: 1px solid #f1f5f9; }}
    td {{ padding: 11px 14px; border-bottom: 1px solid #f8fafc; font-size: 13px; color: #334155; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #fafbfc; }}
    tr.total td {{ background: #f8fafc; font-weight: 700; color: #0f172a; border-top: 2px solid #e2e8f0; }}
    .entity-name {{ font-weight: 600; color: #0f172a; }}
    .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 7px; vertical-align: middle; }}
    .badge {{ display: inline-block; padding: 3px 10px; border-radius: 99px; font-size: 11px; font-weight: 700; letter-spacing: .03em; }}
    .badge.green {{ background: #dcfce7; color: #15803d; }}
    .badge.red   {{ background: #fee2e2; color: #b91c1c; }}
    .badge.amber {{ background: #fef3c7; color: #b45309; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    @media (max-width:800px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
    .story-card {{ background: white; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.06); overflow: hidden; }}
    .story-card .story-header {{ padding: 14px 18px; border-bottom: 1px solid #f1f5f9; font-weight: 600; font-size: 13px; color: #0f172a; }}
    .story-card .story-body {{ padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }}
    .story-card .story-body p {{ font-size: 13px; color: #334155; line-height: 1.6; }}
    .open-q {{ font-style: italic; color: #64748b !important; border-top: 1px solid #f1f5f9; padding-top: 10px; margin-top: 4px; }}
    .alerts-wrap {{ display: flex; flex-direction: column; gap: 10px; }}
    .alert-banner {{ border-radius: 10px; padding: 14px 18px; font-size: 13px; line-height: 1.5; display: flex; align-items: flex-start; gap: 12px; }}
    .alert-banner.red   {{ background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }}
    .alert-banner.amber {{ background: #fffbeb; border: 1px solid #fde68a; color: #92400e; }}
    .alert-icon {{ font-size: 16px; flex-shrink: 0; margin-top: 1px; }}
    .up   {{ color: #16a34a; font-weight: 600; }}
    .down {{ color: #dc2626; font-weight: 600; }}
    .flat {{ color: #64748b; }}
    .action-text {{ font-size: 13px; color: #334155; line-height: 1.4; }}
    .owner-cell {{ font-size: 12px; color: #64748b; white-space: nowrap; }}
    .due-cell {{ font-size: 12px; color: #94a3b8; white-space: nowrap; }}
    .placeholder {{ color: #cbd5e1; font-style: italic; }}
    .footer {{ text-align: center; font-size: 11px; color: #94a3b8; padding: 16px 0 8px; }}
    .month-nav {{ background: #1e3a5f; color: #e2e8f0; border: none; border-radius: 8px; padding: 10px 16px; font-size: 13px; font-weight: 600; cursor: pointer; align-self: center; outline: none; }}
    .month-nav:hover {{ background: #2563eb; }}
    .month-nav option {{ background: #1e293b; color: #e2e8f0; }}
  </style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>GOPM &middot; Financial Dashboard &mdash; Executive Summary</h1>
    <p>{month} {year} &nbsp;&middot;&nbsp; Shared with CEO &amp; Director of Operations &nbsp;&middot;&nbsp; YTD: {ytd_window}</p>
  </div>
  <select class="month-nav" onchange="if(this.value) window.location=this.value" title="View another month">
    <option value="">&#9660; {month} {year}</option>
    {month_options_html}
  </select>
  <div class="header-right">
    <div class="header-stat"><div class="val">{fmt_dollar(ytd_rev)}</div><div class="lbl">YTD Revenue</div></div>
    <div class="header-stat"><div class="val">{fmt_dollar(ytd_ni)}</div><div class="lbl">YTD Net Income</div></div>
    <div class="header-stat"><div class="val">{fmt_pct(ytd_net_margin)}</div><div class="lbl">Net Margin</div></div>
  </div>
</div>

<div class="page">

  <div>
    <div class="section-title">Portfolio Snapshot &mdash; YTD ({ytd_window})</div>
    <div class="kpi-grid">
      <div class="kpi-card neutral">
        <div class="val">{fmt_dollar(ytd_rev)}</div>
        <div class="name">Total YTD Revenue</div>
        <div class="sub">All 3 entities combined</div>
      </div>
      <div class="kpi-card green">
        <div class="val">{fmt_dollar(ytd_ni)}</div>
        <div class="name">Total YTD Net Income</div>
        <div class="sub">{fmt_pct(ytd_net_margin)} combined net margin</div>
      </div>
      <div class="kpi-card {salary_color}">
        <div class="val">{fmt_pct(salary_pct)}</div>
        <div class="name">YTD Salary Load</div>
        <div class="sub">Target 25&ndash;35% &nbsp;&middot;&nbsp; {salary_verdict} &nbsp;&middot;&nbsp; was {fmt_pct(prior_salary_pct)} end of {may_label}</div>
      </div>
      <div class="kpi-card {'red' if month_rev_mom < -20 else ('amber' if month_rev_mom < 0 else 'green')}">
        <div class="val">{fmt_dollar(month_rev_cur)}</div>
        <div class="name">{month} Revenue (All Entities)</div>
        <div class="sub">vs {fmt_dollar(month_rev_prior)} prior month &nbsp;&middot;&nbsp; {'&#9660;' if month_rev_mom < 0 else '&#9650;'} {abs(month_rev_mom):.1f}%</div>
      </div>
    </div>
  </div>

  {'<div><div class="section-title">Flags &mdash; What Happened</div><div class="alerts-wrap">' + banner_html + '</div></div>' if banner_html else ''}

  <div class="card">
    <div class="card-header">Entity Status at a Glance</div>
    <table>
      <thead><tr>
        <th>Entity</th><th>YTD Revenue</th><th>YTD Net Margin</th>
        <th>{month} Revenue</th><th>{month} Net Margin</th><th>MoM Rev</th><th>Status</th>
      </tr></thead>
      <tbody>{entity_rows}</tbody>
    </table>
  </div>

  {ttm_section_html}

  {ar_section_html}

  <div class="two-col">
    <div class="story-card">
      <div class="story-header">&#128221;&nbsp; The {month} Story</div>
      <div class="story-body">{story_html}</div>
    </div>
    <div class="card">
      <div class="card-header">EOY Revenue Forecast &mdash; YTD Run-Rate &times; (12/{months_elapsed})</div>
      <table>
        <thead><tr><th>Entity</th><th>YTD Actual</th><th>Months</th><th>EOY Projection</th></tr></thead>
        <tbody>{eoy_rows}</tbody>
      </table>
      {psb_snow_note}
    </div>
  </div>

  <div class="card">
    <div class="card-header">Action Items This Month</div>
    <table>
      <thead><tr><th>#</th><th>Action</th><th>Entity</th><th>Owner</th><th>Due</th></tr></thead>
      <tbody>{action_rows}</tbody>
    </table>
  </div>

  <div class="footer">
    Financial Dashboard &mdash; Executive Summary &nbsp;&middot;&nbsp; {month} {year} &nbsp;&middot;&nbsp; Green Ocean Property Management
  </div>

</div>

</body>
</html>"""

    return html


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data',         required=True)
    parser.add_argument('--prior',        required=True)
    parser.add_argument('--qb-dir',       default=None,
                        help='Folder with current month TTM files (gopm_ttm.json etc.)')
    parser.add_argument('--prior-qb-dir', default=None,
                        help='Folder with prior month TTM files — enables cross-report revenue analysis')
    parser.add_argument('--ar-json',      default=None,
                        help='JSON file with AR aging: {current, net30, net30plus, entity, as_of, note}')
    parser.add_argument('--output',       required=True)
    parser.add_argument('--month',        required=True)
    parser.add_argument('--year',         required=True, type=int)
    args = parser.parse_args()

    import re as _re
    import glob as _glob

    data       = json.load(open(args.data,  encoding='utf-8'))
    prior_data = json.load(open(args.prior, encoding='utf-8'))
    report_date = datetime.today()

    ar_aging = None
    if args.ar_json and os.path.exists(args.ar_json):
        ar_aging = json.load(open(args.ar_json, encoding='utf-8'))

    netlify_site_id = os.environ.get('NETLIFY_SITE_ID', '')
    netlify_token   = os.environ.get('NETLIFY_TOKEN', '')

    # Determine archive filename for this month
    month_slug   = f'{args.month.lower()}-{args.year}'   # e.g. june-2026
    netlify_dir  = os.path.normpath(os.path.join(os.path.dirname(args.output), '..', 'netlify'))
    os.makedirs(netlify_dir, exist_ok=True)

    # Scan existing monthly archive files to build dropdown
    _month_order = ['january','february','march','april','may','june',
                    'july','august','september','october','november','december']
    def _month_key(fname):
        m = _re.match(r'^([a-z]+)-(\d{4})\.html$', fname)
        if not m: return (0, 0)
        return (int(m.group(2)), _month_order.index(m.group(1)) if m.group(1) in _month_order else 0)

    existing_files = [
        os.path.basename(p) for p in _glob.glob(os.path.join(netlify_dir, '*-????.html'))
        if os.path.basename(p) != f'{month_slug}.html'
    ]
    existing_files.sort(key=_month_key, reverse=True)
    prev_months = []
    for fname in existing_files:
        m = _re.match(r'^([a-z]+)-(\d{4})\.html$', fname)
        if m:
            prev_months.append({'label': f'{m.group(1).capitalize()} {m.group(2)}', 'url': f'/{fname}'})

    html = generate_html(data, prior_data, args.month, args.year,
                         report_date, qb_dir=args.qb_dir,
                         prior_qb_dir=args.prior_qb_dir,
                         ar_aging=ar_aging,
                         prev_months=prev_months)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Saved: {args.output}")

    # Write index.html and month archive to netlify/ folder
    for fname in ['index.html', f'{month_slug}.html']:
        with open(os.path.join(netlify_dir, fname), 'w', encoding='utf-8') as f:
            f.write(html)
    # Write _headers so all .html files serve as text/html
    with open(os.path.join(netlify_dir, '_headers'), 'w') as f:
        f.write('/*\n  Content-Type: text/html; charset=UTF-8\n')
    print(f"Netlify folder: {netlify_dir}  ({len(existing_files)+1} month(s) + index)")

    # Deploy entire netlify/ folder as zip
    if netlify_token:
        import zipfile, io, urllib.request, urllib.error
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fname in os.listdir(netlify_dir):
                fpath = os.path.join(netlify_dir, fname)
                if os.path.isfile(fpath):
                    with open(fpath, 'rb') as fp:
                        zf.writestr(fname, fp.read())
        buf.seek(0)
        req = urllib.request.Request(
            f'https://api.netlify.com/api/v1/sites/{netlify_site_id}/deploys',
            data=buf.read(), method='POST'
        )
        req.add_header('Authorization', f'Bearer {netlify_token}')
        req.add_header('Content-Type', 'application/zip')
        try:
            with urllib.request.urlopen(req) as resp:
                import json as _json
                d = _json.loads(resp.read())
                print(f"Netlify deployed: https://lucent-croquembouche-0bfaf9.netlify.app (state: {d.get('state')})")
        except urllib.error.HTTPError as e:
            print(f"Netlify deploy failed: {e.code} {e.read().decode()}")


if __name__ == '__main__':
    main()
