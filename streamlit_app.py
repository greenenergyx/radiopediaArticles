import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import streamlit.components.v1 as components
import re

# --- CONFIGURATION ---
st.set_page_config(page_title="Radio Tracker", page_icon="🩻", layout="wide")

st.markdown("""
    <style>
        .stDataEditor {max-height: 600px; overflow-y: auto;}
        .block-container {padding-top: 1rem; padding-bottom: 1rem;}
        div[data-testid="stExpander"] div[role="button"] p {
            font-size: 1.1rem;
            font-weight: 600;
        }
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


def get_unique_tags(df, column_name):
    """Récupère tous les tags uniques séparés par des virgules."""
    if column_name not in df.columns:
        return []
    all_text = ",".join(df[column_name].dropna().astype(str).tolist())
    tags = [t.strip() for t in all_text.split(',') if t.strip()]
    return sorted(list(set(tags)))


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

# Chargement initial
if "df" not in st.session_state:
    df_load, worksheet = load_data(st.session_state.client, sheet_url)

    # Nettoyage des booléens
    if df_load is not None:
        cols_to_bool = ['read_status', 'flashcards_made', 'ignored']
        if 'ignored' not in df_load.columns: df_load['ignored'] = False

        for col in cols_to_bool:
            if col in df_load.columns:
                df_load[col] = df_load[col].apply(lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)

    st.session_state.df = df_load
    st.session_state.worksheet = worksheet
else:
    if st.session_state.worksheet is None:
        _, st.session_state.worksheet = load_data(st.session_state.client, sheet_url)

df_base = st.session_state.df
worksheet = st.session_state.worksheet

if df_base is not None:
    # --- PRÉPARATION DE LA VUE ---

    # 1. Nettoyage de la colonne "Voir" si elle traîne
    if "Voir" in df_base.columns:
        df_base.drop(columns=["Voir"], inplace=True)

    # 2. Création du DF d'affichage
    df_display = df_base.copy()

    # 3. Ajout colonne Voir
    df_display.insert(0, "Voir", False)
    if st.session_state.current_url:
        mask = df_display['url'] == st.session_state.current_url
        df_display.loc[mask, 'Voir'] = True

    # --- ZONE DE FILTRES ---
    with st.expander("🔍 Filtres & Affichage", expanded=True):

        # Filtre de statut (Workflow)
        view_mode = st.radio(
            "Mode d'affichage :",
            ["📥 À traiter (Actifs)", "⛔ Ignorés / Suspendus", "📂 Tout voir"],
            horizontal=True
        )

        st.divider()

        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            unique_systems = get_unique_tags(df_base, 'system')
            selected_systems = st.multiselect("Filtrer par Système (ET)", unique_systems)
        with col_f2:
            unique_sections = get_unique_tags(df_base, 'section')
            selected_sections = st.multiselect("Filtrer par Section (ET)", unique_sections)
        with col_f3:
            search_query = st.text_input("Recherche texte", "", placeholder="Titre...")

    # --- LOGIQUE DE FILTRAGE ---

    # 1. FILTRE IGNORED
    df_display['ignored'] = df_display['ignored'].fillna(False).astype(bool)

    if view_mode == "📥 À traiter (Actifs)":
        filtered_df = df_display[~df_display['ignored']]
    elif view_mode == "⛔ Ignorés / Suspendus":
        filtered_df = df_display[df_display['ignored']]
    else:
        filtered_df = df_display

    # 2. SYSTÈME (Logique RESTRICTIVE "ET")
    if selected_systems:
        for sys_tag in selected_systems:
            # On filtre itérativement : il faut que le système SOIT présent à chaque tour
            filtered_df = filtered_df[
                filtered_df['system'].astype(str).str.contains(re.escape(sys_tag), case=False, regex=True)
            ]

    # 3. SECTION (Logique RESTRICTIVE "ET")
    if selected_sections:
        for sec_tag in selected_sections:
            filtered_df = filtered_df[
                filtered_df['section'].astype(str).str.contains(re.escape(sec_tag), case=False, regex=True)
            ]

    # 4. TEXTE
    if search_query:
        filtered_df = filtered_df[
            filtered_df['title'].str.contains(search_query, case=False, na=False)
        ]

    # Limite pour performance (optionnel, désactivé si beaucoup de filtres)
    if not selected_systems and not selected_sections and not search_query and len(filtered_df) > 200:
        filtered_df = filtered_df.head(200)

    # --- LAYOUT PRINCIPAL ---
    col1, col2 = st.columns([1.6, 1])

    with col1:
        st.subheader(f"Articles ({len(filtered_df)})")

        # --- CONFIGURATION DES COLONNES ---
        column_cfg = {
            "rid": None, "content": None, "remote_last_mod_date": None,
            "url": None,
            "Voir": st.column_config.CheckboxColumn("👁️", width="small"),
            "title": st.column_config.TextColumn("Titre", disabled=True),

            "system": st.column_config.TextColumn("Système", width="small", disabled=True),
            "section": st.column_config.TextColumn("Section", width="small", disabled=True),

            "ignored": st.column_config.CheckboxColumn("⛔", width="small", help="Ignorer/Suspendre"),
            "read_status": st.column_config.CheckboxColumn("Lu ?", width="small"),
            "flashcards_made": st.column_config.CheckboxColumn("Flash ?", width="small"),
            "notes": st.column_config.TextColumn("Notes", width="medium"),
            "last_access": st.column_config.TextColumn("Dernier accès", disabled=True)
        }

        edited_df = st.data_editor(
            filtered_df,
            column_config=column_cfg,
            hide_index=True,
            use_container_width=True,
            key="editor"
        )

        # --- GESTION DES CHANGEMENTS ---
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
                        st.toast("⏳ Sauvegarde...", icon="☁️")

                        # Retrouver la ligne réelle
                        original_idx = filtered_df.index[index_in_view]
                        real_rid = df_base.iloc[original_idx]['rid']

                        # 1. MISE À JOUR GOOGLE SHEET (Cloud)
                        cell = worksheet.find(str(real_rid))
                        row_number = cell.row
                        headers = worksheet.row_values(1)

                        for col_name, new_value in data_changes.items():
                            val_to_write = "Oui" if new_value is True else ("" if new_value is False else new_value)
                            if col_name in headers:
                                col_index = headers.index(col_name) + 1
                                worksheet.update_cell(row_number, col_index, val_to_write)

                                # 2. MISE À JOUR MÉMOIRE LOCALE
                                st.session_state.df.at[original_idx, col_name] = new_value

                        # Update date
                        col_access = headers.index('last_access') + 1
                        current_time = str(datetime.now())
                        worksheet.update_cell(row_number, col_access, current_time)

                        st.toast("✅ Sauvegardé !", icon="💾")

                        need_rerun = True

                    except Exception as e:
                        st.error(f"Erreur de sauvegarde : {e}")

            if need_rerun:
                st.rerun()

    # --- VISUALISEUR ---
    with col2:
        url = st.session_state.current_url
        if url:
            try:
                components.iframe(url, height=850, scrolling=True)
            except:
                st.markdown(f"[Ouvrir l'article]({url})")
        else:
            st.info("Sélectionne un article avec l'œil 👁️.")