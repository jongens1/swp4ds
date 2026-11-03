"""Actual location inventory and whole-location recall planning (independent of UI)."""
from datetime import timedelta
import heapq
import math

import numpy as np
import pandas as pd


def is_4ds(locations):
    names = locations.astype('string').str.strip().str.upper()
    return names.str.startswith('4D1-', na=False) | names.eq('SHUTTLE-SWAP').fillna(False)


def inventory(frame, quantity, source):
    data = frame.copy()
    data.columns = data.columns.astype(str).str.strip()
    data = data.rename(columns={'Lokacia': 'Lokace'})
    required = ['Sklad', 'Lokace', 'Produkt', quantity]
    missing = [c for c in required if c not in data]
    if missing:
        raise ValueError(f"{source}: chýbajú stĺpce {', '.join(missing)}")
    for col in ['Sklad', 'Lokace', 'Produkt']:
        if data[col].isna().any() or data[col].astype(str).str.strip().eq('').any():
            raise ValueError(f'{source}: prázdny stĺpec {col}; opravte vstupné dáta.')
        data[col] = data[col].astype(str).str.strip()
    data['Lokace'] = data['Lokace'].str.upper()
    values = pd.to_numeric(data[quantity].astype(str).str.replace('\u00a0', '', regex=False)
                           .str.replace(' ', '', regex=False).str.replace(',', '.', regex=False), errors='coerce')
    if values.isna().any() or (~np.isfinite(values)).any() or values.lt(0).any():
        raise ValueError(f'{source}: množstvo musí byť nezáporné číslo na každom riadku.')
    data['Množstvo'] = values
    return data


def calendar_day(as_of, days):
    if pd.isna(days) or not math.isfinite(days):
        return None
    try:
        return as_of + timedelta(days=math.floor(max(0, days)))
    except OverflowError:
        return None


