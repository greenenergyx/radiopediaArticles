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
        .stDataEditor {border: 1px solid #ddd; border-radius: 5px;}
    </style>
""", unsafe_allow_html=True)


# ==========================================
# 2. FONCTIONS BACKEND (GOOGLE SHEETS)
# ==========================================
@st.cache_resource
def get_google_sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        creds_dict = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        return None


def load_data(client, sheet_url):
    try:
        sh = client.open_by_url(sheet_url)
        worksheet = sh.get_worksheet(0)
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        return df, worksheet, sh
    except Exception:
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


# ==========================================
# 3. ÉTAT (SESSION STATE)
# ==========================================
if "current_rid" not in st.session_state: st.session_state.current_rid = None
if "current_url" not in st.session_state: st.session_state.current_url = None
if "draft_cards" not in st.session_state: st.session_state.draft_cards = []
if "api_key" not in st.session_state: st.session_state.api_key = ""
if "selected_model" not in st.session_state: st.session_state.selected_model = ""

# ==========================================
# 4. BARRE LATÉRALE (CONFIG IA)
# ==========================================
with st.sidebar:
    st.header("⚙️ Config IA")

    # Clé API
    if "GEMINI_API_KEY" in st.secrets:
        st.session_state.api_key = st.secrets["GEMINI_API_KEY"]
    else:
        api_input = st.text_input("Clé Gemini", value=st.session_state.api_key, type="password")
        if api_input: st.session_state.api_key = api_input

    # Détection des modèles
    fetched_models = []
    if st.session_state.api_key:
        try:
            genai.configure(api_key=st.session_state.api_key)
            all_models = genai.list_models()
            for m in all_models:
                if 'generateContent' in m.supported_generation_methods:
                    fetched_models.append(m.name)
            fetched_models.sort(reverse=True)
        except:
            pass

    if fetched_models:
        # Sélection intelligente (Flash par défaut)
        default_idx = 0
        for i, name in enumerate(fetched_models):
            if "flash" in name.lower(): default_idx = i; break
        st.session_state.selected_model = st.selectbox("Modèle IA", fetched_models, index=default_idx)
    elif st.session_state.api_key:
        st.warning("Clé valide mais aucun modèle trouvé (vérifier API Generative Language).")

    st.divider()

    # Export Anki
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
                st.download_button("Sauvegarder", data=out.getvalue(), file_name=f"anki_{date.today()}.txt")

# ==========================================
# 5. CHARGEMENT DONNÉES
# ==========================================
try:
    sheet_url = st.secrets["private_sheet_url"]
except:
    st.error("URL Sheet manquante.")
    st.stop()

if "client" not in st.session_state: st.session_state.client = get_google_sheet_client()

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
# 6. INTERFACE PRINCIPALE
# ==========================================
st.title("🩻 Radiologie Cockpit")

if df_base is not None:
    # --- TRACKER (Tableau du haut) ---
    with st.expander("🔍 Filtrer la liste", expanded=False):
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
        df_display, height=250, hide_index=True, use_container_width=True, key="editor",
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
        }
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
                    headers = worksheet.row_values(1)
                    for k, v in data_chg.items():
                        val = "Oui" if v is True else ("" if v is False else v)
                        if k in headers:
                            worksheet.update_cell(cell.row, headers.index(k) + 1, val)
                            st.session_state.df.at[orig_idx, k] = v
                    worksheet.update_cell(cell.row, headers.index('last_access') + 1, str(datetime.now()))
                    st.toast("Sauvegardé", icon="✅")
                    need_rerun = True
                except:
                    pass
        if need_rerun: st.rerun()

    # --- ESPACE DE TRAVAIL (BAS) ---
    if st.session_state.current_rid:
        current_row_mask = df_base['rid'].astype(str) == str(st.session_state.current_rid)
        if current_row_mask.any():
            current_row = df_base[current_row_mask].iloc[0]

            st.markdown("---")
            col_left, col_right = st.columns([1, 1])

            # GAUCHE : ARTICLE
            with col_left:
                st.subheader(f"📖 {current_row['title']}")
                if current_row['url']:
                    try:
                        components.iframe(current_row['url'], height=850, scrolling=True)
                    except:
                        st.markdown(f"[Lien externe]({current_row['url']})")

            # DROITE : GÉNÉRATEUR
            with col_right:
                st.subheader("🧠 Générateur Interactif")

                # 1. Préparation de la Mémoire (Cartes existantes)
                existing_context_text = ""
                card_count = 0

                # A. Cartes du Sheet
                if sh_obj:
                    df_c, _ = load_cards_data(sh_obj)
                    if not df_c.empty:
                        saved_cards = df_c[df_c['rid'].astype(str) == str(current_row['rid'])]
                        if not saved_cards.empty:
                            card_count += len(saved_cards)
                            existing_context_text += "--- ALREADY SAVED CARDS (Do not duplicate) ---\n"
                            for _, r in saved_cards.iterrows():
                                existing_context_text += f"Q: {r['question']} | A: {r['answer']}\n"

                # B. Cartes du Brouillon
                if st.session_state.draft_cards:
                    card_count += len(st.session_state.draft_cards)
                    existing_context_text += "--- CARDS IN DRAFT (Do not duplicate) ---\n"
                    for r in st.session_state.draft_cards:
                        existing_context_text += f"Q: {r['question']} | A: {r['answer']}\n"

                if card_count > 0:
                    st.info(f"ℹ️ {card_count} cartes connues par l'IA (évitement de doublons actif).")

                # 2. Formulaire
                with st.form("ai_form"):
                    mode = st.radio("Format", ["Cloze (Texte à trous)", "Basic (Question/Réponse)"], horizontal=True)
                    custom_inst = st.text_input("Instruction (ex: focus anatomie)")
                    label_btn = "✨ Générer des cartes" if card_count == 0 else "➕ Ajouter des cartes COMPLÉMENTAIRES"
                    submitted_gen = st.form_submit_button(label_btn, type="primary")

                # 3. Logique de Génération
                if submitted_gen:
                    if not st.session_state.api_key:
                        st.error("Manque clé API.")
                    else:
                        try:
                            genai.configure(api_key=st.session_state.api_key)
                            model = genai.GenerativeModel(st.session_state.selected_model)

                            # PROMPT OPTIMISÉ (Réponse vide pour Cloze)
                            sys_prompt = """
                            System Prompt: Radiology Anki Architect v3.1
                            Role: Create Anki cards.

                            CONTEXT AWARENESS:
                            - Read 'EXISTING CARDS'. Avoid duplicates.
                            - Find new complementary angles.

                            FORMATTING RULES (CRITICAL):

                            1. FORMAT CLOZE (Standard):
                            - Structure: {{c1::Pathology}} shows {{c2::sign}}.
                            - COL 1 (Question): FULL sentence with clozes.
                            - COL 2 (Answer): EMPTY (!!!). Do not repeat the answer. Only use for extra notes/mnemonics.
                            - COL 3 (Tag): System/Section.

                            2. FORMAT BASIC:
                            - COL 1: Question.
                            - COL 2: Answer.
                            - COL 3: Tag.

                            OUTPUT: Column1|Column2|Column3
                            """

                            full_prompt = f"{sys_prompt}\n\nEXISTING CARDS:\n{existing_context_text}\n\nArticle: {current_row['title']}\nFormat: {mode}\nInstr: {custom_inst}\nText:\n{current_row['content']}"

                            with st.spinner(f"Réflexion ({st.session_state.selected_model})..."):
                                resp = model.generate_content(full_prompt)
                                clean = resp.text.replace("```", "").strip()

                                new_batch = []
                                for l in clean.split('\n'):
                                    if '|' in l:
                                        p = l.split('|')
                                        if len(p) >= 2:
                                            q = p[0].strip()
                                            a = p[1].strip()
                                            t = p[2].strip() if len(p) > 2 else ""

                                            # Validation
                                            if len(q) > 5 and "Question" not in q:
                                                new_batch.append({
                                                    "rid": str(current_row['rid']),
                                                    "article_title": current_row['title'],
                                                    "system": current_row['system'],
                                                    "card_type": "Cloze" if "{{" in q else "Basic",
                                                    "question": q, "answer": a, "tags": t
                                                })

                                if new_batch:
                                    st.session_state.draft_cards.extend(new_batch)
                                    st.success(f"{len(new_batch)} nouvelles cartes !")
                                    st.rerun()
                                else:
                                    st.warning("Rien généré de pertinent.")
                        except Exception as e:
                            st.error(f"Erreur IA: {e}")

                # 4. Éditeur de Brouillon (Suppression activée)
                if st.session_state.draft_cards:
                    st.divider()
                    st.subheader("📝 Brouillon")
                    st.caption("Cochez et appuyez sur 'Suppr' (ou l'icône poubelle) pour retirer une ligne.")

                    draft_df = pd.DataFrame(st.session_state.draft_cards)

                    # Tableau éditable avec suppression de lignes (num_rows="dynamic")
                    edited_draft = st.data_editor(
                        draft_df[['question', 'answer', 'tags']],
                        num_rows="dynamic",
                        use_container_width=True,
                        key="draft_edit"
                    )

                    col_save, col_clear = st.columns(2)

                    if col_save.button("💾 Valider & Sauvegarder", type="primary"):
                        try:
                            # Reconstitution des données complètes à partir de l'édité
                            final_rows_to_save = []
                            for idx, r in edited_draft.iterrows():
                                final_rows_to_save.append([
                                    str(current_row['rid']),
                                    current_row['title'],
                                    current_row['system'],
                                    "Cloze" if "{{" in r['question'] else "Basic",
                                    r['question'],
                                    r['answer'],
                                    r['tags']
                                ])

                            if final_rows_to_save:
                                _, ws_cards = load_cards_data(sh_obj)
                                ws_cards.append_rows(final_rows_to_save)

                                # Marquer l'article comme "Flashcards faites"
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
                        except Exception as e:
                            st.error(f"Erreur sauvegarde: {e}")

                    if col_clear.button("🗑️ Tout effacer"):
                        st.session_state.draft_cards = []
                        st.rerun()

    else:
        st.info("👈 Sélectionne un article (👁️) pour commencer.")