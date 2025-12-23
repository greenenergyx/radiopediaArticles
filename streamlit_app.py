import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import streamlit.components.v1 as components

st.set_page_config(page_title="Radio Tracker", page_icon="🩻", layout="wide")

st.markdown("""
    <style>
        .stDataEditor {max-height: 400px; overflow-y: auto;}
        .block-container {padding-top: 1rem; padding-bottom: 1rem;}
    </style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_google_sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client


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


st.title("🩻 Radio Études")

try:
    sheet_url = st.secrets["private_sheet_url"]
except:
    st.error("Il manque l'URL du sheet dans les secrets.")
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
    df['read_status'] = df['read_status'].apply(lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)
    df['flashcards_made'] = df['flashcards_made'].apply(
        lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)

    col1, col2 = st.columns([1.3, 1])

    with col1:
        st.subheader("1. Liste des articles")
        search_query = st.text_input("🔍 Rechercher", "", placeholder="Ex: Neuro, Lung...")

        if search_query:
            filtered_df = df[
                df['title'].str.contains(search_query, case=False, na=False) |
                df['system'].str.contains(search_query, case=False, na=False)
                ]
        else:
            filtered_df = df.head(50)

        list_articles = filtered_df['title'].tolist()

        selected_article_title = st.selectbox(
            "👉 Choisir un article à lire :",
            options=list_articles if list_articles else ["Aucun article trouvé"]
        )

        st.write("Coche les cases ci-dessous :")
        edited_df = st.data_editor(
            filtered_df,
            column_config={
                "rid": None, "content": None, "remote_last_mod_date": None, "section": None,
                "url": None,
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
        if st.button("💾 Enregistrer les changements", type="primary"):
            with st.spinner("Sauvegarde en cours..."):
                try:
                    changes = st.session_state["editor"]["edited_rows"]
                    if not changes:
                        st.info("Aucun changement détecté.")
                    else:
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

                        st.success("C'est enregistré ! Rechargement...")
                        del st.session_state.df
                        st.rerun()

                except Exception as e:
                    st.error(f"Erreur sauvegarde : {e}")

    with col2:
        st.subheader("📖 Lecture")

        selected_url = None
        if selected_article_title and selected_article_title != "Aucun article trouvé":
            row = filtered_df[filtered_df['title'] == selected_article_title].iloc[0]
            selected_url = row['url']

        if selected_url:
            try:
                components.iframe(selected_url, height=800, scrolling=True)
                st.caption(f"Lien direct : [Ouvrir sur Radiopaedia]({selected_url})")
            except Exception:
                st.warning("Impossible d'afficher le site ici.")
                st.markdown(f"[Clique ici pour ouvrir]({selected_url})")
        else:
            st.info("Choisis un article à gauche.")