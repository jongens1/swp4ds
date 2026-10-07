import streamlit as st
import pandas as pd
import numpy as np
import io
import os
import plotly.express as px

# Konfigurácia stránky
st.set_page_config(
    page_title="4DS Oversupply Optimizer",
    page_icon="📦",
    layout="wide"
)

st.title("📦 Optimizer presunu nadzásob do 4DS")
st.markdown("Aplikácia na výpočet nadzásob a nápočet hotových paliet zo **SWAP lokácií** určených na okamžitý presun do 4DS.")

# --- BOČNÝ PANEL: NASTAVENIA ---
st.sidebar.header("⚙️ Nastavenia výpočtu")

# Upload hlavného súboru
uploaded_file = st.sidebar.file_uploader(
    "1. Nahrataj hlavný Excel (zásoby)", 
    type=["xlsx", "xls"]
)

# Upload SWAP súboru
uploaded_swap_file = st.sidebar.file_uploader(
    "2. Nahrataj SWAP Excel (voliteľné)", 
    type=["xlsx", "xls"]
)

st.sidebar.divider()

# Parametre
target_doh = st.sidebar.number_input(
    "Cieľová zásoba na lokácii (Target DOH v dňoch)", 
    min_value=1, 
    max_value=365, 
    value=30
)

sales_period_days = st.sidebar.number_input(
    "Sledované obdobie predajnosti (v dňoch)", 
    min_value=1, 
    max_value=365, 
    value=30
)

min_pallets_to_move = st.sidebar.number_input(
    "Minimálny počet plných paliet na presun", 
    min_value=1, 
    max_value=50, 
    value=1
)

fallback_monthly_sales = st.sidebar.number_input(
    "Náhradná predajnosť pre nepredané produkty (ks/mesiac)",
    min_value=1,
    max_value=100,
    value=5
)

# --- URČENIE ZDROJA DÁT ---
df = None
swap_df = None
data_source_label = ""
swap_source_label = ""

# 1. Hlavný súbor
if uploaded_file is not None:
    df = pd.read_excel(uploaded_file)
    data_source_label = f"`{uploaded_file.name}`"
elif os.path.exists("datatest.xlsx"):
    df = pd.read_excel("datatest.xlsx")
    data_source_label = "`datatest.xlsx` (predvolený)"

# 2. SWAP súbor
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
    # Detekcia stĺpca GEOSIZE
    for col in swap_df.columns:
        if col.upper() == 'GEOSIZE':
            geosize_col = col
            break

    if geosize_col:
        unique_geosizes = swap_df[geosize_col].dropna().unique().tolist()
        st.sidebar.divider()
        st.sidebar.subheader("📐 Filter podla GEOSIZE (SWAP)")
        selected_geosizes = st.sidebar.multiselect(
            "Vyber požadované GEOSIZE:",
            options=unique_geosizes,
            default=unique_geosizes
        )
        # Filtrovanie SWAP dát podľa vybraného GEOSIZE
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
        else:
            st.sidebar.warning("⚠️ SWAP dáta neboli nájdené.")
        
        # Očistenie názvov stĺpcov
        df.columns = df.columns.astype(str).str.strip()
        
        required_cols = ['Sklad', 'Lokace', 'Produkt', 'Množstvo na lokácií', 'Skladom', 'Objem v M3', 'Predajnosť']
        missing_cols = [col for col in required_cols if col not in df.columns]
        
        if missing_cols:
            st.error(f"❌ V Exceli chýbajú tieto povinné stĺpce: {', '.join(missing_cols)}")
        else:
            # Oprava číselných stĺpcov
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
                if geosize_col:
                    sku_df['SWAP_geosize'] = sku_df['SWAP_geosize'].fillna('-')
                else:
                    sku_df['SWAP_geosize'] = '-'
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
            sku_df['Nadzásoba (ks)'] = (sku_df['Skladom'] - sku_df['Potrebná zásoba (ks)']).clip(lower=0)
            sku_df['Nadzásoba (m3)'] = sku_df['Nadzásoba (ks)'] * sku_df['Objem v M3']
            sku_df['Kusov na palete'] = np.where(sku_df['Objem v M3'] > 0, np.floor(1 / sku_df['Objem v M3']), 0)
            sku_df['Plné palety na presun'] = np.floor(sku_df['Nadzásoba (m3)']).astype(int)
            sku_df['Kusov na presun'] = sku_df['Plné palety na presun'] * sku_df['Kusov na palete']
            sku_df['Nadzásoba v dňoch'] = (sku_df['Nadzásoba (ks)'] / sku_df['ADS']).round(1)

            # Rozdelenie paliet na SWAP vs Štandardné lokácie
            sku_df['Paliet zo SWAP (IHNEĎ)'] = np.minimum(sku_df['Plné palety na presun'], sku_df['Paliet_na_SWAP'])
            sku_df['Paliet z bežných lokácií'] = sku_df['Plné palety na presun'] - sku_df['Paliet zo SWAP (IHNEĎ)']

            # === FILTROVANIE A ZORADENIE ===
            filtered_df = sku_df[sku_df['Plné palety na presun'] >= min_pallets_to_move].copy()
            filtered_df = filtered_df.sort_values(by=['Plné palety na presun', 'Paliet zo SWAP (IHNEĎ)'], ascending=[False, False])

            # === ZOBRAZENIE METRÍK ===
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

            # === ANALÝZA GEOSIZE a TOP 10 ===
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
                        # Prepojenie filtrovaných produktov so SWAP detailom pre presný počet paliet per GEOSIZE
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

            # === ZOBRAZENIE TABUĽKY ===
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
            
            st.dataframe(
                filtered_df[display_cols].rename(columns=rename_dict),
                use_container_width=True,
                hide_index=True
            )

            # === EXPORT DO EXCELU ===
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

    except Exception as e:
        st.error(f"⚠️ Nastal problém pri spracovaní súboru: {e}")

else:
    st.info("👈 Nahraj Excel súbory v ľavom menu alebo pridaj `datatest.xlsx` a `swap.xlsx` / `swp.xlsx` do repozitára.")
