import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import streamlit.components.v1 as components

# --- CONFIGURATION ---
st.set_page_config(page_title="Radio Tracker", page_icon="🩻", layout="wide")

# CSS pour optimiser l'espace sur mobile
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
# On utilise session_state pour se souvenir de quel article est ouvert
if "selected_url" not in st.session_state:
    st.session_state.selected_url = None

# --- DÉBUT APP ---
st.title("🩻 Radio Études")

try:
    sheet_url = st.secrets["private_sheet_url"]
except:
    st.error("URL manquante dans les secrets.")
    st.stop()

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
    # Nettoyage des booléens
    df['read_status'] = df['read_status'].apply(lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)
    df['flashcards_made'] = df['flashcards_made'].apply(
        lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)

    # ASTUCE : On ajoute une colonne temporaire "Voir" juste pour l'interface
    # Elle n'existe pas dans Google Sheet, on la crée à la volée
    if 'Voir' not in df.columns:
        df.insert(0, 'Voir', False)

    # --- LAYOUT ---
    col1, col2 = st.columns([1.3, 1])

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

        st.caption("Coche la case **👁️ Voir** pour ouvrir l'article à droite.")

        # --- TABLEAU INTERACTIF ---
        edited_df = st.data_editor(
            filtered_df,
            column_config={
                "rid": None, "content": None, "remote_last_mod_date": None, "section": None,
                "url": None,  # On cache l'URL brute
                "Voir": st.column_config.CheckboxColumn("👁️", width="small", help="Coche pour voir"),
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

        # --- LOGIQUE DE DÉTECTION DES CLICS ---
        # On regarde ce qui vient d'être modifié
        changes = st.session_state["editor"]["edited_rows"]

        # Si quelque chose a changé...
        if changes:
            need_save = False

            for index_in_view, changes_dict in changes.items():
                # 1. EST-CE QUE C'EST UN CLIC SUR "VOIR" ?
                if "Voir" in changes_dict and changes_dict["Voir"] == True:
                    # Bingo ! L'utilisateur a coché "Voir"
                    original_row_index = filtered_df.index[index_in_view]
                    # On met à jour l'URL à afficher
                    st.session_state.selected_url = df.iloc[original_row_index]['url']

                    # Petit hack : on décoche la case "Voir" tout de suite dans la mémoire
                    # pour que ça agisse comme un bouton (clic -> action -> reset)
                    # Note : Visuellement ça restera coché jusqu'au prochain rechargement complet, mais ce n'est pas grave

                # 2. EST-CE QUE C'EST UNE SAUVEGARDE (Lu / Flashcard / Notes) ?
                if any(k in changes_dict for k in ['read_status', 'flashcards_made', 'notes']):
                    need_save = True

            # Si c'était une modif de données (pas juste le bouton voir), on sauvegarde
            if need_save:
                # Bouton de sauvegarde explicite pour éviter de trop écrire dans Google Sheets
                st.info("⚠️ Tu as modifié des statuts. N'oublie pas de cliquer sur Enregistrer ci-dessous.")

        st.write("---")
        if st.button("💾 Enregistrer les statuts", type="primary"):
            with st.spinner("Sauvegarde..."):
                try:
                    headers = worksheet.row_values(1)
                    # On repasse sur tous les changements
                    for index_in_view, changes_dict in changes.items():
                        # On ignore la colonne "Voir" car on ne la sauvegarde pas dans Google Sheet
                        if "Voir" in changes_dict:
                            continue

                        if not changes_dict: continue

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
                    st.rerun()  # On recharge pour rafraîchir le tableau
                except Exception as e:
                    st.error(f"Erreur : {e}")

    # --- VISUALISEUR ---
    with col2:
        st.subheader("📖 Lecture")

        url_to_show = st.session_state.selected_url

        if url_to_show:
            try:
                # On affiche l'iframe
                components.iframe(url_to_show, height=800, scrolling=True)
                st.caption(f"Lien : [Ouvrir sur Radiopaedia]({url_to_show})")
            except Exception:
                st.warning("Impossible d'afficher le site ici.")
                st.markdown(f"[Clique ici pour ouvrir]({url_to_show})")
        else:
            st.info("👈 Coche l'œil (👁️) sur une ligne pour afficher l'article ici.")