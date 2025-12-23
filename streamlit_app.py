import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date
import streamlit.components.v1 as components
import google.generativeai as genai
import re
import io
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Radiopaedia Cockpit", page_icon="🩻", layout="wide")

# CSS pour compacter l'interface
st.markdown("""
    <style>
        .block-container {padding-top: 1rem; padding-bottom: 2rem;}
        div[data-testid="stExpander"] div[role="button"] p {font-weight: bold;}
        .stButton button {width: 100%;}
        /* Réduire la taille des titres */
        h1 {font-size: 1.8rem !important;}
        h2 {font-size: 1.5rem !important;}
        h3 {font-size: 1.2rem !important;}
    </style>
""", unsafe_allow_html=True)


# --- FONCTIONS BACKEND ---
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
        worksheet = sh.get_worksheet(0)  # Articles
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        return df, worksheet, sh
    except Exception as e:
        st.error(f"Erreur connexion : {e}")
        return None, None, None


def load_cards_data(sh):
    try:
        worksheet_cards = sh.worksheet("Cards")
        data = worksheet_cards.get_all_records()
        df_cards = pd.DataFrame(data)
        if df_cards.empty:
            df_cards = pd.DataFrame(
                columns=['rid', 'article_title', 'system', 'card_type', 'question', 'answer', 'tags'])
        return df_cards, worksheet_cards
    except:
        return pd.DataFrame(), None


def get_unique_tags(df, column_name):
    if column_name not in df.columns: return []
    all_text = ",".join(df[column_name].dropna().astype(str).tolist())
    tags = [t.strip() for t in all_text.split(',') if t.strip()]
    return sorted(list(set(tags)))


# --- STATE MANAGEMENT ---
if "current_rid" not in st.session_state: st.session_state.current_rid = None
if "current_url" not in st.session_state: st.session_state.current_url = None
if "draft_cards" not in st.session_state: st.session_state.draft_cards = []
if "api_key" not in st.session_state: st.session_state.api_key = ""
if "selected_model" not in st.session_state: st.session_state.selected_model = "models/gemini-1.5-flash"

# --- SIDEBAR COMPACTE ---
with st.sidebar:
    st.header("⚙️ Config")
    if "GEMINI_API_KEY" in st.secrets:
        st.session_state.api_key = st.secrets["GEMINI_API_KEY"]
        st.success("API Key OK")
    else:
        st.session_state.api_key = st.text_input("Clé Gemini", type="password")

    # Modèle
    available_models = ["models/gemini-1.5-flash", "models/gemini-pro"]
    if st.session_state.api_key:
        try:
            genai.configure(api_key=st.session_state.api_key)
            all = genai.list_models()
            found = [m.name for m in all if 'generateContent' in m.supported_generation_methods]
            if found: available_models = sorted(found, reverse=True)
        except:
            pass
    st.session_state.selected_model = st.selectbox("Modèle IA", available_models)

    st.divider()

    # Export Rapide
    if "sh_obj" in st.session_state:
        if st.button("📥 Télécharger Anki"):
            df_c, _ = load_cards_data(st.session_state.sh_obj)
            if not df_c.empty:
                out = io.StringIO()
                out.write("#separator:Pipe\n#html:true\n#tags column:4\n")
                for _, r in df_c.iterrows():
                    q = str(r['question']).replace('|', '/')
                    a = str(r['answer']).replace('|', '/')
                    tag = str(r.get('tags', '')).strip() or str(r['article_title']).replace(' ', '_')
                    out.write(f"{q}|{a}|{r['card_type']}|{tag}\n")
                st.download_button("💾 .txt", data=out.getvalue(), file_name=f"anki_{date.today()}.txt")

# --- INITIALISATION ---
try:
    sheet_url = st.secrets["private_sheet_url"]
except:
    st.error("Configure les secrets.")
    st.stop()

if "client" not in st.session_state:
    st.session_state.client = get_google_sheet_client()

if "df" not in st.session_state:
    df_load, worksheet, sh_obj = load_data(st.session_state.client, sheet_url)
    if df_load is not None:
        # Standardisation booléens
        for c in ['read_status', 'flashcards_made', 'ignored']:
            if c not in df_load.columns:
                df_load[c] = False
            else:
                df_load[c] = df_load[c].apply(lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)
    st.session_state.df = df_load
    st.session_state.worksheet = worksheet
    st.session_state.sh_obj = sh_obj
else:
    if st.session_state.worksheet is None:
        _, st.session_state.worksheet, st.session_state.sh_obj = load_data(st.session_state.client, sheet_url)

df_base = st.session_state.df
worksheet = st.session_state.worksheet
sh_obj = st.session_state.sh_obj

# =========================================================
# SECTION 1 : LE TABLEAU DE BORD (TRACKER)
# =========================================================
st.title("🩻 Radiologie Cockpit")

