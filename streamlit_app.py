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

    # Création de la colonne "Voir" si elle n'existe pas (initialisée à False partout)
    if 'Voir' not in df.columns:
        df['Voir'] = False

    # --- LOGIQUE DE SÉLECTION UNIQUE (Le correctif est ici) ---
    # On vérifie si l'utilisateur a cliqué sur quelque chose dans le tableau
    if "editor" in st.session_state:
        changes = st.session_state["editor"]["edited_rows"]

        # On cherche s'il y a un clic sur "Voir"
        clicked_index = None
        for idx, change in changes.items():
            if "Voir" in change and change["Voir"] == True:
                clicked_index = int(idx)
                break  # On prend le premier qu'on trouve et on arrête

        # Si on a trouvé un clic sur "Voir"
        if clicked_index is not None:
            # 1. On détermine quelle ligne RÉELLE (dans le DF global) correspond à la ligne affichée
            # Il faut refaire le filtrage pour avoir les bons index
            # (Note: c'est une petite répétition nécessaire pour la précision)
            search_temp = st.session_state.get("search_key", "")
            if search_temp:
                temp_filtered = df[
                    df['title'].str.contains(search_temp, case=False, na=False) |
                    df['system'].str.contains(search_temp, case=False, na=False)
                    ]
            else:
                temp_filtered = df.head(50)

            # On chope l'index réel
            real_index = temp_filtered.index[clicked_index]

            # 2. On remet TOUT le monde à False
            st.session_state.df['Voir'] = False

            # 3. On met juste la ligne cliquée à True
            st.session_state.df.at[real_index, 'Voir'] = True

            # 4. On force le rechargement immédiat pour mettre à jour l'affichage
            # (Cela va "décocher" visuellement les autres cases)
            st.rerun()

    # --- LAYOUT ---
    col1, col2 = st.columns([1.3, 1])

    with col1:
        st.subheader("1. Liste des articles")

        # On utilise une clé pour le champ de recherche pour s'en souvenir lors du rerun
        search_query = st.text_input("🔍 Rechercher", "", placeholder="Titre, système...", key="search_key")

        if search_query:
            filtered_df = df[
                df['title'].str.contains(search_query, case=False, na=False) |
                df['system'].str.contains(search_query, case=False, na=False)
                ]
        else:
            filtered_df = df.head(50)

        st.caption("Coche l'œil (👁️) pour afficher. Une seule ligne active à la fois.")

        # --- TABLEAU INTERACTIF ---
        edited_df = st.data_editor(
            filtered_df,
            column_config={
                "rid": None, "content": None, "remote_last_mod_date": None, "section": None,
                "url": None,
                "Voir": st.column_config.CheckboxColumn("👁️", width="small"),  # Plus besoin de help text
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

        st.write("---")
        if st.button("💾 Enregistrer les statuts", type="primary"):
            with st.spinner("Sauvegarde..."):
                try:
                    # On récupère les changements depuis le widget
                    changes = st.session_state["editor"]["edited_rows"]

                    if changes:
                        headers = worksheet.row_values(1)
                        for index_in_view, changes_dict in changes.items():
                            # On ignore la colonne Voir pour la sauvegarde Google Sheet
                            if "Voir" in changes_dict:
                                del changes_dict["Voir"]

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
                        del st.session_state.df  # On vide le cache pour forcer un rechargement propre
                        st.rerun()
                    else:
                        st.info("Aucun changement de statut à enregistrer.")

                except Exception as e:
                    st.error(f"Erreur : {e}")

    # --- VISUALISEUR ---
    with col2:
        st.subheader("📖 Lecture")

        # On cherche quelle ligne a "Voir" = True dans le DataFrame global
        selected_rows = df[df['Voir'] == True]

        if not selected_rows.empty:
            # On prend la première (et théoriquement unique) ligne sélectionnée
            url_to_show = selected_rows.iloc[0]['url']

            try:
                components.iframe(url_to_show, height=800, scrolling=True)
                st.caption(f"Lien : [Ouvrir sur Radiopaedia]({url_to_show})")
            except Exception:
                st.warning("Impossible d'afficher le site ici.")
                st.markdown(f"[Clique ici pour ouvrir]({url_to_show})")
        else:
            st.info("👈 Coche l'œil (👁️) sur une ligne pour afficher l'article ici.")