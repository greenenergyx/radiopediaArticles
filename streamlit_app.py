import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import streamlit.components.v1 as components
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Radio Tracker", page_icon="🩻", layout="wide")

st.markdown("""
    <style>
        .stDataEditor {max-height: 400px; overflow-y: auto;}
        .block-container {padding-top: 1rem; padding-bottom: 1rem;}
    </style>
""", unsafe_allow_html=True)


# --- CONNEXION ---
@st.cache_resource
def get_google_sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client


# --- CHARGEMENT ---
def load_data(client, sheet_url):
    try:
        sh = client.open_by_url(sheet_url)
        worksheet = sh.get_worksheet(0)
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        return df, worksheet
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        return None, None


# --- VARIABLES DE SESSION ---
if "current_url" not in st.session_state:
    st.session_state.current_url = None

# --- DÉBUT APP ---
st.title("🩻 Radio Études")

try:
    sheet_url = st.secrets["private_sheet_url"]
except:
    st.error("URL manquante dans les secrets.")
    st.stop()

if "client" not in st.session_state:
    st.session_state.client = get_google_sheet_client()

# Chargement des données brutes
if "df" not in st.session_state:
    df_load, worksheet = load_data(st.session_state.client, sheet_url)
    st.session_state.df = df_load
    st.session_state.worksheet = worksheet
else:
    if st.session_state.worksheet is None:
        _, st.session_state.worksheet = load_data(st.session_state.client, sheet_url)

df_base = st.session_state.df
worksheet = st.session_state.worksheet

if df_base is not None:
    # Nettoyage des types
    df_base['read_status'] = df_base['read_status'].apply(
        lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)
    df_base['flashcards_made'] = df_base['flashcards_made'].apply(
        lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)

    # --- PRÉPARATION AFFICHAGE ---

    # 1. NETTOYAGE PRÉVENTIF (Le correctif est ici)
    # Si par malheur df_base contient déjà "Voir" à cause d'une ancienne session, on l'enlève.
    if "Voir" in df_base.columns:
        df_base = df_base.drop(columns=["Voir"])
        st.session_state.df = df_base  # On met à jour la mémoire propre

    # 2. CRÉATION DE LA COPIE D'AFFICHAGE
    df_display = df_base.copy()

    # 3. INSERTION SÉCURISÉE
    if "Voir" not in df_display.columns:
        df_display.insert(0, "Voir", False)

    # Maintien de la coche "Voir" active
    if st.session_state.current_url:
        mask = df_display['url'] == st.session_state.current_url
        df_display.loc[mask, 'Voir'] = True

    # --- LAYOUT ---
    col1, col2 = st.columns([1.3, 1])

    with col1:
        st.subheader("1. Liste des articles")

        search_query = st.text_input("🔍 Rechercher", "", placeholder="Titre, système...", key="search_box")

        if search_query:
            filtered_df = df_display[
                df_display['title'].str.contains(search_query, case=False, na=False) |
                df_display['system'].str.contains(search_query, case=False, na=False)
                ]
        else:
            filtered_df = df_display.head(50)

        st.caption("⚡ Sauvegarde automatique activée.")

        # --- TABLEAU INTERACTIF ---
        edited_df = st.data_editor(
            filtered_df,
            column_config={
                "rid": None, "content": None, "remote_last_mod_date": None, "section": None,
                "url": None,
                "Voir": st.column_config.CheckboxColumn("👁️", width="small"),
                "title": st.column_config.TextColumn("Titre", disabled=True),
                "system": st.column_config.TextColumn("Système", width="small", disabled=True),
                "read_status": st.column_config.CheckboxColumn("Lu ?", width="small"),
                "flashcards_made": st.column_config.CheckboxColumn("Fait ?", width="small"),
                "notes": st.column_config.TextColumn("Notes", width="large"),
                "last_access": st.column_config.TextColumn("Dernier accès", disabled=True)
            },
            hide_index=True,
            use_container_width=True,
            key="editor"
        )

        # --- GESTION DES CHANGEMENTS (AUTOMATIQUE) ---
        changes = st.session_state["editor"]["edited_rows"]

        if changes:
            need_rerun = False

            for index_in_view, change_dict in changes.items():

                # A. CLIC SUR L'OEIL
                if "Voir" in change_dict and change_dict["Voir"] == True:
                    original_idx = filtered_df.index[index_in_view]
                    selected_url = df_base.iloc[original_idx]['url']
                    st.session_state.current_url = selected_url
                    need_rerun = True

                # B. MODIFICATION DE DONNÉES
                data_changes = {k: v for k, v in change_dict.items() if k != "Voir"}

                if data_changes:
                    try:
                        st.toast("⏳ Sauvegarde en cours...", icon="☁️")

                        original_idx = filtered_df.index[index_in_view]
                        real_rid = df_base.iloc[original_idx]['rid']

                        cell = worksheet.find(str(real_rid))
                        row_number = cell.row
                        headers = worksheet.row_values(1)

                        for col_name, new_value in data_changes.items():
                            val_to_write = "Oui" if new_value is True else ("" if new_value is False else new_value)
                            col_index = headers.index(col_name) + 1
                            worksheet.update_cell(row_number, col_index, val_to_write)

                        col_access = headers.index('last_access') + 1
                        worksheet.update_cell(row_number, col_access, str(datetime.now()))

                        st.toast("✅ Sauvegardé !", icon="💾")

                        del st.session_state.df
                        need_rerun = True

                    except Exception as e:
                        st.error(f"Erreur de sauvegarde : {e}")

            if need_rerun:
                st.rerun()

    # --- VISUALISEUR ---
    with col2:
        st.subheader("📖 Lecture")

        url = st.session_state.current_url

        if url:
            try:
                components.iframe(url, height=800, scrolling=True)
                st.caption(f"Lien externe : [Radiopaedia]({url})")
            except:
                st.warning("Erreur d'affichage.")
                st.markdown(f"[Ouvrir le lien]({url})")
        else:
            st.info("Sélectionne un article avec l'œil 👁️.")