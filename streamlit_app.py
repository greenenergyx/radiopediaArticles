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

# ==========================================
# 1. CONFIGURATION & STYLE
# ==========================================
st.set_page_config(page_title="Radiopaedia Cockpit", page_icon="🩻", layout="wide")

st.markdown("""
    <style>
        .block-container {padding-top: 1rem; padding-bottom: 3rem;}
        div[data-testid="stExpander"] div[role="button"] p {font-weight: 600;}
        .stButton button {width: 100%;}
        h1 {font-size: 1.8rem !important;}
        h2 {font-size: 1.5rem !important;}
        h3 {font-size: 1.2rem !important;}
        .stDataEditor {border: 1px solid #ddd; border-radius: 5px;}
    </style>
""", unsafe_allow_html=True)


# ==========================================
# 2. FONCTIONS BACKEND
# ==========================================
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
        return df, worksheet, sh
    except Exception as e:
        st.error(f"Erreur connexion Sheet Articles : {e}")
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
    except Exception as e:
        return pd.DataFrame(), None


def get_unique_tags(df, column_name):
    if column_name not in df.columns: return []
    all_text = ",".join(df[column_name].dropna().astype(str).tolist())
    tags = [t.strip() for t in all_text.split(',') if t.strip()]
    return sorted(list(set(tags)))


# ==========================================
# 3. SESSION STATE
# ==========================================
if "current_rid" not in st.session_state: st.session_state.current_rid = None
if "current_url" not in st.session_state: st.session_state.current_url = None
if "draft_cards" not in st.session_state: st.session_state.draft_cards = []
if "api_key" not in st.session_state: st.session_state.api_key = ""
if "selected_model" not in st.session_state: st.session_state.selected_model = "models/gemini-1.5-flash"

# ==========================================
# 4. SIDEBAR
# ==========================================
with st.sidebar:
    st.header("⚙️ Config")
    if "GEMINI_API_KEY" in st.secrets:
        st.session_state.api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🔑 Clé API chargée")
    else:
        api_input = st.text_input("Clé Gemini", value=st.session_state.api_key, type="password")
        if api_input: st.session_state.api_key = api_input

    # Liste de modèles robuste (si l'API échoue à lister)
    fallback_models = ["models/gemini-1.5-flash", "models/gemini-1.5-pro", "models/gemini-pro"]

    st.session_state.selected_model = st.selectbox(
        "Modèle IA",
        fallback_models,
        index=0
    )

    st.divider()

    if "sh_obj" in st.session_state and st.session_state.sh_obj:
        st.subheader("📤 Export")
        if st.button("Télécharger Anki (.txt)"):
            df_c, _ = load_cards_data(st.session_state.sh_obj)
            if not df_c.empty:
                out = io.StringIO()
                out.write("#separator:Pipe\n#html:true\n#tags column:4\n")
                for _, r in df_c.iterrows():
                    q = str(r['question']).replace('|', '/')
                    a = str(r['answer']).replace('|', '/')
                    tag = str(r.get('tags', '')).strip() or str(r['article_title']).replace(' ', '_')
                    out.write(f"{q}|{a}|{r['card_type']}|{tag}\n")

                st.download_button("💾 Sauvegarder", data=out.getvalue(), file_name=f"anki_export_{date.today()}.txt",
                                   mime="text/plain")
            else:
                st.warning("Aucune carte à exporter.")

# ==========================================
# 5. CHARGEMENT DONNÉES
# ==========================================
try:
    sheet_url = st.secrets["private_sheet_url"]
except:
    st.error("⚠️ URL manquante dans secrets.")
    st.stop()

if "client" not in st.session_state:
    st.session_state.client = get_google_sheet_client()

if "df" not in st.session_state:
    df_load, worksheet, sh_obj = load_data(st.session_state.client, sheet_url)
    if df_load is not None:
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

# ==========================================
# 6. INTERFACE COCKPIT
# ==========================================
st.title("🩻 Radiologie Cockpit")