def build_return_plan(main, swap, four_ds, sales_days, fallback_monthly, buffer_days,
                      horizon_days, as_of):
    """SWAP and 4DS snapshots own their zones; never add their stock twice.

    Demand is per SKU, not repeated per location. Primary is consumed first,
    then SWAP. A recall always includes every SKU on the selected warehouse/location.
    """
    primary = inventory(main, 'Množstvo na lokácií', 'Hlavný Excel')
    swaps = inventory(swap, 'Množstvo', 'SWAP Excel')
    remote = inventory(four_ds, 'Množstvo', '4DS.xlsx')
    if 'Skladom' not in remote:
        raise ValueError('4DS.xlsx: chýba stĺpec Skladom.')
    if 'Predajnosť' not in primary:
        raise ValueError('Hlavný Excel: chýba stĺpec Predajnosť.')
    sales = pd.to_numeric(primary['Predajnosť'].astype(str).str.replace(',', '.', regex=False), errors='coerce')
    # Missing sales remains unknown; it must not silently become a recall order.
    if (~np.isfinite(sales.dropna())).any():
        raise ValueError('Predajnosť musí byť konečné číslo.')
    primary['Predajnosť'] = sales
    conflicts = primary.groupby('Produkt')['Predajnosť'].nunique().gt(1)
    if conflicts.any():
        raise ValueError('Rozdielna predajnosť pre rovnaký produkt: ' + ', '.join(conflicts[conflicts].index[:10]))
    demand = primary.groupby('Produkt')['Predajnosť'].first()

    ignored_remote = int((~is_4ds(remote['Lokace'])).sum())
    remote = remote[is_4ds(remote['Lokace'])].copy()
    swaps = swaps[~is_4ds(swaps['Lokace'])].copy()
    swap_keys = pd.MultiIndex.from_frame(swaps[['Sklad', 'Lokace']]).unique()
    main_keys = pd.MultiIndex.from_frame(primary[['Sklad', 'Lokace']])
    main_swap = primary['Lokace'].str.contains('SWAP', regex=False) | main_keys.isin(swap_keys)
    primary = primary[~is_4ds(primary['Lokace']) & ~main_swap].copy()
    remote = remote.groupby(['Sklad', 'Lokace', 'Produkt'], as_index=False)['Množstvo'].sum()
    remote = remote[remote['Množstvo'] > 0].copy()

    summary = pd.concat([
        primary.groupby('Produkt')['Množstvo'].sum().rename('Primár (ks)'),
        swaps.groupby('Produkt')['Množstvo'].sum().rename('SWAP (ks)'),
        remote.groupby('Produkt')['Množstvo'].sum().rename('4DS (ks)'),
        demand.rename('Predajnosť')
    ], axis=1)
    stock_cols = ['Primár (ks)', 'SWAP (ks)', '4DS (ks)']
    summary[stock_cols] = summary[stock_cols].fillna(0)
    summary['Náhradná predajnosť'] = summary['Predajnosť'].le(0)
    summary['Denný predaj (ks)'] = summary['Predajnosť'] / sales_days
    summary.loc[summary['Náhradná predajnosť'], 'Denný predaj (ks)'] = fallback_monthly / 30
    summary['Primár + SWAP (ks)'] = summary['Primár (ks)'] + summary['SWAP (ks)']
    ads = summary['Denný predaj (ks)']
    summary['Primár vydrží (dni)'] = summary['Primár (ks)'] / ads
    summary['SWAP samostatne (dni)'] = summary['SWAP (ks)'] / ads
    summary['Primár + SWAP vydrží (dni)'] = summary['Primár + SWAP (ks)'] / ads
    summary['Zásoba spolu vydrží (dni)'] = (summary['Primár + SWAP (ks)'] + summary['4DS (ks)']) / ads
    summary['Prvý návrat o (dni)'] = (summary['Primár + SWAP vydrží (dni)'] - buffer_days).clip(lower=0)
    summary.loc[summary['4DS (ks)'].eq(0), 'Prvý návrat o (dni)'] = np.nan
    summary['Primár dôjde'] = summary['Primár vydrží (dni)'].map(lambda d: calendar_day(as_of, d))
    summary['Primár + SWAP dôjde'] = summary['Primár + SWAP vydrží (dni)'].map(lambda d: calendar_day(as_of, d))
    summary['Prvý návrat najneskôr'] = summary['Prvý návrat o (dni)'].map(lambda d: calendar_day(as_of, d))
    summary['Stav'] = np.select([
        ads.isna(),
        summary['Primár + SWAP vydrží (dni)'].le(buffer_days) & summary['4DS (ks)'].eq(0),
        summary['Prvý návrat o (dni)'].eq(0),
        summary['Prvý návrat o (dni)'].le(horizon_days),
    ], ['Chýba predajnosť', 'Nízka zásoba – bez zásoby v 4DS', 'Presunúť teraz', 'Presun v horizonte'], default='Bez presunu v horizonte')

    # Every physical location is one unit, including all products stored together.
    pending = {(warehouse, location): group for (warehouse, location), group in
               remote.groupby(['Sklad', 'Lokace'], sort=True)}
    projected = summary['Primár + SWAP (ks)'].to_dict()
    plan_rows = []
    blocked = []
    for key in list(pending):
        group = pending[key]
        if group['Produkt'].map(ads).isna().any():
            blocked.append(key)
            del pending[key]
    contents = {key: list(zip(group['Produkt'], group['Množstvo'])) for key, group in pending.items()}
    rates = ads.to_dict()
    def next_due(key):
        return min(max(0, projected[sku] / rates[sku] - buffer_days) for sku, _ in contents[key])
    queue = [(next_due(key), key) for key in pending]
    heapq.heapify(queue)
    ordinal = 0
    while queue:
        due, key = heapq.heappop(queue)
        # Previous recalls only increase coverage. Refresh stale priorities lazily.
        current_due = next_due(key)
        if current_due > due:
            heapq.heappush(queue, (current_due, key))
            continue
        if due > horizon_days:
            break
        ordinal += 1
        for sku, quantity in contents[key]:
            plan_rows.append({
                'Poradie lokácie': ordinal,
                'Presun najneskôr': calendar_day(as_of, due),
                'O dní': math.floor(due), 'Sklad': key[0], 'Lokace': key[1],
                'Produkt': sku, 'Presunúť (ks)': quantity,
                'Rozsah presunu': 'Celá lokácia – všetky uvedené produkty',
            })
            projected[sku] += quantity
    plan = pd.DataFrame(plan_rows, columns=['Poradie lokácie', 'Presun najneskôr', 'O dní',
                         'Sklad', 'Lokace', 'Produkt', 'Presunúť (ks)', 'Rozsah presunu'])
    summary.index.name = 'Produkt'
    return (summary.reset_index().sort_values('Prvý návrat o (dni)', na_position='last'),
            plan, (primary, swaps, remote), ignored_remote, blocked)


