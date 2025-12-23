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
        /* Style des onglets */
        .stTabs [data-baseweb="tab-list"] {gap: 10px;}
        .stTabs [data-baseweb="tab"] {height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 5px;}
        .stTabs [aria-selected="true"] {background-color: #ffffff; border-bottom: 2px solid #ff4b4b;}
    </style>
""", unsafe_allow_html=True)


# ==========================================
# 2. FONCTIONS BACKEND
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
        # Assurer les colonnes minimales
        expected_cols = ['rid', 'article_title', 'system', 'card_type', 'question', 'answer', 'tags']
        if df_cards.empty:
            df_cards = pd.DataFrame(columns=expected_cols)
        else:
            for c in expected_cols:
                if c not in df_cards.columns: df_cards[c] = ""
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
# 4. BARRE LATÉRALE (Commune)
# ==========================================
with st.sidebar:
    st.title("🩻 Radiopaedia")
    st.header("⚙️ Config IA")

    if "GEMINI_API_KEY" in st.secrets:
        st.session_state.api_key = st.secrets["GEMINI_API_KEY"]
    else:
        api_input = st.text_input("Clé Gemini", value=st.session_state.api_key, type="password")
        if api_input: st.session_state.api_key = api_input

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
        default_idx = 0
        for i, name in enumerate(fetched_models):
            if "flash" in name.lower(): default_idx = i; break
        st.session_state.selected_model = st.selectbox("Modèle IA", fetched_models, index=default_idx)

    st.divider()

    # Export Anki Global
    if "sh_obj" in st.session_state and st.session_state.sh_obj:
        st.subheader("📤 Export Global")
        if st.button("Télécharger Tout (.txt)"):
            df_c, _ = load_cards_data(st.session_state.sh_obj)
            if not df_c.empty:
                out = io.StringIO()
                out.write("#separator:Pipe\n#html:true\n#tags column:4\n")
                for _, r in df_c.iterrows():
                    q = str(r['question']).replace('|', '/')
                    a = str(r['answer']).replace('|', '/')
                    tag = str(r.get('tags', '')).strip() or str(r['article_title']).replace(' ', '_')
                    out.write(f"{q}|{a}|{r['card_type']}|{tag}\n")
                st.download_button("Sauvegarder", data=out.getvalue(), file_name=f"anki_full_{date.today()}.txt")

# ==========================================
# 5. CHARGEMENT INITIAL
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
# 6. APPLICATION PRINCIPALE (ONGLETS)
# ==========================================

# Création des deux onglets
tab_cockpit, tab_manager = st.tabs(["🚀 Cockpit de Lecture", "🗃️ Gestion des Cartes"])

# ==========================================
# ONGLET 1 : LE COCKPIT (Ton app actuelle)
# ==========================================
with tab_cockpit:
    if df_base is not None:
        # --- TRACKER ---
        with st.expander("🔍 Filtrer la liste des articles", expanded=False):
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

        # --- ESPACE DE TRAVAIL ---
        if st.session_state.current_rid:
            current_row_mask = df_base['rid'].astype(str) == str(st.session_state.current_rid)
            if current_row_mask.any():
                current_row = df_base[current_row_mask].iloc[0]

                st.markdown("---")
                col_left, col_right = st.columns([1, 1])

                with col_left:
                    st.subheader(f"📖 {current_row['title']}")
                    if current_row['url']:
                        try:
                            components.iframe(current_row['url'], height=850, scrolling=True)
                        except:
                            st.markdown(f"[Lien externe]({current_row['url']})")

                with col_right:
                    st.subheader("🧠 Générateur Interactif")

                    # Contexte (Mémoire)
                    existing_context_text = ""
                    card_count = 0

                    if sh_obj:
                        df_c, _ = load_cards_data(sh_obj)
                        if not df_c.empty:
                            saved_cards = df_c[df_c['rid'].astype(str) == str(current_row['rid'])]
                            if not saved_cards.empty:
                                card_count += len(saved_cards)
                                existing_context_text += "--- SAVED CARDS ---\n"
                                for _, r in saved_cards.iterrows():
                                    existing_context_text += f"Q: {r['question']} | A: {r['answer']}\n"

                    if st.session_state.draft_cards:
                        card_count += len(st.session_state.draft_cards)
                        existing_context_text += "--- DRAFT CARDS ---\n"
                        for r in st.session_state.draft_cards:
                            existing_context_text += f"Q: {r['question']} | A: {r['answer']}\n"

                    if card_count > 0:
                        st.info(f"ℹ️ {card_count} cartes en mémoire.")

                    # Formulaire
                    with st.form("ai_form"):
                        mode = st.radio("Format", ["Cloze (Trous)", "Basic"], horizontal=True)
                        custom_inst = st.text_input("Instruction")
                        label_btn = "✨ Générer" if card_count == 0 else "➕ Ajouter Complémentaires"
                        submitted_gen = st.form_submit_button(label_btn, type="primary")

                    if submitted_gen:
                        if not st.session_state.api_key:
                            st.error("Manque clé API.")
                        else:
                            try:
                                genai.configure(api_key=st.session_state.api_key)
                                model = genai.GenerativeModel(st.session_state.selected_model)

                                sys_prompt = """
                                System Prompt: Radiology Anki Architect v3.1
                                Role: Create Anki cards.
                                CONTEXT AWARENESS: Check 'EXISTING CARDS'. Avoid duplicates. Find new angles.
                                FORMAT CLOZE: {{c1::Pathology}} shows {{c2::sign}}. COL 2 (Answer) MUST BE EMPTY.
                                FORMAT BASIC: Classic Q&A.
                                OUTPUT: Column1|Column2|Column3
                                """

                                full_prompt = f"{sys_prompt}\n\nEXISTING CARDS:\n{existing_context_text}\n\nArticle: {current_row['title']}\nFormat: {mode}\nInstr: {custom_inst}\nText:\n{current_row['content']}"

                                with st.spinner("Réflexion..."):
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
                                        st.rerun()
                                    else:
                                        st.warning("Rien de nouveau généré.")
                            except Exception as e:
                                st.error(f"Erreur IA: {e}")

                    # Brouillon
                    if st.session_state.draft_cards:
                        st.divider()
                        st.subheader("📝 Brouillon")
                        draft_df = pd.DataFrame(st.session_state.draft_cards)
                        edited_draft = st.data_editor(draft_df[['question', 'answer', 'tags']], num_rows="dynamic",
                                                      key="draft_edit")

                        col_save, col_clear = st.columns(2)
                        if col_save.button("💾 Valider"):
                            try:
                                final_rows_to_save = []
                                for idx, r in edited_draft.iterrows():
                                    final_rows_to_save.append([
                                        str(current_row['rid']), current_row['title'], current_row['system'],
                                        "Cloze" if "{{" in r['question'] else "Basic",
                                        r['question'], r['answer'], r['tags']
                                    ])
                                if final_rows_to_save:
                                    _, ws_cards = load_cards_data(sh_obj)
                                    ws_cards.append_rows(final_rows_to_save)

                                    # Update statut
                                    cell = worksheet.find(str(current_row['rid']))
                                    headers = worksheet.row_values(1)
                                    if 'flashcards_made' in headers:
                                        worksheet.update_cell(cell.row, headers.index('flashcards_made') + 1, "Oui")
                                        idx_local = \
                                        df_base.index[df_base['rid'].astype(str) == str(current_row['rid'])].tolist()[0]
                                        st.session_state.df.at[idx_local, 'flashcards_made'] = True

                                    st.session_state.draft_cards = []
                                    st.toast("Sauvegardé !", icon="🎉")
                                    time.sleep(1)
                                    st.rerun()
                            except Exception as e:
                                st.error(f"Erreur: {e}")

                        if col_clear.button("🗑️ Effacer"):
                            st.session_state.draft_cards = []
                            st.rerun()
        else:
            st.info("👈 Sélectionne un article.")

# ==========================================
# ONGLET 2 : GESTIONNAIRE DE CARTES (NOUVEAU)
# ==========================================
with tab_manager:
    st.header("🗃️ Base de Données des Flashcards")
    st.caption(
        "Filtrez, éditez ou supprimez vos cartes générées. Attention : les suppressions ici sont définitives sur le Google Sheet.")

    if sh_obj:
        # Chargement frais des cartes
        df_cards_all, ws_cards_all = load_cards_data(sh_obj)

        if not df_cards_all.empty:
            # --- FILTRES ---
            col_f1, col_f2, col_f3 = st.columns(3)

            # Filtre Système
            u_sys_cards = sorted(list(set(df_cards_all['system'].astype(str).tolist())))
            sel_sys_cards = col_f1.multiselect("Filtrer par Système", u_sys_cards)

            # Filtre Article (Titre)
            u_title_cards = sorted(list(set(df_cards_all['article_title'].astype(str).tolist())))
            sel_title_cards = col_f2.multiselect("Filtrer par Article", u_title_cards)

            # Recherche Texte
            search_cards = col_f3.text_input("Recherche dans les questions", "")

            # Application des filtres
            df_cards_view = df_cards_all.copy()

            if sel_sys_cards:
                df_cards_view = df_cards_view[df_cards_view['system'].isin(sel_sys_cards)]
            if sel_title_cards:
                df_cards_view = df_cards_view[df_cards_view['article_title'].isin(sel_title_cards)]
            if search_cards:
                df_cards_view = df_cards_view[
                    df_cards_view['question'].str.contains(search_cards, case=False, na=False)]

            st.markdown(f"**{len(df_cards_view)}** cartes affichées (sur {len(df_cards_all)} au total).")

            # --- TABLEAU ÉDITABLE ---
            # On permet l'ajout et la suppression de lignes (num_rows="dynamic")
            edited_cards = st.data_editor(
                df_cards_view,
                num_rows="dynamic",
                use_container_width=True,
                key="manager_editor",
                column_config={
                    "rid": st.column_config.TextColumn("RID", disabled=True, width="small"),
                    "article_title": st.column_config.TextColumn("Article", disabled=True),
                    "system": st.column_config.TextColumn("Système", disabled=True, width="small"),
                    "question": st.column_config.TextColumn("Question (Front)", width="large"),
                    "answer": st.column_config.TextColumn("Answer (Back/Extra)", width="medium"),
                    "tags": st.column_config.TextColumn("Tags", width="small"),
                    "card_type": st.column_config.SelectboxColumn("Type", options=["Cloze", "Basic"], width="small")
                }
            )

            # --- BOUTON DE SAUVEGARDE ---
            # La logique ici est délicate : comment réconcilier les edits avec le sheet ?
            # Pour une app perso (< 5000 cartes), la méthode la plus sûre et simple est :
            # 1. Si on détecte un changement, on demande confirmation.
            # 2. On réécrit TOUT l'onglet Cards avec les nouvelles données (ceux filtrés + ceux non filtrés mais non touchés).
            # Mais attention : si on filtre, on ne voit pas tout.

            # Approche simplifiée robuste :
            # On ne permet la sauvegarde que des modifications faites sur la VUE actuelle,
            # mais il faut être sûr de ne pas écraser les autres.

            if st.button("💾 Appliquer les modifications au Google Sheet", type="primary"):
                try:
                    # 1. Identifier les lignes modifiées/supprimées est complexe avec des filtres.
                    # L'astuce : On recharge tout, on supprime les lignes qui correspondent à notre VUE actuelle (avant edit),
                    # et on ajoute les lignes de notre VUE éditée.

                    # Mais le plus simple pour éviter les bugs d'index :
                    # Si l'utilisateur a filtré, il est risqué de réécrire.
                    # On va utiliser une approche "Rewrite All" UNIQUEMENT si aucun filtre n'est actif,
                    # OU on avertit l'utilisateur.

                    if len(df_cards_view) != len(df_cards_all):
                        st.warning(
                            "⚠️ Attention : Vous utilisez des filtres. La modification directe en mode filtré est désactivée par sécurité pour éviter de perdre les cartes masquées. Veuillez effacer les filtres pour faire des modifications de masse.")
                    else:
                        # Pas de filtre actif, on peut réécrire l'onglet en toute sécurité
                        # Convertir le DF édité en liste de listes
                        # On garde l'ordre des colonnes du Sheet
                        cols = ['rid', 'article_title', 'system', 'card_type', 'question', 'answer', 'tags']
                        # Assurer que edited_cards a toutes les colonnes
                        final_data = [cols] + edited_cards[cols].values.tolist()

                        ws_cards_all.clear()
                        ws_cards_all.update(final_data)

                        st.success("Base de données mise à jour avec succès !")
                        time.sleep(1)
                        st.rerun()

                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")
        else:
            st.info("Aucune carte trouvée dans l'onglet 'Cards'. Commencez par en générer dans le Cockpit !")