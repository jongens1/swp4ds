import streamlit as st
import pandas as pd
import numpy as np
import io
import os
import plotly.express as px
import plotly.graph_objects as go

# Konfigurácia stránky - STÁLY NÁZOV
st.set_page_config(
    page_title="Riadenie toku z a do 4DS",
    page_icon="📦",
    layout="wide"
)

st.title("📦 Riadenie toku z a do 4DS")
st.markdown("Aplikácia na optimalizáciu nadzásob, využitie SWAP lokácií a **prioritnú simuláciu logistických tokov**.")

# --- BOČNÝ PANEL: NASTAVENIA ---
st.sidebar.header("⚙️ Nastavenia výpočtu")

# Upload hlavného súboru
uploaded_file = st.sidebar.file_uploader(
    "1. Nahraj hlavný Excel (zásoby)", 
    type=["xlsx", "xls"]
)

# Upload SWAP súboru
uploaded_swap_file = st.sidebar.file_uploader(
    "2. Nahraj SWAP Excel (voliteľné)", 
    type=["xlsx", "xls"]
)

st.sidebar.divider()
st.sidebar.subheader("🎯 Parametre nadzásoby")

target_doh = st.sidebar.number_input(
    "Cieľová zásoba na lokácii (Target DOH v dňoch)", 
    min_value=1, max_value=365, value=30
)

sales_period_days = st.sidebar.number_input(
    "Sledované obdobie predajnosti (v dňoch)", 
    min_value=1, max_value=365, value=30
)

min_pallets_to_move = st.sidebar.number_input(
    "Minimálny počet plných paliet na presun", 
    min_value=1, max_value=50, value=1
)

fallback_monthly_sales = st.sidebar.number_input(
    "Náhradná predajnosť pre nepredané produkty (ks/mesiac)",
    min_value=1, max_value=100, value=5
)

min_stock_zero_sales = st.sidebar.number_input(
    "🛡️ Min. zásoba na lokácii pri 0 predaji (ks)",
    min_value=0, max_value=1000, value=20
)

st.sidebar.divider()
st.sidebar.subheader("🔮 Parametre Simulácie & Dopravy")

daily_logistics_capacity = st.sidebar.number_input(
    "🚚 Max. kapacita prepravy (paliet / deň)",
    min_value=1, max_value=500, value=20,
    help="Maximálny počet paliet, ktoré dokáže logistika denne odviezť alebo priviezť."
)

recall_trigger_doh = st.sidebar.number_input(
    "🔔 Hranica pre spätný závoz z 4DS (Trigger DOH v dňoch)",
    min_value=1, max_value=60, value=7,
    help="Ak zásoba na primárnom sklade klesne pod tento počet dní, vzniká PRIORITNÁ požiadavka priviezť paletu z 4DS."
)

sim_horizon_days = st.sidebar.number_input(
    "📅 Horizont simulácie (dni)",
    min_value=10, max_value=180, value=60
)

# --- URČENIE ZDROJA DÁT ---
df = None
swap_df = None
data_source_label = ""
swap_source_label = ""

if uploaded_file is not None:
    df = pd.read_excel(uploaded_file)
    data_source_label = f"`{uploaded_file.name}`"
elif os.path.exists("datatest.xlsx"):
    df = pd.read_excel("datatest.xlsx")
    data_source_label = "`datatest.xlsx` (predvolený)"

if uploaded_swap_file is not None:
    swap_df = pd.read_excel(uploaded_swap_file)
    swap_source_label = f"`{uploaded_swap_file.name}`"
elif os.path.exists("swap.xlsx"):
    swap_df = pd.read_excel("swap.xlsx")
    swap_source_label = "`swap.xlsx` (predvolený)"
elif os.path.exists("swp.xlsx"):
    swap_df = pd.read_excel("swp.xlsx")
    swap_source_label = "`swp.xlsx` (predvolený)"

# === SPRACOVANIE SWAP DÁT & GEOSIZE FILTER ===
selected_geosizes = []
geosize_col = None

