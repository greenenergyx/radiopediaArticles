import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- CONFIGURATION ---
st.set_page_config(page_title="Radio Tracker", page_icon="🩻", layout="wide")


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
st.title("🩻 Radio Études - Mode Liste")

sheet_url = st.secrets["private_sheet_url"]

if "client" not in st.session_state:
    st.session_state.client = get_google_sheet_client()

# On charge les données
if "df" not in st.session_state:
    df_load, worksheet = load_data(st.session_state.client, sheet_url)
    st.session_state.df = df_load
    st.session_state.worksheet = worksheet
else:
    # On garde le worksheet accessible
    if st.session_state.worksheet is None:
        _, st.session_state.worksheet = load_data(st.session_state.client, sheet_url)

df = st.session_state.df
worksheet = st.session_state.worksheet

if df is not None:
    # 1. Barre de recherche
    search_query = st.text_input("🔍 Filtrer (Titre, Système...)", "", placeholder="Ex: Neuro, Lung, Anatomy...")

    # 2. Préparation des données pour l'éditeur
    # On convertit les colonnes de statut en Vrai/Faux (Booléen) pour avoir des cases à cocher
    # Si dans ton Excel c'est écrit "Oui", ça devient True (Coché), sinon False
    df['read_status'] = df['read_status'].apply(lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)
    df['flashcards_made'] = df['flashcards_made'].apply(
        lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)

    # Filtrage
    if search_query:
        filtered_df = df[
            df['title'].str.contains(search_query, case=False, na=False) |
            df['system'].str.contains(search_query, case=False, na=False)
            ]
    else:
        filtered_df = df.head(50)  # On affiche les 50 premiers par défaut pour ne pas surcharger

    # 3. L'ÉDITEUR DE DONNÉES (La pièce maîtresse)
    st.caption("Modifie les cases ci-dessous et clique sur 'Enregistrer les modifications' en bas.")

    edited_df = st.data_editor(
        filtered_df,
        column_config={
            "rid": None,  # On cache l'ID technique
            "content": None,  # On cache le texte trop long
            "remote_last_mod_date": None,
            "section": None,
            "url": st.column_config.LinkColumn(
                "Lien", display_text="Ouvrir"
            ),
            "title": st.column_config.TextColumn(
                "Titre", width="medium", disabled=True  # On empêche de modifier le titre
            ),
            "system": st.column_config.TextColumn(
                "Système", width="small", disabled=True
            ),
            "read_status": st.column_config.CheckboxColumn(
                "Lu ?", width="small"
            ),
            "flashcards_made": st.column_config.CheckboxColumn(
                "Flashcard ?", width="small"
            ),
            "notes": st.column_config.TextColumn(
                "Mes Notes", width="large"
            ),
            "last_access": st.column_config.TextColumn(
                "Dernier accès", disabled=True
            )
        },
        hide_index=True,
        use_container_width=True,
        key="editor"
    )

    # 4. SAUVEGARDE
    if st.button("💾 Enregistrer les modifications", type="primary"):
        with st.spinner("Sauvegarde en cours sur Google Sheets..."):
            try:
                # On compare les données originales filtrées avec les données éditées
                # Pour chaque ligne modifiée, on met à jour Google Sheets

                # On récupère les changements
                changes = st.session_state["editor"]["edited_rows"]

                if not changes:
                    st.warning("Aucune modification détectée.")
                else:
                    # Pour chaque changement (index de la ligne dans la vue filtrée -> nouvelles valeurs)
                    for index_in_view, changes_dict in changes.items():
                        # Retrouver la vraie ligne originale grâce à l'index
                        original_row_index = filtered_df.index[index_in_view]
                        real_rid = df.iloc[original_row_index]['rid']

                        # Trouver la ligne dans Google Sheet via le RID (plus sûr)
                        cell = worksheet.find(str(real_rid))
                        row_number = cell.row

                        # Mettre à jour les colonnes modifiées
                        headers = worksheet.row_values(1)

                        for col_name, new_value in changes_dict.items():
                            # Si c'est un booléen (case à cocher), on remet "Oui" ou "" pour le CSV
                            if col_name in ['read_status', 'flashcards_made']:
                                val_to_write = "Oui" if new_value else ""
                            else:
                                val_to_write = new_value

                            col_index = headers.index(col_name) + 1
                            worksheet.update_cell(row_number, col_index, val_to_write)

                        # Mettre à jour la date d'accès
                        col_access = headers.index('last_access') + 1
                        worksheet.update_cell(row_number, col_access, str(datetime.now()))

                    st.success("✅ Sauvegarde terminée !")
                    # On vide le cache pour forcer le rechargement des données
                    del st.session_state.df
                    st.rerun()

            except Exception as e:
                st.error(f"Erreur lors de la sauvegarde : {e}")