if df_base is not None:
    # --- FILTRES (Dans un expander pour gagner de la place) ---
    with st.expander("🔍 Filtrer la liste", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        view_mode = c1.radio("Vue", ["📥 À faire", "✅ Fait", "📂 Tout"], horizontal=True)
        u_sys = get_unique_tags(df_base, 'system')
        sel_sys = c2.multiselect("Système", u_sys)
        u_sec = get_unique_tags(df_base, 'section')
        sel_sec = c3.multiselect("Section", u_sec)
        s_query = c4.text_input("Titre", "")

    # --- PRÉPARATION DU TABLEAU ---
    df_display = df_base.copy()

    # Gestion colonne "Voir" (Radio button hack)
    if "Voir" in df_display.columns: df_display.drop(columns=["Voir"], inplace=True)
    df_display.insert(0, "Voir", False)
    if st.session_state.current_rid:
        mask = df_display['rid'].astype(str) == str(st.session_state.current_rid)
        df_display.loc[mask, 'Voir'] = True

    # Filtrage
    df_display['ignored'] = df_display['ignored'].fillna(False).astype(bool)
    if view_mode == "📥 À faire":
        # On cache les ignorés ET ceux qui sont déjà lus+flashcardés
        df_display = df_display[~df_display['ignored']]
    elif view_mode == "✅ Fait":
        df_display = df_display[(df_display['read_status']) & (df_display['flashcards_made'])]

    if sel_sys:
        for s in sel_sys: df_display = df_display[
            df_display['system'].astype(str).str.contains(re.escape(s), case=False, regex=True)]
    if sel_sec:
        for s in sel_sec: df_display = df_display[
            df_display['section'].astype(str).str.contains(re.escape(s), case=False, regex=True)]
    if s_query:
        df_display = df_display[df_display['title'].str.contains(s_query, case=False, na=False)]

    if len(df_display) > 100: df_display = df_display.head(100)

    # --- TABLEAU INTERACTIF (Hauteur réduite) ---
    edited_df = st.data_editor(
        df_display,
        height=250,  # Compact !
        column_config={
            "rid": None, "content": None, "remote_last_mod_date": None, "url": None,
            "Voir": st.column_config.CheckboxColumn("👁️", width="small"),
            "title": st.column_config.TextColumn("Titre", disabled=True),
            "system": st.column_config.TextColumn("Système", width="small", disabled=True),
            "section": None,  # Caché pour gagner place
            "ignored": st.column_config.CheckboxColumn("⛔", width="small"),
            "read_status": st.column_config.CheckboxColumn("Lu ?", width="small"),
            "flashcards_made": st.column_config.CheckboxColumn("Flash ?", width="small"),
            "notes": st.column_config.TextColumn("Notes", width="medium"),
            "last_access": st.column_config.TextColumn("Dernier", disabled=True)
        },
        hide_index=True, use_container_width=True, key="editor"
    )

    # --- LOGIQUE DE MISE À JOUR (TRACKER) ---
    changes = st.session_state["editor"]["edited_rows"]
    if changes:
        need_rerun = False
        for idx_view, chg in changes.items():
            # 1. Sélection d'article (L'OEIL)
            if "Voir" in chg and chg["Voir"]:
                orig_idx = df_display.index[idx_view]
                row = df_base.iloc[orig_idx]
                st.session_state.current_rid = str(row['rid'])
                st.session_state.current_url = row['url']
                need_rerun = True

            # 2. Modif Données (Lu, Notes, etc.)
            data_chg = {k: v for k, v in chg.items() if k != "Voir"}
            if data_chg:
                try:
                    orig_idx = df_display.index[idx_view]
                    real_rid = df_base.iloc[orig_idx]['rid']

                    cell = worksheet.find(str(real_rid))
                    row_n = cell.row
                    headers = worksheet.row_values(1)

                    for k, v in data_chg.items():
                        val = "Oui" if v is True else ("" if v is False else v)
                        if k in headers:
                            worksheet.update_cell(row_n, headers.index(k) + 1, val)
                            st.session_state.df.at[orig_idx, k] = v  # Update local memory

                    # Update Date
                    worksheet.update_cell(row_n, headers.index('last_access') + 1, str(datetime.now()))
                    st.toast("Sauvegardé", icon="✅")
                    need_rerun = True
                except Exception as e:
                    st.error(f"Erreur save: {e}")

        if need_rerun: st.rerun()

# =========================================================
# SECTION 2 : L'ESPACE DE TRAVAIL (SPLIT VIEW)
# =========================================================

# Vérifie qu'un article est sélectionné
if st.session_state.current_rid:
    # On récupère les données de l'article sélectionné
    current_row = df_base[df_base['rid'].astype(str) == str(st.session_state.current_rid)].iloc[0]

    st.markdown("---")  # Séparateur visuel

    col_left, col_right = st.columns([1, 1])  # 50% / 50%

    # --- COLONNE GAUCHE : LECTURE ---
    with col_left:
        st.subheader(f"📖 {current_row['title']}")
        if current_row['url']:
            try:
                # Iframe haute pour lecture confortable
                components.iframe(current_row['url'], height=800, scrolling=True)
            except:
                st.warning("Le site bloque l'affichage.")
                st.markdown(f"[Ouvrir dans un onglet]({current_row['url']})")
        else:
            st.error("Pas d'URL pour cet article.")

    # --- COLONNE DROITE : ARCHITECTE IA ---
    with col_right:
        st.subheader("🧠 Générateur Flashcards")

        # Formulaire pour éviter les rechargements intempestifs
        with st.form("ai_form"):
            mode = st.radio("Format", ["Format A: Cloze (Trous)", "Format B: Liste Différentiel"], horizontal=True)
            custom_inst = st.text_input("Instruction spécifique (ex: focus sur l'anatomie)")
            submitted_gen = st.form_submit_button("✨ Générer les cartes", type="primary")

        if submitted_gen:
            if not st.session_state.api_key:
                st.error("Manque clé API !")
            else:
                try:
                    genai.configure(api_key=st.session_state.api_key)
                    model = genai.GenerativeModel(st.session_state.selected_model)

                    # SYSTEM PROMPT (Le tien, compacté pour le code)
                    sys_prompt = """
                    Role: Expert Radiology Tutor. Task: Create Anki cards.
                    Objective: Maximize retention, minimize card count.
                    1. Filter: Only Buzzwords, Critical Differentiators, Epidemiology, Associations.
                    2. Rules: 
                       - Format A (Cloze): {{c1::hidden}}. One fact per card.
                       - Format B (List): Bullet points.
                    3. Output: Pipe separator (|). No headers.
                       Structure: Question/Cloze | Extra/Answer | Tag
                    """

                    full_prompt = f"{sys_prompt}\nArticle: {current_row['title']}\nFormat: {mode}\nInstr: {custom_inst}\nText:\n{current_row['content']}"

                    with st.spinner("Analyse en cours..."):
                        resp = model.generate_content(full_prompt)
                        clean = resp.text.replace("```", "").strip()

                        new_batch = []
                        for l in clean.split('\n'):
                            if '|' in l:
                                p = l.split('|')
                                if len(p) >= 2:
                                    new_batch.append({
                                        "rid": str(current_row['rid']),
                                        "article_title": current_row['title'],
                                        "system": current_row['system'],
                                        "card_type": "Cloze" if "{{" in p[0] else "Basic",
                                        "question": p[0].strip(),
                                        "answer": p[1].strip(),
                                        "tags": p[2].strip() if len(p) > 2 else ""
                                    })

                        if new_batch:
                            st.session_state.draft_cards = new_batch  # Remplace les anciens brouillons
                            st.success(f"{len(new_batch)} cartes générées !")
                        else:
                            st.warning("Rien généré. Vérifie le texte.")
                except Exception as e:
                    st.error(f"Erreur IA: {e}")

        # --- PREVISUALISATION & SAUVEGARDE ---
        if st.session_state.draft_cards:
            st.divider()
            st.caption("Brouillon actuel :")
            draft_df = pd.DataFrame(st.session_state.draft_cards)

            # Editeur pour corriger les cartes avant sauvegarde
            edited_draft = st.data_editor(draft_df[['question', 'answer', 'tags']], num_rows="dynamic",
                                          key="draft_edit")

            col_save, col_clear = st.columns(2)

            # --- LE BOUTON MAGIQUE ---
            if col_save.button("💾 Valider & Marquer comme Fait", type="primary"):
                try:
                    _, ws_cards = load_cards_data(sh_obj)
                    if ws_cards:
                        # 1. Sauvegarde dans l'onglet Cards
                        rows = []
                        for idx, r in edited_draft.iterrows():
                            # On récupère les métadonnées originales du brouillon
                            orig = draft_df.iloc[idx]
                            rows.append(
                                [orig['rid'], orig['article_title'], orig['system'], orig['card_type'], r['question'],
                                 r['answer'], r['tags']])

                        ws_cards.append_rows(rows)

                        # 2. AUTO-UPDATE du statut "Flashcards Made" dans l'onglet Articles
                        # On trouve la ligne de l'article courant
                        cell = worksheet.find(str(current_row['rid']))
                        headers = worksheet.row_values(1)
                        col_flash = headers.index('flashcards_made') + 1

                        # Mise à jour Cloud
                        worksheet.update_cell(cell.row, col_flash, "Oui")

                        # Mise à jour Locale (pour que la case se coche visuellement tout de suite)
                        idx_local = df_base.index[df_base['rid'].astype(str) == str(current_row['rid'])].tolist()[0]
                        st.session_state.df.at[idx_local, 'flashcards_made'] = True

                        st.session_state.draft_cards = []
                        st.balloons()
                        st.toast("Cartes sauvegardées et Article marqué 'Fait' !", icon="🎉")
                        time.sleep(1)
                        st.rerun()

                except Exception as e:
                    st.error(f"Erreur: {e}")

            if col_clear.button("🗑️ Annuler"):
                st.session_state.draft_cards = []
                st.rerun()

else:
    st.info("👈 Sélectionne un article (👁️) dans le tableau ci-dessus pour commencer.")