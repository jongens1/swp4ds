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
st.markdown("Aplikácia na výpočet a výber plných paliet ($1\\text{ m}^3$) určených na presun do 4DS.")

# --- BOČNÝ PANEL: NASTAVENIA ---
st.sidebar.header("⚙️ Nastavenia výpočtu")

# Upload súboru
uploaded_file = st.sidebar.file_uploader(
    "Nahrataj Excel súbor (.xlsx)", 
    type=["xlsx", "xls"]
)

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
    value=5,
    help="Ak produkt nemá žiadny predaj (0 ks), výpočet bude počítať s týmto odhadom predaja za mesiac."
)

# --- URČENIE ZDROJA DÁT ---
df = None
data_source_label = ""

if uploaded_file is not None:
    df = pd.read_excel(uploaded_file)
    data_source_label = f"Nahranný súbor (`{uploaded_file.name}`)"
elif os.path.exists("datatest.xlsx"):
    df = pd.read_excel("datatest.xlsx")
    data_source_label = "Predvolené dáta (`datatest.xlsx`)"

# --- HLAVNÁ LOGIKA ---
if df is not None:
    try:
        st.sidebar.success(f"📁 Použité dáta: **{data_source_label}**")
        
        # Očistenie názvov stĺpcov od medzier
        df.columns = df.columns.astype(str).str.strip()
        
        # Kontrola povinných stĺpcov
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

            # === FILTROVANIE A ZORADENIE ===
            filtered_df = sku_df[sku_df['Plné palety na presun'] >= min_pallets_to_move].copy()
            filtered_df = filtered_df.sort_values(by=['Plné palety na presun', 'Nadzásoba v dňoch'], ascending=[False, False])

            # === ZOBRAZENIE METRÍK ===
            st.subheader("📊 Súhrn pre presun")
            col1, col2, col3, col4 = st.columns(4)
            
            total_pallets = filtered_df['Plné palety na presun'].sum()
            total_items = filtered_df['Kusov na presun'].sum()
            total_m3 = (filtered_df['Plné palety na presun'] * 1.0).sum()
            total_skus = len(filtered_df)

            col1.metric("Celkom paliet na presun", f"{total_pallets:,} pal".replace(",", " "))
            col2.metric("Celkom objem", f"{total_m3:,.1f} m³".replace(",", " "))
            col3.metric("Celkom kusov", f"{int(total_items):,} ks".replace(",", " "))
            col4.metric("Počet dotknutých SKU", f"{total_skus} SKU")

            st.divider()

            # === GRAFY / VIZUALIZÁCIE ===
            if not filtered_df.empty:
                st.subheader("📈 Vizualizácia a analýza nadzásob")
                g_col1, g_col2 = st.columns(2)

                # GRAF 1: TOP 10 SKU podľa počtu paliet
                with g_col1:
                    top10_df = filtered_df.head(10).sort_values(by='Plné palety na presun', ascending=True)
                    fig_top10 = px.bar(
                        top10_df,
                        x='Plné palety na presun',
                        y='Produkt',
                        orientation='h',
                        title="Top 10 SKU s najväčším počtom paliet na presun",
                        labels={'Plné palety na presun': 'Počet paliet (ks)', 'Produkt': 'SKU'},
                        color='Plné palety na presun',
                        color_continuous_scale='Blues'
                    )
                    fig_top10.update_layout(showlegend=False, height=380)
                    st.plotly_chart(fig_top10, use_container_width=True)

                # GRAF 2: Kategórie ležiakov podľa závažnosti
                with g_col2:
                    # Rozdelenie do kategórií
                    bins = [0, 60, 180, 365, np.inf]
                    labels = ['Mierna (30-60 dní)', 'Stredná (60-180 dní)', 'Vysoká (180-365 dní)', 'Kritická (365+ dní)']
                    
                    filtered_df['Kategória ležiaka'] = pd.cut(filtered_df['Nadzásoba v dňoch'], bins=bins, labels=labels)
                    cat_summary = filtered_df.groupby('Kategória ležiaka', observed=False)['Plné palety na presun'].sum().reset_index()

                    fig_pie = px.pie(
                        cat_summary,
                        values='Plné palety na presun',
                        names='Kategória ležiaka',
                        title="Rozdelenie paliet podľa veku ležiaka (DOH)",
                        hole=0.4,
                        color_discrete_sequence=px.colors.sequential.RdBu_r
                    )
                    fig_pie.update_layout(height=380)
                    st.plotly_chart(fig_pie, use_container_width=True)

                st.divider()

            # === ZOBRAZENIE TABUĽKY ===
            st.subheader(f"📋 Zoznam produktov na presun do 4DS (Plné palety ≥ {min_pallets_to_move})")
            
            display_cols = [
                'Sklad', 'Produkt', 'Lokace', 'Skladom', 
                'Predajnosť', 'Kusov na palete', 'Nadzásoba v dňoch', 
                'Plné palety na presun', 'Kusov na presun'
            ]
            
            st.dataframe(
                filtered_df[display_cols],
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
    st.info("👈 Nahraj Excel súbor v ľavom menu alebo pridaj `datatest.xlsx` do repozitára pre zahájenie výpočtu.")