def render_return_tab(st, main, swap, uploaded, source_label, sales_days, fallback_monthly,
                      buffer_days, horizon_days):
    """Keep input/validation failures isolated from the original two tabs."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import io
    import os

    st.subheader('Riadenie presunu späť zo 4DS')
    st.caption('Aktuálny stav zo vstupných Excelov. Výpočet je nezávislý od simulácie a filtrov vývozu do 4DS.')
    source = uploaded if uploaded is not None else ('4DS.xlsx' if os.path.exists('4DS.xlsx') else None)
    if source is None:
        st.info('Pridaj 4DS.xlsx do koreňa GitHub repozitára alebo ho nahraj v ľavom paneli. '
                'Stĺpce: Sklad, Lokace, Produkt, Množstvo, Skladom.')
        return
    if swap is None:
        st.warning('Na spoľahlivý výpočet pokrytia primáru a SWAP-u je potrebný aj SWAP Excel s množstvami.')
        return
    st.caption(f'4DS dáta: {source_label or "4DS.xlsx (predvolený)"}')
    as_of = st.date_input('Dátum stavu zásob', value=datetime.now(ZoneInfo('Europe/Prague')).date(), key='return_as_of')
    st.markdown(f'**Predstih spätného presunu: {buffer_days} dní.** Používa hranicu spätného závozu '
                'z ľavého panela. Termíny sú zaokrúhlené nadol na celý deň.')
    try:
        remote_input = pd.read_excel(source)
        summary, plan, zones, ignored, blocked = build_return_plan(
            main, swap, remote_input, sales_days, fallback_monthly, buffer_days, horizon_days, as_of)
    except (ValueError, OSError, ImportError) as error:
        st.error(f'Spätný presun: {error}')
        return
    st.caption('Množstvá sa sčítajú podľa lokácií; stĺpec Skladom sa nesčítava. '
               '4D1-* a SHUTTLE-SWAP patria do 4DS. Ostatné SWAP lokácie sa čítajú zo SWAP Excelu, '
               'ostatné lokácie z hlavného Excelu tvoria primár. Zásoby naprieč skladmi sa pre SKU sčítajú. '
               'Predpoklad: najskôr sa spotrebuje primár, potom SWAP; bez nových príjmov.')
    if ignored:
        st.warning(f'4DS.xlsx: {ignored} riadkov mimo 4D1-* a SHUTTLE-SWAP sa nezapočítalo.')
    if summary['Denný predaj (ks)'].isna().any():
        st.warning('Niektorým produktom chýba predajnosť v hlavnom Exceli. Termíny pre ne nie sú vypočítané.')
    if blocked:
        st.warning(f'{len(blocked)} celých 4DS lokácií má produkt bez predajnosti a nie je zahrnutých do plánu.')
        st.dataframe(pd.DataFrame(blocked, columns=['Sklad', 'Lokace']), hide_index=True)
    if summary['Náhradná predajnosť'].any():
        st.caption(f'Pri nulovom alebo zápornom predaji je použitý odhad {fallback_monthly} ks/mesiac podľa nastavenia vľavo.')
    cols = st.columns(3)
    for col, label, data in zip(cols, ['1. Primár lokácie', '2. SWAP lokácie', '3. Všetky 4DS lokácie'], zones):
        with col:
            st.metric(label, f'{data["Množstvo"].sum():,.0f} ks'.replace(',', ' '))
            st.caption(f'{data["Produkt"].nunique()} SKU · {len(data[["Sklad", "Lokace"]].drop_duplicates())} lokácií')
    st.subheader('Kedy dôjde zásoba a kedy začať s návratom')
    st.dataframe(summary, hide_index=True, use_container_width=True)
    st.caption('SWAP samostatne = počet dní, ktoré pridá po spotrebovaní primáru. '
               'Prvý návrat je termín potreby daného SKU; spoločná lokácia môže byť v pláne vytiahnutá skôr kvôli inému produktu.')
    st.subheader(f'Plán celých lokácií na {horizon_days} dní')
    st.caption('Každá kombinácia Sklad + Lokace sa presúva celá a iba raz. '
               'Pri zmiešanej lokácii presuň všetky jej uvedené produkty spolu. '
               'Po každom návrate sa pokrytie prepočíta vrátane privezených kusov. '
               'Plán neobmedzuje denná dopravná kapacita; súčet termínov určuje potrebu dopravy.')
    if plan.empty:
        st.info('V zadanom horizonte nie je naplánovaná žiadna úplná lokácia. Skontroluj aj upozornenia na chýbajúce dáta.')
    else:
        st.dataframe(plan, hide_index=True, use_container_width=True)
        daily = plan.drop_duplicates(['Sklad', 'Lokace']).groupby('Presun najneskôr').size().rename('Celé lokácie').reset_index()
        st.dataframe(daily, hide_index=True, use_container_width=True)
    with st.expander('Detail zásob v troch sekciách'):
        for label, data in zip(['1. Primár lokácie', '2. SWAP lokácie', '3. Všetky 4DS lokácie'], zones):
            st.markdown(f'**{label}**')
            st.dataframe(data[['Sklad', 'Lokace', 'Produkt', 'Množstvo']], hide_index=True, use_container_width=True)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        summary.to_excel(writer, index=False, sheet_name='Pokrytie zásob')
        plan.to_excel(writer, index=False, sheet_name='Spätné presuny')
        for label, data in zip(['Primár', 'SWAP', '4DS'], zones):
            data[['Sklad', 'Lokace', 'Produkt', 'Množstvo']].to_excel(writer, index=False, sheet_name=label)
    st.download_button('Stiahnuť plán spätných presunov (Excel)', data=output.getvalue(),
                       file_name='spatne_presuny_4DS.xlsx',
                       mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
