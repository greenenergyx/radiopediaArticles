import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date  # <--- CORRECTION ICI (Import des deux outils)
import streamlit.components.v1 as components
import google.generativeai as genai
import re
import io
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Radiopaedia Architect", page_icon="🩻", layout="wide")

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


# --- FONCTIONS DE CONNEXION ---
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
        worksheet = sh.get_worksheet(0)  # Onglet 1 : Articles
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        return df, worksheet, sh
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        return None, None, None


def load_cards_data(sh):
    """Charge les cartes depuis l'onglet 'Cards'"""
    try:
        worksheet_cards = sh.worksheet("Cards")
        data = worksheet_cards.get_all_records()
        df_cards = pd.DataFrame(data)
        if df_cards.empty:
            df_cards = pd.DataFrame(
                columns=['rid', 'article_title', 'system', 'card_type', 'question', 'answer', 'tags'])
        return df_cards, worksheet_cards
    except gspread.exceptions.WorksheetNotFound:
        st.error("L'onglet 'Cards' n'existe pas dans le Google Sheet. Crée-le svp !")
        return pd.DataFrame(), None
    except Exception as e:
        st.error(f"Erreur chargement cartes: {e}")
        return pd.DataFrame(), None


def get_unique_tags(df, column_name):
    if column_name not in df.columns: return []
    all_text = ",".join(df[column_name].dropna().astype(str).tolist())
    tags = [t.strip() for t in all_text.split(',') if t.strip()]
    return sorted(list(set(tags)))


# --- VARIABLES SESSION ---
if "current_url" not in st.session_state: st.session_state.current_url = None
if "draft_cards" not in st.session_state: st.session_state.draft_cards = []
if "api_key" not in st.session_state: st.session_state.api_key = ""
if "selected_model" not in st.session_state: st.session_state.selected_model = "models/gemini-pro"

# --- SIDEBAR ---
with st.sidebar:
    st.title("⚙️ Configuration")

    if "GEMINI_API_KEY" in st.secrets:
        st.session_state.api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🔑 Clé API chargée")
    else:
        api_input = st.text_input("Clé API Google Gemini", value=st.session_state.api_key, type="password")
        if api_input: st.session_state.api_key = api_input

    st.divider()

    st.write("🤖 **Modèle IA**")
    available_models = ["models/gemini-1.5-flash", "models/gemini-pro"]

    if st.session_state.api_key:
        try:
            genai.configure(api_key=st.session_state.api_key)
            all_models = genai.list_models()
            found_models = [m.name for m in all_models if 'generateContent' in m.supported_generation_methods]
            if found_models:
                available_models = sorted(found_models, reverse=True)
        except Exception:
            pass

    st.session_state.selected_model = st.selectbox("Choisir le modèle :", available_models, index=0)
    st.divider()
    st.info("ℹ️ Prompt 'Crack the Core' actif.")

# --- DÉBUT APP ---
st.title("🩻 Radio Architect & Tracker")

try:
    sheet_url = st.secrets["private_sheet_url"]
except:
    st.error("URL manquante dans les secrets.")
    st.stop()

if "client" not in st.session_state:
    st.session_state.client = get_google_sheet_client()

if "df" not in st.session_state:
    df_load, worksheet, sh_obj = load_data(st.session_state.client, sheet_url)

    if df_load is not None:
        cols_to_bool = ['read_status', 'flashcards_made', 'ignored']
        if 'ignored' not in df_load.columns: df_load['ignored'] = False
        for col in cols_to_bool:
            if col in df_load.columns:
                df_load[col] = df_load[col].apply(lambda x: True if str(x).lower() in ['oui', 'true', '1'] else False)

    st.session_state.df = df_load
    st.session_state.worksheet = worksheet
    st.session_state.sh_obj = sh_obj
else:
    if st.session_state.worksheet is None:
        _, st.session_state.worksheet, st.session_state.sh_obj = load_data(st.session_state.client, sheet_url)

