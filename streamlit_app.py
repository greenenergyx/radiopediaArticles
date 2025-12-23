import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date
import streamlit.components.v1 as components
import google.generativeai as genai
import io
import re
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Radiopaedia Cockpit", page_icon="🩻", layout="wide")

# CSS Custom
st.markdown("""
    <style>
        .block-container {padding-top: 1rem; padding-bottom: 3rem;}
        div[data-testid="stExpander"] div[role="button"] p {font-weight: 600;}
        .stButton button {width: 100%;}
        h1 {font-size: 1.8rem !important;}
        .stDataEditor {border: 1px solid #ddd;}
    </style>
""", unsafe_allow_html=True)


# --- BACKEND SHEETS ---
@st.cache_resource
def get_google_sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        creds_dict = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"Secret Error: {e}")
        return None


def load_data(client, sheet_url):
    try:
        sh = client.open_by_url(sheet_url)
        worksheet = sh.get_worksheet(0)
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        return df, worksheet, sh
    except Exception as e:
        st.error(f"Sheet Load Error: {e}")
        return None, None, None


def load_cards_data(sh):
    try:
        worksheet_cards = sh.worksheet("Cards")
        data = worksheet_cards.get_all_records()
        df_cards = pd.DataFrame(data)
        columns = ['rid', 'article_title', 'system', 'card_type', 'question', 'answer', 'tags']
        if df_cards.empty:
            df_cards = pd.DataFrame(columns=columns)
        else:
            for col in columns:
                if col not in df_cards.columns:
                    df_cards[col] = ""
        return df_cards, worksheet_cards
    except:
        return pd.DataFrame(), None


def get_unique_tags(df, column_name):
    if column_name not in df.columns: return []
    all_text = ",".join(df[column_name].dropna().astype(str).tolist())
    tags = [t.strip() for t in all_text.split(',') if t.strip()]
    return sorted(list(set(tags)))


# --- STATE ---
if "current_rid" not in st.session_state: st.session_state.current_rid = None
if "draft_cards" not in st.session_state: st.session_state.draft_cards = []
if "api_key" not in st.session_state: st.session_state.api_key = ""
if "selected_model" not in st.session_state: st.session_state.selected_model = ""

# --- SIDEBAR (CODE MODIFIÉ "INCASSABLE") ---
with st.sidebar:
    st.header("⚙️ AI Config")

    # 1. API Key
    if "GEMINI_API_KEY" in st.secrets:
        st.session_state.api_key = st.secrets["GEMINI_API_KEY"]
    else:
        # Fallback pour test manuel si pas dans les secrets
        st.session_state.api_key = st.text_input("Gemini API Key", value="", type="password")

    # 2. Model Selection (HYBRIDE : Dynamique + Fallback)
    model_options = []

    # Tentative de listing officiel
    if st.session_state.api_key:
        try:
            genai.configure(api_key=st.session_state.api_key)
            all_models = genai.list_models()
            for m in all_models:
                if 'generateContent' in m.supported_generation_methods:
                    model_options.append(m.name)
            model_options.sort(reverse=True)
        except Exception:
            # Si l'API échoue à lister (erreur 403/404), on ne plante pas.
            pass

    # C'EST ICI QUE JE RÈGLE TON PROBLÈME :
    # Si la liste est vide (échec API), on force les noms connus manuellement.
    if not model_options:
        model_options = [
            "models/gemini-1.5-flash",
            "models/gemini-1.5-pro",
            "models/gemini-1.0-pro",
            "gemini-pro"
        ]
        st.caption("⚠️ Mode manuel (API listing failed)")

    st.session_state.selected_model = st.selectbox("Select Model", model_options)

    # Export Anki
    if "sh_obj" in st.session_state and st.session_state.sh_obj:
        st.divider()
        if st.button("📥 Export Anki (.txt)"):
            df_c, _ = load_cards_data(st.session_state.sh_obj)
            if not df_c.empty:
                out = io.StringIO()
                out.write("#separator:Pipe\n#html:true\n#tags column:4\n")
                for _, r in df_c.iterrows():
                    q = str(r['question']).replace('|', '/')
                    a = str(r['answer']).replace('|', '/')
                    tag = str(r.get('tags', '')).strip() or str(r['article_title']).replace(' ', '_')
                    out.write(f"{q}|{a}|{r['card_type']}|{tag}\n")
                st.download_button("Download .txt", data=out.getvalue(), file_name=f"anki_{date.today()}.txt")

