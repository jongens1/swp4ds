import streamlit as st
import pandas as pd
import numpy as np
import io
import os
import plotly.express as px
import plotly.graph_objects as go

# Konfigurácia stránky
st.set_page_config(
    page_title="4DS Oversupply Optimizer & Simulator",
    page_icon="📦",
    layout="wide"
)

st.title("📦 Riadenie toku pailet z 4DS a do 4DS")
st.markdown("Aplikácia na optimalizáciu nadzásob, využitie SWAP lokácií a **simuláciu časového vývoja zásob a spätných závozov**.")

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
st.sidebar.subheader("🔮 Simulácia spätného toku")

daily_logistics_capacity = st.sidebar.number_input(
    "🚚 Max. kapacita prepravy (paliet / deň)",
    min_value=1, max_value=500, value=20,
    help="Maximálny počet paliet, ktoré dokáže logistika denne odviezť alebo priviezť."
)

recall_trigger_doh = st.sidebar.number_input(
    "🔔 Hranica pre spätný závoz z 4DS (Trigger DOH v dňoch)",
    min_value=1, max_value=60, value=7,
    help="Ak zásoba na primárnom sklade klesne pod tento počet dní, vzniká požiadavka priviezť paletu z 4DS."
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
            tab1, tab2 = st.tabs(["📦 Návrh presunu do 4DS", "🔮 Simulácia & Spätný tok (4DS ➔ Primár)"])

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
            # ZÁLOŽKA 2: SIMULÁCIA A SPÄTNÝ TOK (4DS -> PRIMÁR)
            # =========================================================================
            with tab2:
                st.subheader("🔮 Simulácia vývoja zásob v čase a plán spätných závozov")
                st.markdown(f"""
                Tento modul simuluje, čo sa stane po vyvezení navrhnutých paliet do 4DS. 
                Pri dennom predaji sleduje stav na primárnom sklade a spočíta, **kedy bude potrebné palety doviezť z 4DS späť**.
                * **Nastavená denná kapacita logistiky:** `{daily_logistics_capacity} paliet/deň`
                * **Hranica pre spätný závoz:** `{recall_trigger_doh} dní` predaja
                """)

                # === LOGIKA SIMULÁCIE ===
                sim_data = filtered_df.copy()
                
                # Zásoba po vyvezení
                sim_data['Skladom_po_vyvoze'] = sim_data['Skladom'] - sim_data['Kusov na presun']
                sim_data['Trigger_ks'] = sim_data['ADS'] * recall_trigger_doh

                # Vypočítame v ktorý deň klesne zásoba pod Trigger_ks
                # Deň = (Skladom_po_vyvoze - Trigger_ks) / ADS
                sim_data['Dni_do_spatneho_zavozu'] = np.where(
                    sim_data['ADS'] > 0,
                    (sim_data['Skladom_po_vyvoze'] - sim_data['Trigger_ks']) / sim_data['ADS'],
                    999
                )
                sim_data['Dni_do_spatneho_zavozu'] = sim_data['Dni_do_spatneho_zavozu'].clip(lower=1).round().astype(int)

                # Filtrujeme len tie, ktoré sa vrátia v rámci horizontu simulácie
                recall_in_scope = sim_data[sim_data['Dni_do_spatneho_zavozu'] <= sim_horizon_days].copy()

                # Vytvoríme časovú os po dňoch (1 až sim_horizon_days)
                timeline_days = list(range(1, sim_horizon_days + 1))
                daily_schedule = []

                # 1. Rozplánovanie VÝVOZU (Outbound) - prioritne SWAP
                outbound_pallets_total = total_pallets
                days_needed_for_outbound = int(np.ceil(outbound_pallets_total / daily_logistics_capacity)) if daily_logistics_capacity > 0 else 0

                remaining_outbound = outbound_pallets_total
                outbound_by_day = {}
                for d in timeline_days:
                    if remaining_outbound > 0:
                        moved_today = min(remaining_outbound, daily_logistics_capacity)
                        outbound_by_day[d] = moved_today
                        remaining_outbound -= moved_today
                    else:
                        outbound_by_day[d] = 0

                # 2. Rozplánovanie SPÄTNÉHO ZÁVOZU (Inbound 1 paleta na požiadavku)
                inbound_by_day = {d: 0 for d in timeline_days}
                for _, row in recall_in_scope.iterrows():
                    d = row['Dni_do_spatneho_zavozu']
                    if d in inbound_by_day:
                        # Keď klesne pod limit, potrebujeme z 4DS priviezť 1 paletu
                        inbound_by_day[d] += 1

                # Zostavenie tabuľky vyťaženia
                for d in timeline_days:
                    out_p = outbound_by_day.get(d, 0)
                    in_p = inbound_by_day.get(d, 0)
                    tot_p = out_p + in_p
                    daily_schedule.append({
                        'Deň': f"Deň {d}",
                        'Deň_cislo': d,
                        'Vývoz do 4DS (pal)': out_p,
                        'Spätný dovoz z 4DS (pal)': in_p,
                        'Celkom preprava (pal)': tot_p,
                        'Preťaženie': tot_p > daily_logistics_capacity
                    })

                schedule_df = pd.DataFrame(daily_schedule)

                # === METRIKY SIMULÁCIE ===
                s_col1, s_col2, s_col3, s_col4 = st.columns(4)
                
                first_recall_day = recall_in_scope['Dni_do_spatneho_zavozu'].min() if not recall_in_scope.empty else "Nespustí sa"
                overloaded_days = schedule_df[schedule_df['Preťaženie'] == True]['Deň_cislo'].count()

                s_col1.metric("Doba vývozu do 4DS", f"{days_needed_for_outbound} dní")
                s_col2.metric("Prvý spätný závoz o", f"{first_recall_day} dní" if isinstance(first_recall_day, (int, float, np.integer)) else first_recall_day)
                s_col3.metric("Paliet na spätný závoz (v horizonte)", f"{recall_in_scope['Plné palety na presun'].count()} pal")
                s_col4.metric("Dni s preťažením kapacity", f"{overloaded_days} dní", delta="⚠️ Pozor" if overloaded_days > 0 else "OK", delta_color="inverse" if overloaded_days > 0 else "normal")

                st.divider()

                # === GRAF VYŤAŽENIA DOPRAVY V ČASE ===
                st.subheader("📊 Denné vyťaženie (Vývoz vs Spätný tok)")
                
                fig_timeline = go.Figure()
                
                # Stĺpce Vývozu
                fig_timeline.add_trace(go.Bar(
                    x=schedule_df['Deň_cislo'],
                    y=schedule_df['Vývoz do 4DS (pal)'],
                    name='Vývoz do 4DS',
                    marker_color='#1f77b4'
                ))
                
                # Stĺpce Spätného toku
                fig_timeline.add_trace(go.Bar(
                    x=schedule_df['Deň_cislo'],
                    y=schedule_df['Spätný dovoz z 4DS (pal)'],
                    name='Spätný dovoz z 4DS',
                    marker_color='#ff7f0e'
                ))
                
                # Čiara kapacity
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
                    yaxis_title='Počet paliet na prepravu',
                    height=400,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig_timeline, use_container_width=True)

                st.divider()

                # === TABUĽKA PLÁNU SPÄTNÝCH ZÁVOZOV ===
                st.subheader("📅 Harmonogram produktov vyžadujúcich spätný závoz z 4DS")
                if not recall_in_scope.empty:
                    recall_display = recall_in_scope.sort_values(by='Dni_do_spatneho_zavozu')[
                        ['Produkt', 'Dni_do_spatneho_zavozu', 'Skladom_po_vyvoze', 'ADS', 'Plné palety na presun', 'SWAP_lokacie']
                    ].copy()
                    
                    recall_display.columns = [
                        'Produkt', 'Potrebný závoz o (dní)', 'Zostane na primáre (ks)', 
                        'Denný predaj (ks/deň)', 'Palety v 4DS', 'Pôvodné SWAP Lokácie'
                    ]
                    
                    st.dataframe(recall_display, use_container_width=True, hide_index=True)
                else:
                    st.success("✅ V sledovanom horizonte nebude potrebné z 4DS doviezť späť žiadny tovar.")

    except Exception as e:
        st.error(f"⚠️ Nastal problém pri spracovaní súboru: {e}")

else:
    st.info("👈 Nahraj Excel súbory v ľavom menu alebo pridaj `datatest.xlsx` a `swap.xlsx` / `swp.xlsx` do repozitára.")