df_base = st.session_state.df
worksheet = st.session_state.worksheet
sh_obj = st.session_state.sh_obj

tab1, tab2, tab3 = st.tabs(["📊 Tracker", "🏭 Usine à Flashcards (Architect)", "🗃️ Base & Export"])

# ==========================================
# TAB 1 : LE TRACKER
# ==========================================
with tab1:
    if df_base is not None:
        if "Voir" in df_base.columns: df_base.drop(columns=["Voir"], inplace=True)
        df_display = df_base.copy()
        df_display.insert(0, "Voir", False)
        if st.session_state.current_url:
            mask = df_display['url'] == st.session_state.current_url
            df_display.loc[mask, 'Voir'] = True

        with st.expander("🔍 Filtres & Affichage", expanded=True):
            view_mode = st.radio("Mode :", ["📥 À traiter", "⛔ Ignorés", "📂 Tout"], horizontal=True)
            st.divider()
            c_f1, c_f2, c_f3 = st.columns(3)
            with c_f1:
                u_sys = get_unique_tags(df_base, 'system')
                sel_sys = st.multiselect("Système (ET)", u_sys)
            with c_f2:
                u_sec = get_unique_tags(df_base, 'section')
                sel_sec = st.multiselect("Section (ET)", u_sec)
            with c_f3:
                s_query = st.text_input("Recherche", "")

        df_display['ignored'] = df_display['ignored'].fillna(False).astype(bool)
        if view_mode == "📥 À traiter":
            filtered_df = df_display[~df_display['ignored']]
        elif view_mode == "⛔ Ignorés":
            filtered_df = df_display[df_display['ignored']]
        else:
            filtered_df = df_display

        if sel_sys:
            for s in sel_sys: filtered_df = filtered_df[
                filtered_df['system'].astype(str).str.contains(re.escape(s), case=False, regex=True)]
        if sel_sec:
            for s in sel_sec: filtered_df = filtered_df[
                filtered_df['section'].astype(str).str.contains(re.escape(s), case=False, regex=True)]
        if s_query:
            filtered_df = filtered_df[filtered_df['title'].str.contains(s_query, case=False, na=False)]

        if not sel_sys and not sel_sec and not s_query and len(filtered_df) > 200:
            filtered_df = filtered_df.head(200)

        col1, col2 = st.columns([1.6, 1])
        with col1:
            st.subheader(f"Articles ({len(filtered_df)})")

            edited_df = st.data_editor(
                filtered_df,
                column_config={
                    "rid": None, "content": None, "remote_last_mod_date": None, "url": None,
                    "Voir": st.column_config.CheckboxColumn("👁️", width="small"),
                    "title": st.column_config.TextColumn("Titre", disabled=True),
                    "system": st.column_config.TextColumn("Système", width="small", disabled=True),
                    "section": st.column_config.TextColumn("Section", width="small", disabled=True),
                    "ignored": st.column_config.CheckboxColumn("⛔", width="small"),
                    "read_status": st.column_config.CheckboxColumn("Lu ?", width="small"),
                    "flashcards_made": st.column_config.CheckboxColumn("Flash ?", width="small"),
                    "notes": st.column_config.TextColumn("Notes", width="medium"),
                    "last_access": st.column_config.TextColumn("Dernier accès", disabled=True)
                },
                hide_index=True, use_container_width=True, key="editor"
            )

            changes = st.session_state["editor"]["edited_rows"]
            if changes:
                need_rerun = False
                for idx_view, chg in changes.items():
                    if "Voir" in chg and chg["Voir"]:
                        orig_idx = filtered_df.index[idx_view]
                        st.session_state.current_url = df_base.iloc[orig_idx]['url']
                        need_rerun = True

                    data_chg = {k: v for k, v in chg.items() if k != "Voir"}
                    if data_chg:
                        try:
                            st.toast("⏳ Sauvegarde...", icon="☁️")
                            orig_idx = filtered_df.index[idx_view]
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
                            st.toast("✅ Sauvegardé !", icon="💾")
                            need_rerun = True
                        except Exception as e:
                            st.error(f"Erreur: {e}")

                if need_rerun: st.rerun()

        with col2:
            url = st.session_state.current_url
            if url:
                try:
                    components.iframe(url, height=850, scrolling=True)
                except:
                    st.markdown(f"[Ouvrir]({url})")
            else:
                st.info("Sélectionne avec 👁️")