# --- MAIN APP ---
try:
    sheet_url = st.secrets["private_sheet_url"]
except:
    st.warning("Missing 'private_sheet_url' in st.secrets")
    st.stop()

if "client" not in st.session_state: st.session_state.client = get_google_sheet_client()

if st.session_state.client:
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
else:
    st.stop()

# UI Cockpit
st.title("🩻 Radiopaedia Cockpit")

if df_base is not None:
    # Tracker
    with st.expander("🔍 Articles List", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        view_mode = c1.radio("View", ["📥 To Do", "✅ Done", "📂 All"], horizontal=True)
        u_sys = get_unique_tags(df_base, 'system')
        sel_sys = c2.multiselect("System", u_sys)
        u_sec = get_unique_tags(df_base, 'section')
        sel_sec = c3.multiselect("Section", u_sec)
        s_query = c4.text_input("Search", "")

    df_display = df_base.copy()
    if "Voir" in df_display.columns: df_display.drop(columns=["Voir"], inplace=True)
    df_display.insert(0, "Voir", False)

    if st.session_state.current_rid:
        mask = df_display['rid'].astype(str) == str(st.session_state.current_rid)
        if mask.any():
            df_display.loc[mask, 'Voir'] = True

    df_display['ignored'] = df_display['ignored'].fillna(False).astype(bool)
    if view_mode == "📥 To Do":
        df_display = df_display[~df_display['ignored'] & ~df_display['read_status']]
    elif view_mode == "✅ Done":
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
            "rid": None, "content": None, "remote_last_mod_date": None, "url": None, "section": None,
            "Voir": st.column_config.CheckboxColumn("👁️", width="small"),
            "title": st.column_config.TextColumn("Title", disabled=True),
            "system": st.column_config.TextColumn("System", width="small", disabled=True),
        }
    )

    changes = st.session_state["editor"]["edited_rows"]
    if changes:
        need_rerun = False
        for idx_view, chg in changes.items():
            if "Voir" in chg and chg["Voir"]:
                try:
                    orig_idx = df_display.index[idx_view]
                    st.session_state.current_rid = str(df_base.loc[orig_idx, 'rid'])
                    need_rerun = True
                except:
                    pass

            data_chg = {k: v for k, v in chg.items() if k != "Voir"}
            if data_chg:
                try:
                    orig_idx = df_display.index[idx_view]
                    real_rid = df_base.loc[orig_idx, 'rid']
                    cell = worksheet.find(str(real_rid))
                    headers = worksheet.row_values(1)
                    for k, v in data_chg.items():
                        val = "TRUE" if v is True else ("FALSE" if v is False else v)
                        if k in headers:
                            col_idx = headers.index(k) + 1
                            worksheet.update_cell(cell.row, col_idx, val)
                            st.session_state.df.at[orig_idx, k] = v
                    if 'last_access' in headers:
                        worksheet.update_cell(cell.row, headers.index('last_access') + 1, str(datetime.now()))
                    st.toast("Saved", icon="✅")
                    need_rerun = True
                except Exception as e:
                    st.error(f"Save Error: {e}")

        if need_rerun: st.rerun()

    # Workspace
    if st.session_state.current_rid:
        current_row_mask = df_base['rid'].astype(str) == str(st.session_state.current_rid)
        if current_row_mask.any():
            current_row = df_base[current_row_mask].iloc[0]

            st.markdown("---")
            c_left, c_right = st.columns([1, 1])

            with c_left:
                st.subheader(f"📖 {current_row['title']}")
                if current_row['url']:
                    components.iframe(current_row['url'], height=850, scrolling=True)
                else:
                    st.warning("No URL available")

            with c_right:
                st.subheader("🧠 Generator")

                existing_ctx = ""
                if sh_obj:
                    df_c, _ = load_cards_data(sh_obj)
                    if not df_c.empty:
                        exist = df_c[df_c['rid'].astype(str) == str(current_row['rid'])]
                        if not exist.empty:
                            st.caption(f"ℹ️ {len(exist)} existing cards.")
                            existing_ctx = "\n".join(
                                [f"- Q: {r['question']} | A: {r['answer']}" for _, r in exist.iterrows()])

                mode = st.radio("Format", ["Cloze", "List"], horizontal=True)
                instr = st.text_input("Instructions", placeholder="Ex: Focus on MRI findings...")

                if st.button("✨ Generate Cards", type="primary"):
                    if not st.session_state.api_key:
                        st.error("No API Key found.")
                    else:
                        try:
                            with st.spinner(f"Generating with {st.session_state.selected_model}..."):
                                genai.configure(api_key=st.session_state.api_key)
                                model = genai.GenerativeModel(st.session_state.selected_model)

                                # Prompt in English
                                mem = f"DO NOT generate these questions again (already done):\n{existing_ctx}" if existing_ctx else ""
                                sys = """Role: Elite Medical Editor for Radiopaedia. 
                                Goal: Create Anki flashcards.
                                Rules:
                                1. STRICTLY Stand-Alone cards: Never use 'It', 'They', 'The lesion'. Always name the pathology/sign in the question.
                                2. Format: Question|Answer|Tag
                                3. Separator is purely pipe (|). No Markdown tables.
                                4. Language: English."""

                                prompt = f"{sys}\n{mem}\n\nTask:\nArticle Title: {current_row['title']}\nFormat Mode: {mode}\nUser Instruction: {instr}\n\nSource Content:\n{current_row['content']}"

                                resp = model.generate_content(prompt)

                                clean = resp.text.replace("```", "").strip()
                                batch = []
                                for l in clean.split('\n'):
                                    if '|' in l and len(l) > 5:
                                        p = l.split('|')
                                        if len(p) >= 2:
                                            batch.append({
                                                "rid": str(current_row['rid']),
                                                "article_title": current_row['title'],
                                                "system": current_row['system'],
                                                "card_type": mode,
                                                "question": p[0].strip(),
                                                "answer": p[1].strip(),
                                                "tags": p[2].strip() if len(p) > 2 else ""
                                            })

                                if batch:
                                    st.session_state.draft_cards.extend(batch)
                                    st.success(f"{len(batch)} cards generated!")
                                    st.rerun()
                                else:
                                    st.warning("AI generated no formatted output. Try again.")

                        except Exception as e:
                            st.error(f"Gemini API Error: {e}")

                if st.session_state.draft_cards:
                    st.divider()
                    st.write("### 📝 Draft (Unsaved)")
                    draft_df = pd.DataFrame(st.session_state.draft_cards)

                    edited_draft = st.data_editor(
                        draft_df,
                        num_rows="dynamic",
                        key="draft_editor",
                        column_config={
                            "rid": None, "article_title": None, "system": None, "card_type": None
                        }
                    )

                    c_save, c_clear = st.columns(2)
                    with c_save:
                        if st.button("💾 Save to Sheets", type="primary"):
                            try:
                                if sh_obj:
                                    ws_cards = sh_obj.worksheet("Cards")
                                    save_df = edited_draft[
                                        ['rid', 'article_title', 'system', 'card_type', 'question', 'answer', 'tags']]
                                    rows_to_add = save_df.values.tolist()

                                    if rows_to_add:
                                        ws_cards.append_rows(rows_to_add)

                                        try:
                                            cell = worksheet.find(str(current_row['rid']))
                                            headers = worksheet.row_values(1)
                                            if 'flashcards_made' in headers:
                                                worksheet.update_cell(cell.row, headers.index('flashcards_made') + 1,
                                                                      "TRUE")
                                                idx = \
                                                df_base[df_base['rid'].astype(str) == str(current_row['rid'])].index[0]
                                                st.session_state.df.at[idx, 'flashcards_made'] = True
                                        except:
                                            pass

                                        st.session_state.draft_cards = []
                                        st.toast("Cards Saved!", icon="🎉")
                                        time.sleep(1)
                                        st.rerun()
                            except Exception as e:
                                st.error(f"Save Error: {e}")

                    with c_clear:
                        if st.button("🗑️ Clear Draft"):
                            st.session_state.draft_cards = []
                            st.rerun()