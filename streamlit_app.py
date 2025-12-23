import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- CONFIGURATION ---
# Titre de la page et icône
st.set_page_config(page_title="Radio Tracker", page_icon="🩻")


# --- FONCTIONS DE CONNEXION ---
def get_google_sheet_client():
    # On récupère les secrets (la clé JSON) que nous configurerons sur le serveur
    # Cela permet de garder ton fichier sécurisé
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client


# --- CHARGEMENT DES DONNÉES ---
def load_data(client, sheet_url):
    try:
        sh = client.open_by_url(sheet_url)
        worksheet = sh.get_worksheet(0)  # Prend le premier onglet
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        return df, worksheet
    except Exception as e:
        st.error(f"Erreur de connexion au Sheet: {e}")
        return None, None


# --- INTERFACE UTILISATEUR ---
st.title("🩻 Radio Études")
st.write("Suivi de tes lectures et flashcards Radiopaedia")

# 1. Connexion
# L'URL sera aussi stockée dans les secrets pour ne pas la mettre dans le code public
sheet_url = st.secrets["private_sheet_url"]

if "client" not in st.session_state:
    st.session_state.client = get_google_sheet_client()

df, worksheet = load_data(st.session_state.client, sheet_url)

if df is not None:
    # 2. Recherche
    search_query = st.text_input("🔍 Rechercher un article (Titre ou Système)", "")

    # Filtrer les résultats
    if search_query:
        filtered_df = df[
            df['title'].str.contains(search_query, case=False, na=False) |
            df['system'].str.contains(search_query, case=False, na=False)
            ]
    else:
        filtered_df = df.head(10)  # Affiche les 10 premiers si pas de recherche

    st.write(f"Articles trouvés : {len(filtered_df)}")

    # 3. Affichage des résultats
    # On utilise un selectbox pour choisir l'article à modifier (plus facile sur mobile)
    article_options = filtered_df['title'].tolist()

    if article_options:
        selected_article_title = st.selectbox("Choisir un article à mettre à jour :", article_options)

        # Récupérer les infos de l'article sélectionné
        selected_row = filtered_df[filtered_df['title'] == selected_article_title].iloc[0]
        st.info(f"Système : {selected_row['system']}")

        # Lien cliquable vers l'article
        st.markdown(f"👉 **[Ouvrir l'article sur Radiopaedia]({selected_row['url']})**")

        # --- FORMULAIRE DE MISE À JOUR ---
        with st.form("update_form"):
            st.write("Statut :")

            # On vérifie l'état actuel (TRUE si la case contient quelque chose, FALSE sinon)
            is_read = str(selected_row['read_status']).strip() != ""
            is_flashcard = str(selected_row['flashcards_made']).strip() != ""

            new_read = st.checkbox("✅ Lu", value=is_read)
            new_flashcard = st.checkbox("🧠 Flashcard Créée", value=is_flashcard)
            notes = st.text_area("Notes", value=str(selected_row['notes']))

            submitted = st.form_submit_button("Enregistrer les changements")

            if submitted:
                # Retrouver l'index de la ligne dans le Google Sheet (on ajoute 2 car gspread commence à 1 et il y a l'entête)
                # Note : C'est une méthode simple. Pour 17000 lignes, ça peut être lent.
                # On utilise l'ID unique (rid) pour être sûr de la ligne.
                try:
                    cell = worksheet.find(str(selected_row['rid']))
                    row_number = cell.row

                    # Mise à jour des colonnes (Ajuste les lettres/index selon ton fichier réel)
                    # On cherche les index des colonnes basés sur les noms
                    headers = worksheet.row_values(1)
                    col_read = headers.index('read_status') + 1
                    col_flash = headers.index('flashcards_made') + 1
                    col_notes = headers.index('notes') + 1
                    col_access = headers.index('last_access') + 1

                    # Mettre à jour
                    worksheet.update_cell(row_number, col_read, "Oui" if new_read else "")
                    worksheet.update_cell(row_number, col_flash, "Oui" if new_flashcard else "")
                    worksheet.update_cell(row_number, col_notes, notes)
                    worksheet.update_cell(row_number, col_access, str(datetime.now()))

                    st.success("Mise à jour réussie ! Recharge la page pour voir les changements.")
                    # On vide le cache pour forcer le rechargement des données fraîches au prochain clic
                    st.cache_data.clear()

                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")

    else:
        st.warning("Aucun article trouvé.")