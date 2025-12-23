import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import streamlit.components.v1 as components

# --- CONFIGURATION ---
st.set_page_config(page_title="Radio Tracker", page_icon="🩻", layout="wide")

# --- CSS POUR MOBILE ---
# Petit hack pour que le tableau ne prenne pas trop de place sur mobile
st.markdown("""
    <style>
        .stDataEditor {max-height: 400px; overflow-y: auto;}
    </style>
""", unsafe_allow_html=True)


# --- FONCTIONS DE CONNEXION ---
def get_google_sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client


# --- CHARGEMENT DES DONNÉES ---
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


# --- INTERFACE ---
st.title("🩻 Radio Études")

sheet_url = st.secrets["private_sheet_url"]

if "client" not in st.session_state:
    st.session_state.client = get_google_sheet_client()

if "df" not in st.session_state:
    df_load, worksheet = load_data(st.session_state.client, sheet_url)
    st.session_state.df = df_load
    st.session_state.worksheet = worksheet
else:
    if st.session_state.worksheet is None:
        _, st.session_state.worksheet = load_data(st.session_state.client, sheet_url)

df = st.session_state.df
worksheet = st.session_state.worksheet

if df is not None:
    # --- PRÉPARATION DES DONNÉES ---
    df['read_status'] = df['read_status'].apply(lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)
    df['flashcards_made'] = df['flashcards_made'].apply(
        lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)

    # --- MISE EN PAGE : COLONNES ---
    # Sur mobile, col1 sera en haut et col2 en bas. Sur PC, côte à côte.
    col1, col2 = st.columns([1.2, 1])

    selected_url = None

    with col1:
        st.subheader("1. Liste des articles")
        search_query = st.text_input("🔍 Rechercher", "", placeholder="Titre, système...")

        if search_query:
            filtered_df = df[
                df['title'].str.contains(search_query, case=False, na=False) |
                df['system'].str.contains(search_query, case=False, na=False)
                ]
        else:
            filtered_df = df.head(50)

        st.caption("👈 Clique sur la case vide à gauche d'une ligne pour ouvrir l'article.")

        # L'ÉDITEUR AVEC SÉLECTION
        edited_df = st.data_editor(
            filtered_df,
            column_config={
                "rid": None, "content": None, "remote_last_mod_date": None, "section": None,
                "url": None,  # On cache l'URL car on va l'ouvrir automatiquement
                "title": st.column_config.TextColumn("Titre", disabled=True),
                "system": st.column_config.TextColumn("Système", width="small", disabled=True),
                "read_status": st.column_config.CheckboxColumn("Lu ?", width="small"),
                "flashcards_made": st.column_config.CheckboxColumn("Flashcard ?", width="small"),
                "notes": st.column_config.TextColumn("Notes", width="large"),
                "last_access": st.column_config.TextColumn("Dernier accès", disabled=True)
            },
            hide_index=True,
            use_container_width=True,
            key="editor",
            selection_mode="single-row",  # Active la sélection d'une seule ligne
            on_select="rerun"  # Recharge la page quand on clique
        )

        # GESTION DE LA SÉLECTION
        # On regarde quelle ligne a été cliquée
        selection = st.session_state.editor.get("selection", {"rows": []})
        if selection["rows"]:
            row_idx = selection["rows"][0]
            # On récupère l'URL de la ligne sélectionnée
            selected_url = filtered_df.iloc[row_idx]["url"]
            selected_title = filtered_df.iloc[row_idx]["title"]

        # BOUTON SAUVEGARDE (Toujours dans la colonne 1)
        st.write("---")
        if st.button("💾 Enregistrer tout", type="primary"):
            with st.spinner("Sauvegarde..."):
                try:
                    changes = st.session_state["editor"]["edited_rows"]
                    if changes:
                        headers = worksheet.row_values(1)
                        for index_in_view, changes_dict in changes.items():
                            original_row_index = filtered_df.index[index_in_view]
                            real_rid = df.iloc[original_row_index]['rid']
                            cell = worksheet.find(str(real_rid))
                            row_number = cell.row

                            for col_name, new_value in changes_dict.items():
                                if col_name in ['read_status', 'flashcards_made']:
                                    val_to_write = "Oui" if new_value else ""
                                else:
                                    val_to_write = new_value
                                col_index = headers.index(col_name) + 1
                                worksheet.update_cell(row_number, col_index, val_to_write)

                            col_access = headers.index('last_access') + 1
                            worksheet.update_cell(row_number, col_access, str(datetime.now()))

                        st.success("Sauvegardé !")
                        del st.session_state.df
                        st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")

    # --- VISUALISEUR (COLONNE 2) ---
    with col2:
        if selected_url:
            st.subheader(f"📖 {selected_title}")
            # C'est ici qu'on tente d'afficher le site
            # Height = 800px pour avoir de la place pour lire
            try:
                components.iframe(selected_url, height=800, scrolling=True)
                st.caption(f"Si l'article ne s'affiche pas, [clique ici pour l'ouvrir]({selected_url})")
            except:
                st.warning("Ce site refuse de s'ouvrir ici.")
                st.markdown(f"[Ouvrir l'article dans un nouvel onglet]({selected_url})")
        else:
            st.info("Sélectionne une ligne dans le tableau à gauche pour voir l'article ici.")