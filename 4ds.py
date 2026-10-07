import streamlit as st
import pandas as pd
import numpy as np
import io
import os

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
            # Oprava číselných stĺpcov (ak sú v exceli ako text s čiarkou "0,009")
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

            # === VÝPOČTY PRE KAŽDÉ SKU ===
            # 1. Denný predaj (ADS)
            sku_df['ADS'] = sku_df['Predajnosť'] / sales_period_days
            
            # 2. Potrebná zásoba (ks)
            sku_df['Potrebná zásoba (ks)'] = sku_df['ADS'] * target_doh
            
            # 3. Nadzásoba v kusoch (nie záporná)
            sku_df['Nadzásoba (ks)'] = (sku_df['Skladom'] - sku_df['Potrebná zásoba (ks)']).clip(lower=0)
            
            # 4. Nadzásoba v m3
            sku_df['Nadzásoba (m3)'] = sku_df['Nadzásoba (ks)'] * sku_df['Objem v M3']
            
            # 5. Kusov na 1 paletu (1m3 / Objem v M3)
            sku_df['Kusov na palete'] = np.where(sku_df['Objem v M3'] > 0, np.floor(1 / sku_df['Objem v M3']), 0)
            
            # 6. Počet plných paliet na presun
            sku_df['Plné palety na presun'] = np.floor(sku_df['Nadzásoba (m3)']).astype(int)
            
            # 7. Celkový počet kusov na presun
            sku_df['Kusov na presun'] = sku_df['Plné palety na presun'] * sku_df['Kusov na palete']
            
            # 8. Nadzásoba v dňoch (Ležiak faktor)
            sku_df['Nadzásoba v dňoch'] = np.where(sku_df['ADS'] > 0, sku_df['Nadzásoba (ks)'] / sku_df['ADS'], 9999)
            sku_df['Nadzásoba v dňoch'] = sku_df['Nadzásoba v dňoch'].round(1)

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
