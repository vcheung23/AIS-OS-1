import json

d = json.load(open('C:/Users/vcheu/Claude Folder/financial_dashboard/qb_june_2026/data.json'))
dm = json.load(open('C:/Users/vcheu/Claude Folder/financial_dashboard/qb_may_2026/data.json'))

gm = d['gm_per_entity']
mp = d['month_pnl']
mp_prior = dm['month_pnl']
lc = d['labor_cost']
nl = d['non_labor_expense']
an = d['analytics']

ytd_rev = sum(gm[e]['total_revenue'] for e in ['GOPM','PSB','PPB'])
ytd_ni = sum(gm[e]['net_income'] for e in ['GOPM','PSB','PPB'])

print('=== PORTFOLIO YTD ===')
print('YTD Revenue:', f"{ytd_rev:,.0f}")
print('YTD Net Income:', f"{ytd_ni:,.0f}")
print('YTD Net Margin:', f"{ytd_ni/ytd_rev*100:.1f}%")

print()
print('=== gm_per_entity keys sample ===')
print(list(gm['GOPM'].keys()))

print()
print('=== month_pnl keys sample ===')
print(list(mp['GOPM'].keys()))

print()
print('=== MONTH P&L (June) ===')
for e in ['GOPM','PSB','PPB']:
    print(e, json.dumps(mp[e], indent=2))

print()
print('=== MONTH P&L (May) ===')
for e in ['GOPM','PSB','PPB']:
    print(e, json.dumps(mp_prior[e], indent=2))

print()
print('=== LABOR ===')
print(json.dumps(lc, indent=2)[:800])

print()
print('=== NON-LABOR ===')
print(json.dumps(nl, indent=2)[:600])

print()
print('=== ACTIONS ===')
for a in an['actions']:
    print('-', a)

print()
print('=== INSIGHTS ===')
for i in an['insights'][:6]:
    print('-', i)