# ==========================================
# TAB 2 : USINE À FLASHCARDS (IA)
# ==========================================
with tab2:
    st.header("🏭 Générateur de Cartes (Board Exam Architect)")

    if not st.session_state.api_key:
        st.warning("⚠️ Clé API introuvable.")
    else:
        st.write("1️⃣ **Article Source**")
        c_sel1, c_sel2 = st.columns([1, 2])
        f_sys_ia = c_sel1.selectbox("Filtrer par Système", ["Tout"] + u_sys)

        candidates = df_base
        if f_sys_ia != "Tout":
            candidates = candidates[candidates['system'].astype(str).str.contains(re.escape(f_sys_ia), case=False)]

        candidates['label'] = candidates['title'] + " (ID: " + candidates['rid'].astype(str) + ")"
        sel_article_label = c_sel2.selectbox("Sélectionne l'article", candidates['label'].unique())

        row_art = candidates[candidates['label'] == sel_article_label].iloc[0]

        st.divider()
        col_content, col_gen = st.columns(2)

        with col_content:
            st.subheader(f"📄 {row_art['title']}")
            st.text_area("Texte source", row_art['content'], height=500, disabled=True)

        with col_gen:
            st.subheader("🤖 IA Architect")
            st.info(f"Modèle : **{st.session_state.selected_model}**")
            mode = st.radio("Format cible", ["Format A: Board Fact Cloze", "Format B: Differential List"],
                            horizontal=True)
            custom_inst = st.text_input("Instruction additionnelle (Optionnel)")

            if st.button("✨ Créer les Flashcards", type="primary", use_container_width=True):
                try:
                    genai.configure(api_key=st.session_state.api_key)
                    model = genai.GenerativeModel(st.session_state.selected_model)

                    system_prompt = """
                    System Prompt: Radiology Board Exam Anki Architect
                    Role: You are the Lead Editor for the "Crack the Core" Radiology Board Review Series. Your task is to convert raw medical text into high-performance Anki flashcards that mimic the style and difficulty of the ABR Core Exam.
                    Objective: Maximize retention of "Aunt Minnie" diagnoses, critical differentiators, and board-relevant epidemiology while minimizing card count. Quality over quantity.

                    1. The "Board Filter" (Selection Criteria)
                    Do NOT create cards for generic anatomy or basic physiology unless it is the direct basis for a pathology.
                    Only create cards for:
                    - Buzzwords: Specific phrases used in board questions.
                    - Critical Differentiators: The single feature that separates two look-alike pathologies.
                    - Board Epidemiology: "Most common", age peaks, gender biases.
                    - Associations: Syndromes, mutations, "Next Best Step".
                    - Mechanism: Brief pathophysiology if it explains imaging appearance.

                    2. Card Construction Rules
                    Format A: The "Board Fact" Cloze (Standard)
                    - Syntax: Use {{c1::hidden text}} for the key fact.
                    - Rule: One fact per card. Do not cloze multiple unrelated facts.
                    - Focus: Cloze the finding or the diagnosis, not the lead-in words.
                    - Extra Field: Place detailed explanation/mechanism/mnemonic in the "Extra" field.

                    Format B: The "Differential" List (Basic)
                    - Use this for lists of 3+ items or specific criteria triads.
                    - Question: What is the [Name of Sign/Triad/List]?
                    - Answer: Use concise bullet points.

                    3. Formatting
                    - Images: If text describes visual sign, append [IMAGE: Description] to the card.
                    - Mnemonics: Always highlight mnemonics in bold.
                    - Tags: Suggest a hierarchical tag (e.g., #Neuro::TemporalBone).

                    4. Output Structure
                    - Output ONLY the final result in a Code Block.
                    - Use a PIPE (|) separator.
                    - Structure: Question/Cloze Text | Extra/Answer | Tag
                    - Do NOT include headers.
                    """

                    full_prompt = f"""
                    {system_prompt}
                    CURRENT TASK:
                    - Format requested: {mode}
                    - Additional User Instructions: {custom_inst}
                    TEXT TO PROCESS:
                    {row_art['content']}
                    """

                    with st.spinner(f"L'Architecte analyse avec {st.session_state.selected_model}..."):
                        response = model.generate_content(full_prompt)
                        clean = response.text.replace("```", "").strip()
                        new_batch = []
                        for l in clean.split('\n'):
                            if '|' in l:
                                parts = l.split('|')
                                if len(parts) >= 2:
                                    q = parts[0].strip()
                                    a = parts[1].strip()
                                    t = parts[2].strip() if len(parts) > 2 else ""

                                    new_batch.append({
                                        "rid": str(row_art['rid']),
                                        "article_title": row_art['title'],
                                        "system": row_art['system'],
                                        "card_type": "Cloze" if "{{" in q else "Basic",
                                        "question": q,
                                        "answer": a,
                                        "tags": t
                                    })

                        if new_batch:
                            st.session_state.draft_cards.extend(new_batch)
                            st.success(f"{len(new_batch)} cartes de haute qualité générées !")
                        else:
                            st.warning("Aucune carte générée. Vérifie le modèle ou le texte.")

                except Exception as e:
                    st.error(f"Erreur IA : {e}")

        if st.session_state.draft_cards:
            st.divider()
            st.subheader(f"📝 Brouillons ({len(st.session_state.draft_cards)})")

            draft_df = pd.DataFrame(st.session_state.draft_cards)
            st.dataframe(draft_df[['question', 'answer', 'tags']], use_container_width=True)

            c_save, c_del = st.columns(2)
            if c_save.button("☁️ Valider et Envoyer (Sheets)", type="primary"):
                try:
                    _, ws_cards = load_cards_data(sh_obj)
                    if ws_cards:
                        rows_to_add = []
                        for _, row in draft_df.iterrows():
                            rows_to_add.append([
                                row['rid'], row['article_title'], row['system'],
                                row['card_type'], row['question'], row['answer'],
                                row.get('tags', '')
                            ])
                        ws_cards.append_rows(rows_to_add)
                        st.session_state.draft_cards = []
                        st.success("Sauvegardé dans 'Cards' avec succès !")
                        time.sleep(1)
                        st.rerun()
                except Exception as e:
                    st.error(f"Erreur sauvegarde : {e}")

            if c_del.button("🗑️ Tout jeter"):
                st.session_state.draft_cards = []
                st.rerun()

# ==========================================
# TAB 3 : EXPORT
# ==========================================
with tab3:
    st.header("🗃️ Mes Flashcards")
    if st.button("🔄 Rafraîchir"):
        st.cache_resource.clear()
        st.rerun()

    df_cards, _ = load_cards_data(sh_obj)

    if not df_cards.empty:
        st.write(f"Total : **{len(df_cards)} cartes**")
        st.dataframe(df_cards, use_container_width=True)

        out = io.StringIO()
        out.write("#separator:Pipe\n#html:true\n#tags column:4\n")

        for _, r in df_cards.iterrows():
            q = str(r['question']).replace('|', '/')
            a = str(r['answer']).replace('|', '/')
            if 'tags' in r and str(r['tags']).strip() != "":
                tag = str(r['tags']).strip()
            else:
                tag = str(r['article_title']).replace(' ', '_')
            out.write(f"{q}|{a}|{r['card_type']}|{tag}\n")

        # CORRECTION ICI : Utilisation de date.today()
        st.download_button("⬇️ Télécharger Export Anki (.txt)", data=out.getvalue(),
                           file_name=f"anki_board_prep_{date.today()}.txt")
    else:
        st.info("Aucune carte trouvée.")