if swap_df is not None:
    swap_df.columns = swap_df.columns.astype(str).str.strip()
    for col in swap_df.columns:
        if col.upper() == 'GEOSIZE':
            geosize_col = col
            break

    if geosize_col:
        unique_geosizes = swap_df[geosize_col].dropna().unique().tolist()
        st.sidebar.divider()
        st.sidebar.subheader("📐 Filter podľa GEOSIZE (SWAP)")
        selected_geosizes = st.sidebar.multiselect(
            "Vyber požadované GEOSIZE:",
            options=unique_geosizes,
            default=unique_geosizes
        )
        swap_df_filtered = swap_df[swap_df[geosize_col].isin(selected_geosizes)].copy()
    else:
        swap_df_filtered = swap_df.copy()
else:
    swap_df_filtered = None

# --- HLAVNÁ LOGIKA ---
if df is not None:
    try:
        st.sidebar.success(f"📁 Hlavné dáta: **{data_source_label}**")
        if swap_df is not None:
            st.sidebar.success(f"🔄 SWAP dáta: **{swap_source_label}**")
        
        df.columns = df.columns.astype(str).str.strip()
        required_cols = ['Sklad', 'Lokace', 'Produkt', 'Množstvo na lokácií', 'Skladom', 'Objem v M3', 'Predajnosť']
        missing_cols = [col for col in required_cols if col not in df.columns]
        
        if missing_cols:
            st.error(f"❌ V Exceli chýbajú tieto povinné stĺpce: {', '.join(missing_cols)}")
        else:
            num_cols = ['Množstvo na lokácií', 'Skladom', 'Objem v M3', 'Predajnosť']
            for col in num_cols:
                if df[col].dtype == 'object':
                    df[col] = df[col].astype(str).str.replace(',', '.').str.strip().astype(float)

            # === ZSKUPENIE PODĽA PRODUKTU (SKU) ===
            sku_df = df.groupby('Produkt').agg({
                'Sklad': 'first',
                'Lokace': lambda x: ', '.join(x.dropna().astype(str).unique()),
                'Skladom': 'first',
                'Objem v M3': 'first',
                'Predajnosť': 'first',
                'Množstvo na lokácií': 'sum'
            }).reset_index()

            sku_df['Predajnosť'] = sku_df['Predajnosť'].fillna(0)

            # === SPRACOVANIE SWAP DÁT ===
            if swap_df_filtered is not None and not swap_df_filtered.empty:
                agg_dict = {
                    'Paliet_na_SWAP': ('Lokacia', 'count'),
                    'SWAP_lokacie': ('Lokacia', lambda x: ', '.join(x.dropna().astype(str).unique()))
                }
                if geosize_col:
                    agg_dict['SWAP_geosize'] = (geosize_col, lambda x: ', '.join(x.dropna().astype(str).unique()))

                swap_summary = swap_df_filtered.groupby('Produkt').agg(**agg_dict).reset_index()

                sku_df = pd.merge(sku_df, swap_summary, on='Produkt', how='left')
                sku_df['Paliet_na_SWAP'] = sku_df['Paliet_na_SWAP'].fillna(0).astype(int)
                sku_df['SWAP_lokacie'] = sku_df['SWAP_lokacie'].fillna('-')
                sku_df['SWAP_geosize'] = sku_df['SWAP_geosize'].fillna('-') if geosize_col else '-'
            else:
                sku_df['Paliet_na_SWAP'] = 0
                sku_df['SWAP_lokacie'] = '-'
                sku_df['SWAP_geosize'] = '-'

            # === VÝPOČTY PRE KAŽDÉ SKU ===
            fallback_daily_sales = fallback_monthly_sales / 30.0
            
            sku_df['ADS'] = np.where(
                sku_df['Predajnosť'] > 0, 
                sku_df['Predajnosť'] / sales_period_days, 
                fallback_daily_sales
            )
            
            sku_df['Potrebná zásoba (ks)'] = sku_df['ADS'] * target_doh
            sku_df['Potrebná zásoba (ks)'] = np.where(
                sku_df['Predajnosť'] <= 0,
                np.maximum(sku_df['Potrebná zásoba (ks)'], min_stock_zero_sales),
                sku_df['Potrebná zásoba (ks)']
            )
            
            sku_df['Nadzásoba (ks)'] = (sku_df['Skladom'] - sku_df['Potrebná zásoba (ks)']).clip(lower=0)
            sku_df['Nadzásoba (m3)'] = sku_df['Nadzásoba (ks)'] * sku_df['Objem v M3']
            sku_df['Kusov na palete'] = np.where(sku_df['Objem v M3'] > 0, np.floor(1 / sku_df['Objem v M3']), 0)
            sku_df['Plné palety na presun'] = np.floor(sku_df['Nadzásoba (m3)']).astype(int)
            sku_df['Kusov na presun'] = sku_df['Plné palety na presun'] * sku_df['Kusov na palete']
            sku_df['Nadzásoba v dňoch'] = (sku_df['Nadzásoba (ks)'] / sku_df['ADS']).round(1)

            sku_df['Paliet zo SWAP (IHNEĎ)'] = np.minimum(sku_df['Plné palety na presun'], sku_df['Paliet_na_SWAP'])
            sku_df['Paliet z bežných lokácií'] = sku_df['Plné palety na presun'] - sku_df['Paliet zo SWAP (IHNEĎ)']

            # === FILTROVANIE A ZORADENIE ===
            filtered_df = sku_df[sku_df['Plné palety na presun'] >= min_pallets_to_move].copy()
            filtered_df = filtered_df.sort_values(by=['Plné palety na presun', 'Paliet zo SWAP (IHNEĎ)'], ascending=[False, False])

            # =========================================================================
            # TABULKOVÉ ZOBRAZENIE CEZ ZÁLOŽKY (TABS)
            # =========================================================================
            tab1, tab2 = st.tabs(["📦 Návrh presunu do 4DS", "🔮 Simulácia & Prioritný tok"])

            with tab1:
                # === METRIKY ===
                st.subheader("📊 Súhrn pre presun do 4DS")
                col1, col2, col3, col4, col5 = st.columns(5)
                
                total_pallets = filtered_df['Plné palety na presun'].sum()
                swap_pallets = filtered_df['Paliet zo SWAP (IHNEĎ)'].sum()
                standard_pallets = filtered_df['Paliet z bežných lokácií'].sum()
                total_m3 = (filtered_df['Plné palety na presun'] * 1.0).sum()
                total_skus = len(filtered_df)

                col1.metric("Celkom paliet na presun", f"{total_pallets:,} pal".replace(",", " "))
                col2.metric("⚡ IHNEĎ zo SWAP-u", f"{swap_pallets:,} pal".replace(",", " "), delta="Pripravené na odvoz", delta_color="normal")
                col3.metric("📦 Z bežných lokácií", f"{standard_pallets:,} pal".replace(",", " "))
                col4.metric("Celkový objem", f"{total_m3:,.1f} m³".replace(",", " "))
                col5.metric("Počet dotknutých SKU", f"{total_skus} SKU")

                st.divider()

                # === GRAFY ===
                if not filtered_df.empty:
                    g_col1, g_col2 = st.columns([3, 2])

                    with g_col1:
                        st.subheader("📈 Top 10 SKU: SWAP vs Bežné lokácie")
                        top10_df = filtered_df.head(10).copy()
                        top10_melted = top10_df.melt(
                            id_vars=['Produkt'], 
                            value_vars=['Paliet zo SWAP (IHNEĎ)', 'Paliet z bežných lokácií'],
                            var_name='Typ lokácie', 
                            value_name='Počet paliet'
                        )

                        fig_stack = px.bar(
                            top10_melted,
                            x='Počet paliet',
                            y='Produkt',
                            color='Typ lokácie',
                            orientation='h',
                            color_discrete_map={
                                'Paliet zo SWAP (IHNEĎ)': '#2ca02c',
                                'Paliet z bežných lokácií': '#1f77b4'
                            }
                        )
                        fig_stack.update_layout(height=350, yaxis={'categoryorder':'total ascending'})
                        st.plotly_chart(fig_stack, use_container_width=True)

                    with g_col2:
                        st.subheader("📐 Rozpad SWAP paliet podľa GEOSIZE")
                        if geosize_col and swap_df_filtered is not None and not swap_df_filtered.empty:
                            swap_in_scope = swap_df_filtered[swap_df_filtered['Produkt'].isin(filtered_df['Produkt'])]
                            geosize_summary = swap_in_scope.groupby(geosize_col).size().reset_index(name='Počet paliet')
                            
                            fig_geo = px.bar(
                                geosize_summary,
                                x=geosize_col,
                                y='Počet paliet',
                                text='Počet paliet',
                                color=geosize_col,
                                color_discrete_sequence=px.colors.qualitative.Safe
                            )
                            fig_geo.update_layout(height=350, showlegend=False)
                            st.plotly_chart(fig_geo, use_container_width=True)
                        else:
                            st.info("Žiadne GEOSIZE dáta na zobrazenie.")

                    st.divider()

                # === TABUĽKA ===
                st.subheader(f"📋 Zoznam produktov na presun do 4DS (Plné palety ≥ {min_pallets_to_move})")
                display_cols = [
                    'Sklad', 'Produkt', 'Plné palety na presun', 
                    'Paliet zo SWAP (IHNEĎ)', 'SWAP_geosize', 'SWAP_lokacie', 
                    'Paliet z bežných lokácií', 'Lokace', 
                    'Skladom', 'Nadzásoba v dňoch', 'Kusov na presun'
                ]
                rename_dict = {
                    'SWAP_geosize': 'SWAP GEOSIZE',
                    'SWAP_lokacie': 'SWAP Lokácie (IHNEĎ)',
                    'Lokace': 'Bežné Lokácie'
                }
                st.dataframe(filtered_df[display_cols].rename(columns=rename_dict), use_container_width=True, hide_index=True)

                # EXPORT
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    filtered_df.to_excel(writer, index=False, sheet_name='Návrh 4DS presunu')
                excel_data = output.getvalue()

                st.download_button(
                    label="📥 Stiahnuť návrh presunov (Excel)",
                    data=excel_data,
                    file_name="navrh_presunu_4DS.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

            # =========================================================================
            # ZÁLOŽKA 2: DYNAMICKÁ PRIORITNÁ SIMULÁCIA
            # =========================================================================
            with tab2:
                st.subheader("🔮 Dynamická simulácia s prioritou pre spätný tok")
                st.markdown(f"""
                Simulácia prebieha deň po dni. **Spätné závozy z 4DS majú 100% PRIORITU** pre zamedzenie výpadkov.
                * **Denná kapacita prepravy:** `{daily_logistics_capacity} paliet/deň`
                * **Pravidlo:** Ak si spätný tok vyžaduje napr. 5 paliet, pre vývoz nadzásoby do 4DS zostane v ten deň už len 15 paliet.
                """)

                # Inicializácia stavu produktov pre simuláciu
                sim_skus = []
                for _, row in filtered_df.iterrows():
                    sim_skus.append({
                        'Produkt': row['Produkt'],
                        'ADS': row['ADS'],
                        'Kusov_na_palete': row['Kusov na palete'],
                        'stock_primary': float(row['Skladom']),
                        'stock_4ds': 0,
                        'outbound_queue': int(row['Plné palety na presun']),
                        'Trigger_ks': float(row['ADS'] * recall_trigger_doh)
                    })

                daily_schedule = []
                recall_log = []

                # Simulácia deň po dni
                for d in range(1, sim_horizon_days + 1):
                    inbound_today = 0
                    outbound_today = 0
                    
                    # 1. Odpísanie predaja za deň
                    for item in sim_skus:
                        item['stock_primary'] = max(0.0, item['stock_primary'] - item['ADS'])

                    # 2. PRIORITA 1: Spätný dovoz (4DS -> Primár)
                    inbound_candidates = [
                        item for item in sim_skus 
                        if item['stock_primary'] <= item['Trigger_ks'] and item['stock_4ds'] > 0
                    ]
                    inbound_candidates.sort(key=lambda x: x['stock_primary'] / x['ADS'] if x['ADS'] > 0 else 0)

                    for item in inbound_candidates:
                        if inbound_today < daily_logistics_capacity:
                            item['stock_4ds'] -= 1
                            item['stock_primary'] += item['Kusov_na_palete']
                            inbound_today += 1
                            
                            recall_log.append({
                                'Deň': f"Deň {d}",
                                'Deň_cislo': d,
                                'Produkt': item['Produkt'],
                                'Dôvod': f"Zásoba klesla pod {recall_trigger_doh} dňový limit",
                                'Vrátená 1 paleta (ks)': int(item['Kusov_na_palete']),
                                'Zostáva v 4DS (pal)': item['stock_4ds']
                            })
                        else:
                            break

                    # 3. PRIORITA 2: Vývoz nadzásoby (Primár -> 4DS) z VOĽNEJ kapacity
                    remaining_cap = daily_logistics_capacity - inbound_today

                    if remaining_cap > 0:
                        outbound_candidates = [
                            item for item in sim_skus 
                            if item['outbound_queue'] > 0
                        ]
                        outbound_candidates.sort(key=lambda x: x['outbound_queue'], reverse=True)

                        for item in outbound_candidates:
                            while item['outbound_queue'] > 0 and remaining_cap > 0:
                                item['outbound_queue'] -= 1
                                item['stock_primary'] -= item['Kusov_na_palete']
                                item['stock_4ds'] += 1
                                outbound_today += 1
                                remaining_cap -= 1

                    daily_schedule.append({
                        'Deň': f"Deň {d}",
                        'Deň_cislo': d,
                        'Vývoz do 4DS (pal)': outbound_today,
                        'Spätný dovoz z 4DS (pal)': inbound_today,
                        'Celkom preprava (pal)': inbound_today + outbound_today,
                        'Využitie kapacity (%)': round(((inbound_today + outbound_today) / daily_logistics_capacity) * 100, 1)
                    })

                schedule_df = pd.DataFrame(daily_schedule)
                recall_df = pd.DataFrame(recall_log)

                # === METRIKY SIMULÁCIE ===
                s_col1, s_col2, s_col3, s_col4 = st.columns(4)
                
                total_outbound_sim = schedule_df['Vývoz do 4DS (pal)'].sum()
                total_inbound_sim = schedule_df['Spätný dovoz z 4DS (pal)'].sum()
                avg_utilization = schedule_df['Využitie kapacity (%)'].mean()
                full_days = schedule_df[schedule_df['Celkom preprava (pal)'] == daily_logistics_capacity]['Deň_cislo'].count()

                s_col1.metric("Celkom vyvezené do 4DS", f"{total_outbound_sim} pal")
                s_col2.metric("Celkom vrátené z 4DS", f"{total_inbound_sim} pal")
                s_col3.metric("Priemerné využitie dopravy", f"{avg_utilization:.1f} %")
                s_col4.metric("Dni so 100% vyťaženou dopravou", f"{full_days} dní")

                st.divider()

                # === GRAF VYŤAŽENIA DOPRAVY V ČASE ===
                st.subheader("📊 Denné vyťaženie dopravy (Spätný tok má prioritu)")
                
                fig_timeline = go.Figure()
                
                fig_timeline.add_trace(go.Bar(
                    x=schedule_df['Deň_cislo'],
                    y=schedule_df['Vývoz do 4DS (pal)'],
                    name='Vývoz nadzásoby do 4DS',
                    marker_color='#1f77b4'
                ))
                
                fig_timeline.add_trace(go.Bar(
                    x=schedule_df['Deň_cislo'],
                    y=schedule_df['Spätný dovoz z 4DS (pal)'],
                    name='PRIORITA: Spätný dovoz z 4DS',
                    marker_color='#ff7f0e'
                ))
                
                fig_timeline.add_trace(go.Scatter(
                    x=schedule_df['Deň_cislo'],
                    y=[daily_logistics_capacity] * len(schedule_df),
                    mode='lines',
                    name='Max. Denná Kapacita',
                    line=dict(color='red', width=2, dash='dash')
                ))

                fig_timeline.update_layout(
                    barmode='stack',
                    xaxis_title='Deň simulácie',
                    yaxis_title='Počet paliet',
                    height=400,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig_timeline, use_container_width=True)

                st.divider()

                # === DENNÍK SPÄTNÝCH ZÁVOZOV ===
                st.subheader("📅 Denník prioritných spätných závozov z 4DS")
                if not recall_df.empty:
                    st.dataframe(recall_df[['Deň', 'Produkt', 'Dôvod', 'Vrátená 1 paleta (ks)', 'Zostáva v 4DS (pal)']], use_container_width=True, hide_index=True)
                else:
                    st.success("✅ Počas celej simulácie nebolo potrebné z 4DS doviezť späť žiadnu paletu.")

    except Exception as e:
        st.error(f"⚠️ Nastal problém pri spracovaní súboru: {e}")

else:
    st.info("👈 Nahraj Excel súbory v ľavom menu alebo pridaj `datatest.xlsx` a `swap.xlsx` / `swp.xlsx` do repozitára.")