if df_base is not None:
    # --- TRACKER ---
    with st.expander("🔍 Liste des articles", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        view_mode = c1.radio("Vue", ["📥 À faire", "✅ Fait", "📂 Tout"], horizontal=True)
        u_sys = get_unique_tags(df_base, 'system')
        sel_sys = c2.multiselect("Système", u_sys)
        u_sec = get_unique_tags(df_base, 'section')
        sel_sec = c3.multiselect("Section", u_sec)
        s_query = c4.text_input("Recherche", "")

    df_display = df_base.copy()
    if "Voir" in df_display.columns: df_display.drop(columns=["Voir"], inplace=True)
    df_display.insert(0, "Voir", False)

    if st.session_state.current_rid:
        mask = df_display['rid'].astype(str) == str(st.session_state.current_rid)
        df_display.loc[mask, 'Voir'] = True

    df_display['ignored'] = df_display['ignored'].fillna(False).astype(bool)
    if view_mode == "📥 À faire":
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

    edited_df = st.data_editor(
        df_display,
        height=250,
        column_config={
            "rid": None, "content": None, "remote_last_mod_date": None, "url": None,
            "Voir": st.column_config.CheckboxColumn("👁️", width="small"),
            "title": st.column_config.TextColumn("Titre", disabled=True),
            "system": st.column_config.TextColumn("Système", width="small", disabled=True),
            "section": None,
            "ignored": st.column_config.CheckboxColumn("⛔", width="small"),
            "read_status": st.column_config.CheckboxColumn("Lu ?", width="small"),
            "flashcards_made": st.column_config.CheckboxColumn("Flash ?", width="small"),
            "notes": st.column_config.TextColumn("Notes", width="medium"),
            "last_access": st.column_config.TextColumn("Dernier", disabled=True)
        },
        hide_index=True, use_container_width=True, key="editor"
    )

    changes = st.session_state["editor"]["edited_rows"]
    if changes:
        need_rerun = False
        for idx_view, chg in changes.items():
            if "Voir" in chg and chg["Voir"]:
                orig_idx = df_display.index[idx_view]
                row = df_base.iloc[orig_idx]
                st.session_state.current_rid = str(row['rid'])
                st.session_state.current_url = row['url']
                need_rerun = True

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
                            st.session_state.df.at[orig_idx, k] = v
                    worksheet.update_cell(row_n, headers.index('last_access') + 1, str(datetime.now()))
                    st.toast("Sauvegardé", icon="✅")
                    need_rerun = True
                except Exception as e:
                    st.error(f"Erreur save: {e}")
        if need_rerun: st.rerun()

    # --- ESPACE DE TRAVAIL ---
    if st.session_state.current_rid:
        current_row = df_base[df_base['rid'].astype(str) == str(st.session_state.current_rid)].iloc[0]

        st.markdown("---")
        col_left, col_right = st.columns([1, 1])

        # 1. LECTURE
        with col_left:
            st.subheader(f"📖 {current_row['title']}")
            if current_row['url']:
                try:
                    components.iframe(current_row['url'], height=850, scrolling=True)
                except:
                    st.markdown(f"[Ouvrir lien]({current_row['url']})")

        # 2. IA ARCHITECT
        with col_right:
            st.subheader("🧠 Générateur Flashcards")

            # --- CONTEXT AWARENESS ---
            existing_cards_context = ""
            count_existing = 0

            if sh_obj:
                df_c, _ = load_cards_data(sh_obj)
                if not df_c.empty:
                    existing_for_article = df_c[df_c['rid'].astype(str) == str(current_row['rid'])]
                    count_existing = len(existing_for_article)
                    if count_existing > 0:
                        cards_list = []
                        for _, c_row in existing_for_article.iterrows():
                            cards_list.append(f"- Q: {c_row['question']} | A: {c_row['answer']}")
                        existing_cards_context = "\n".join(cards_list)

            if count_existing > 0:
                st.caption(f"ℹ️ Prend en compte {count_existing} cartes existantes.")

            with st.form("ai_form"):
                mode = st.radio("Format", ["Format A: Cloze (Trous)", "Format B: Liste Différentiel"], horizontal=True)
                custom_inst = st.text_input("Instruction spécifique")
                submitted_gen = st.form_submit_button("✨ Générer (Incrémental)", type="primary")

            if submitted_gen:
                if not st.session_state.api_key:
                    st.error("Manque clé API Gemini ! Vérifie la barre latérale.")
                else:
                    try:
                        # CONFIGURATION EXPLICITE À CHAQUE APPEL
                        genai.configure(api_key=st.session_state.api_key)

                        # Paramètres de sécurité pour éviter les blocages "Médical = Gore"
                        safety_settings = [
                            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                        ]

                        model = genai.GenerativeModel(
                            st.session_state.selected_model,
                            safety_settings=safety_settings
                        )

                        memory_block = ""
                        if existing_cards_context:
                            memory_block = f"EXISTING CARDS (DO NOT DUPLICATE):\n{existing_cards_context}"

                        sys_prompt = """
                        System Prompt: Radiology Board Exam Anki Architect v2.2
                        Role: Elite Medical Editor.
                        Task: Create "Stand-Alone" Anki cards.

                        CRITICAL RULE: "STAND-ALONE" TEST
                        - Never start with "It", "They".
                        - Always name the pathology explicitly in the question.

                        1. CONTENT FILTERS
                        - NO History/Trivia.
                        - FOCUS: Critical differentiators, "Aunt Minnie", Epidemiology.

                        2. FORMATTING RULES
                        - Format A (Cloze): [Pathology] + [Verb] + {{c1::[Fact]}}.
                        - Format B (List): Bullet points.

                        3. OUTPUT FORMAT
                        - Code Block ONLY.
                        - Pipe Separator (|).
                        - Cols: Question/Cloze | Extra/Answer | Tag
                        """

                        full_prompt = f"{sys_prompt}\n{memory_block}\nArticle: {current_row['title']}\nFormat: {mode}\nInstr: {custom_inst}\nText:\n{current_row['content']}"

                        with st.spinner(f"Génération ({st.session_state.selected_model})..."):
                            resp = model.generate_content(full_prompt)

                            if not resp.text:
                                st.error("L'IA a renvoyé une réponse vide.")
                            else:
                                clean = resp.text.replace("```", "").strip()
                                new_batch = []
                                for l in clean.split('\n'):
                                    if '|' in l:
                                        p = l.split('|')
                                        if len(p) >= 2:
                                            q = p[0].strip()
                                            if len(q) > 5 and "Question" not in q:
                                                new_batch.append({
                                                    "rid": str(current_row['rid']),
                                                    "article_title": current_row['title'],
                                                    "system": current_row['system'],
                                                    "card_type": "Cloze" if "{{" in q else "Basic",
                                                    "question": q,
                                                    "answer": p[1].strip(),
                                                    "tags": p[2].strip() if len(p) > 2 else ""
                                                })

                                if new_batch:
                                    st.session_state.draft_cards.extend(new_batch)
                                    st.success(f"{len(new_batch)} nouvelles cartes !")
                                else:
                                    st.warning("Aucune carte valide trouvée dans la réponse.")
                                    with st.expander("Voir la réponse brute pour débogage"):
                                        st.write(clean)

                    except Exception as e:
                        st.error(f"ERREUR CRITIQUE IA : {e}")
                        st.caption("Essaie de changer de modèle dans la barre latérale (ex: gemini-1.5-pro).")

            # --- PREVISUALISATION ---
            if st.session_state.draft_cards:
                st.divider()
                st.subheader(f"Brouillon ({len(st.session_state.draft_cards)})")

                draft_df = pd.DataFrame(st.session_state.draft_cards)

                edited_draft = st.data_editor(
                    draft_df[['question', 'answer', 'tags']],
                    num_rows="dynamic",
                    use_container_width=True,
                    key="draft_editor"
                )

                col_save, col_clear = st.columns(2)

                if col_save.button("💾 Valider & Marquer Fait", type="primary"):
                    try:
                        _, ws_cards = load_cards_data(sh_obj)
                        if ws_cards:
                            rows = []
                            for _, r in edited_draft.iterrows():
                                rows.append([
                                    str(current_row['rid']),
                                    current_row['title'],
                                    current_row['system'],
                                    "Cloze" if "{{" in r['question'] else "Basic",
                                    r['question'],
                                    r['answer'],
                                    r['tags']
                                ])

                            if rows:
                                ws_cards.append_rows(rows)

                                cell = worksheet.find(str(current_row['rid']))
                                headers = worksheet.row_values(1)
                                if 'flashcards_made' in headers:
                                    worksheet.update_cell(cell.row, headers.index('flashcards_made') + 1, "Oui")
                                    idx_local = \
                                    df_base.index[df_base['rid'].astype(str) == str(current_row['rid'])].tolist()[0]
                                    st.session_state.df.at[idx_local, 'flashcards_made'] = True

                                st.session_state.draft_cards = []
                                st.balloons()
                                st.toast("Sauvegardé !", icon="🎉")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.warning("Le tableau est vide.")

                    except Exception as e:
                        st.error(f"Erreur: {e}")

                if col_clear.button("🗑️ Tout vider"):
                    st.session_state.draft_cards = []
                    st.rerun()

    else:
        st.info("👈 Sélectionne un article (👁️) pour commencer.")