import streamlit as st
import pandas as pd
import altair as alt
import json
import os
import random
import datetime
import folium
from streamlit_folium import st_folium
from utils import load_data, calculate_recommendations, create_txt, load_night_markets, TAG_MAPPING, get_coordinates

# ==========================================
# 1. 全域設定
# ==========================================
st.set_page_config(page_title="高雄旅遊智慧規劃助手", layout="wide", page_icon="🧳")

USER_DB_FILE = "users_db.json"
HOURS_OPTIONS = [f"{i:02d}:00" for i in range(24)] # Deprecated but kept for compatibility logic
CATEGORY_OPTIONS = ["景點", "飲食", "交通", "住宿", "購物", "活動", "其他"]
WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
GOOGLE_MAPS_API_KEY = "" 

# --- 本地資料庫函式 ---
def load_db():
    if not os.path.exists(USER_DB_FILE): return {}
    try:
        with open(USER_DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def save_db(db):
    with open(USER_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=4)

def update_user_data(username, data_key, data_value):
    db = load_db()
    if username in db:
        db[username][data_key] = data_value
        save_db(db)

def change_password(username, new_password):
    db = load_db()
    if username in db:
        db[username]["password"] = new_password
        save_db(db)
        return True
    return False

# --- Session State 初始化 ---
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'user_name' not in st.session_state: st.session_state.user_name = ""
if 'itinerary' not in st.session_state: st.session_state.itinerary = []
if 'preferences' not in st.session_state: st.session_state.preferences = None
if 'recommendations' not in st.session_state: st.session_state.recommendations = None
if 'trip_info' not in st.session_state:
    st.session_state.trip_info = {"name": "我的高雄之旅", "days": 2, "start_date": datetime.date.today(), "budget": 5000, "pre_spent": 0}
if 'map_center' not in st.session_state: st.session_state.map_center = [22.6273, 120.3014]
if 'map_zoom' not in st.session_state: st.session_state.map_zoom = 12
if 'focus_spot' not in st.session_state: st.session_state.focus_spot = None
if 'candidates' not in st.session_state: st.session_state.candidates = [] # New: Candidate List

# [Architecture Change] Merged History into Home, removed Page 5
PAGES = ["🏠 首頁 (我的旅程)", "1. 建立新旅程", "2. 旅遊偏好", "3. 行程規劃", "4. 總覽與匯出"]
if 'current_page' not in st.session_state: st.session_state.current_page = PAGES[0]
# [Fix] Safety check for legacy session state
if st.session_state.current_page not in PAGES:
    st.session_state.current_page = PAGES[0]

# --- Helper Functions ---
def navigate_to(page_name): st.session_state.current_page = page_name

def save_current_state():
    if st.session_state.logged_in and st.session_state.user_name:
        rec_data = None
        if st.session_state.recommendations is not None and not st.session_state.recommendations.empty:
            rec_data = st.session_state.recommendations.to_dict('records')
        user_data = {
            "trip_info": st.session_state.trip_info,
            "itinerary": st.session_state.itinerary,
            "preferences": st.session_state.preferences,
            "recommendations": rec_data,
            "candidates": st.session_state.candidates, # [Fix] Save candidates
            "current_page": st.session_state.current_page,
            "last_modified": str(datetime.datetime.now())
        }
        update_user_data(st.session_state.user_name, "data", user_data)

def save_to_history(history_name):
    if st.session_state.logged_in and st.session_state.user_name:
        db = load_db()
        user_entry = db[st.session_state.user_name]
        if "history" not in user_entry: user_entry["history"] = {}
        rec_data = None
        if st.session_state.recommendations is not None and not st.session_state.recommendations.empty:
            rec_data = st.session_state.recommendations.to_dict('records')
        current_snapshot = {
            "trip_info": st.session_state.trip_info,
            "itinerary": st.session_state.itinerary,
            "preferences": st.session_state.preferences,
            "recommendations": rec_data,
            "saved_at": str(datetime.datetime.now())
        }
        user_entry["history"][history_name] = current_snapshot
        save_db(db)
        st.success(f"已儲存：{history_name}")

def delete_history(history_name):
    if st.session_state.logged_in:
        db = load_db()
        user_entry = db[st.session_state.user_name]
        if "history" in user_entry and history_name in user_entry["history"]:
            del user_entry["history"][history_name]
            save_db(db)
            st.success(f"已刪除：{history_name}")
            st.rerun()

def delete_item(index):
    st.session_state.itinerary.pop(index)
    save_current_state()

def move_item(index, direction):
    items = st.session_state.itinerary
    new_index = index + direction
    if 0 <= new_index < len(items):
        items[index], items[new_index] = items[new_index], items[index]
        save_current_state()

# 輔助：確保 SubBudgets 結構存在
def ensure_sub_budgets(item):
    if 'SubBudgets' not in item or not isinstance(item['SubBudgets'], list):
        # 舊資料相容：如果有 Cost 但沒有 SubBudgets，轉為第一筆
        cost = item.get('Cost', 0)
        if cost > 0:
            item['SubBudgets'] = [{
                "Category": item.get('Category', '其他'),
                "Cost": cost,
                "Note": item.get('Note', '')
            }]
        else:
            item['SubBudgets'] = []
    return item

# [新增 Callback] 處理新增預算細項，避免 StreamlitAPIException
def add_sub_budget_callback(item, key_cat, key_desc, key_val):
    # 從 session_state 讀取輸入值
    cat = st.session_state[key_cat]
    desc = st.session_state[key_desc]
    val_str = st.session_state[key_val]
    
    try: cost = int(val_str)
    except: cost = 0
    
    # 新增資料
    item['SubBudgets'].append({
        "Category": cat, "Note": desc, "Cost": cost
    })
    
    # 更新總額
    item['Cost'] = sum(s['Cost'] for s in item['SubBudgets'])
    
    # 清空輸入框 (這是合法的，因為是在 callback 中執行，尚未進入下一輪 render)
    st.session_state[key_desc] = ""
    st.session_state[key_val] = ""
    
    save_current_state()

# [新增 Callback] 關閉新增模式
def close_add_mode_callback(key_mode):
    st.session_state[key_mode] = False

# ==========================================
# 2. 登入/註冊系統
# ==========================================
if not st.session_state.logged_in:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🔐 旅遊規劃登入系統")
        tab_login, tab_register = st.tabs(["登入", "註冊新帳號"])
        with tab_login:
            with st.form("login_form"):
                login_user = st.text_input("帳號")
                login_pass = st.text_input("密碼", type="password")
                if st.form_submit_button("登入", type="primary", use_container_width=True):
                    db = load_db()
                    if login_user in db and db[login_user]["password"] == login_pass:
                        st.session_state.logged_in = True
                        st.session_state.user_name = login_user
                        saved_data = db[login_user].get("data", {})
                        if saved_data:
                            st.session_state.trip_info = saved_data.get("trip_info", st.session_state.trip_info)
                            st.session_state.itinerary = saved_data.get("itinerary", [])
                            st.session_state.preferences = saved_data.get("preferences", None)
                            st.session_state.candidates = saved_data.get("candidates", []) # [Fix] Load candidates
                            st.session_state.current_page = saved_data.get("current_page", PAGES[0])
                            rec_data = saved_data.get("recommendations", None)
                            if rec_data: st.session_state.recommendations = pd.DataFrame(rec_data)
                        st.success("登入成功！")
                        st.rerun()
                    else: st.error("帳號或密碼錯誤")
        with tab_register:
            with st.form("register_form"):
                reg_user = st.text_input("設定帳號")
                reg_pass = st.text_input("設定密碼", type="password")
                if st.form_submit_button("註冊", use_container_width=True):
                    db = load_db()
                    if reg_user in db: st.error("此帳號已被註冊")
                    elif reg_user and reg_pass:
                        db[reg_user] = {"password": reg_pass, "data": {}, "history": {}}
                        save_db(db)
                        st.success("註冊成功！請登入。")
                    else: st.error("請輸入帳號與密碼")
    st.stop()

# ==========================================
# 3. 側邊欄控制
# ==========================================
# ==========================================
# 3. 側邊欄控制 (Modern UI)
# ==========================================
with st.sidebar:
    # 1. User Profile Header
    # Simple layout: Avatar | Welcome
    c1, c2 = st.columns([1, 4])
    with c1: st.write("👤")
    with c2: st.markdown(f"**Hi, {st.session_state.user_name}**")
    
    st.divider()
    
    # 2. Navigation
    try: curr_idx = PAGES.index(st.session_state.current_page)
    except: curr_idx = 0
    
    # Use generic label or hidden label for cleaner look
    selected_page = st.radio("導航", PAGES, index=curr_idx, label_visibility="collapsed")
    
    if selected_page != st.session_state.current_page:
        st.session_state.current_page = selected_page
        st.rerun()
        
    st.divider()

    # 3. Trip Dashboard (Only show if logged in and past home)
    if st.session_state.current_page in PAGES[1:]:
        with st.container(border=True):
            st.markdown(f"### 🚩 {st.session_state.trip_info['name']}")
            
            # Date Info
            s_date = st.session_state.trip_info['start_date']
            days = st.session_state.trip_info['days']
            st.caption(f"📅 {s_date} ({days} 天)")

            # Budget Viz
            cur_budget = st.session_state.trip_info['budget']
            plan_spent = sum(item['Cost'] for item in st.session_state.itinerary)
            total_spent = st.session_state.trip_info.get('pre_spent', 0) + plan_spent
            remaining_budget = cur_budget - total_spent
            
            # Progress Bar logic
            if cur_budget > 0:
                usage_pct = min(1.0, max(0.0, total_spent / cur_budget))
            else:
                usage_pct = 0.0
            
            st.progress(usage_pct, text=f"預算使用率 {int(usage_pct*100)}%")
            
            # Metrics Grid
            m1, m2 = st.columns(2)
            m1.metric("已使用", f"${total_spent:,}")
            m2.metric("剩餘", f"${remaining_budget:,}", delta_color="normal" if remaining_budget >= 0 else "inverse")
            
            # Budget Edit inside Expander to keep clean
            with st.expander("⚙️ 設定預算", expanded=False):
               # 1. Total Budget
               new_budget_str = st.text_input("總預算", value=str(cur_budget))
               
               # 2. Pre-spent Budget [New]
               cur_pre_spent = st.session_state.trip_info.get('pre_spent', 0)
               new_pre_spent_str = st.text_input("已預支 (行前花費)", value=str(cur_pre_spent))
               
               try:
                   new_budget = int(new_budget_str)
                   if new_budget < 0: new_budget = 0
               except: new_budget = cur_budget
               
               try:
                   new_pre_spent = int(new_pre_spent_str)
                   if new_pre_spent < 0: new_pre_spent = 0
               except: new_pre_spent = cur_pre_spent
                   
               if new_budget != cur_budget or new_pre_spent != cur_pre_spent:
                   st.session_state.trip_info['budget'] = new_budget
                   st.session_state.trip_info['pre_spent'] = new_pre_spent
                   save_current_state()
                   st.rerun()

    st.markdown("---")
    if st.button("🚪 登出", type="secondary", use_container_width=True):
        save_current_state()
        st.session_state.logged_in = False
        st.session_state.user_name = ""
        st.session_state.itinerary = []
        st.session_state.recommendations = None
        st.session_state.current_page = PAGES[0]
        st.rerun()

# --- 🏠 首頁 (歷史行程整合) ---
if st.session_state.current_page == PAGES[0]:
    st.title(f"👋 嗨，{st.session_state.user_name}！")
    
    db = load_db()
    hist = db.get(st.session_state.user_name, {}).get("history", {})
    
    # === 情境 A：新使用者 (無歷史紀錄) ===
    if not hist:
        st.markdown("### 歡迎來到高雄旅遊智慧規劃助手！🚀")
        st.info("看起來您還沒有建立過任何行程。別擔心，讓我們開始您的第一次規劃吧！")
        
        # Hero Section
        with st.container(border=True):
            # [Refine] Use vertical_alignment="center" to create a "Magazine Spread" feel
            # Ratio 1.2 : 1 gives enough space for text while keeping image substantial
            c1, c2 = st.columns([1.2, 1], gap="large", vertical_alignment="center")
            
            with c1:
                st.markdown("### 🌟 探索．規劃．出發")
                st.markdown("##### 為您量身打造的完美旅程")
                st.write("") # Spacer
                
                # Stylish list using markdown
                st.markdown("""
                > **🎯 AI 智能推薦**  
                > 根據您的偏好，發掘隱藏版美食與景點。
                
                > **🧘 彈性自在**  
                > 隨時調整行程，享受說走就走的自由。
                
                > **📂 一鍵帶著走**  
                > 支援 TXT 與 CSV 匯出，行程細節一手掌握。
                """)
                
                st.write("") # Spacer
                if st.button("🚀 開始規劃我的旅程", type="primary", use_container_width=True):
                    # 清空狀態，開始新 Session
                    st.session_state.itinerary = []
                    st.session_state.recommendations = None
                    st.session_state.preferences = None
                    st.session_state.trip_info = {"name": "高雄首遊", "days": 2, "start_date": datetime.date.today(), "budget": 5000, "pre_spent": 0}
                    navigate_to(PAGES[1]) # 前往設定頁
                    st.rerun()
                    
            with c2:
                # [Mod] Rotating Magazine Style Images (3:4 ratio)
                # Placeholders for user to fill in
                # Suggestion: Use high-quality portrait photos (e.g. 900x1200)
                hero_images = [
                    "https://i.meee.com.tw/kqPJjgg.jpg", # Image 1
                    "https://i.meee.com.tw/Y7is20S.jpg", # Image 2 
                    "https://i.meee.com.tw/ObYVXZN.jpg"  # Image 3 
                ]
                selected_hero = random.choice(hero_images)
                st.image(selected_hero, use_container_width=True)

    # === 情境 B：老朋友 (有歷史紀錄) ===
    else:
        # 1. 建立新旅程區塊 (Dashboard Hero)
        with st.container(border=True):
            c1, c2 = st.columns([0.8, 0.2], vertical_alignment="center")
            c1.subheader("🚀 準備好出發了嗎？")
            c1.caption("建立一個全新的高雄旅遊計畫，AI 會協助您安排最合適的景點。")
            if c2.button("➕ 建立新旅程", type="primary", use_container_width=True):
                # 清空狀態，開始新 Session
                st.session_state.itinerary = []
                st.session_state.recommendations = None
                st.session_state.preferences = None
                st.session_state.trip_info = {"name": "新旅程", "days": 2, "start_date": datetime.date.today(), "budget": 5000, "pre_spent": 0}
                navigate_to(PAGES[1]) # 前往設定頁
                st.rerun()

        st.divider()

        # 2. 歷史行程列表
        st.subheader("📂 我的旅程列表")
        sorted_hist = sorted(hist.items(), key=lambda x: x[1].get('saved_at', ''), reverse=True)
        
        for name, data in sorted_hist:
            saved_time = data.get('saved_at', '未記錄時間')[:16] 
            days_count = data.get('trip_info',{}).get('days', '?')
            with st.container(border=True):
                hc1, hc2, hc3 = st.columns([0.6, 0.2, 0.2])
                with hc1:
                    st.markdown(f"#### 🗺️ {name}")
                    st.caption(f"📅 最後儲存：{saved_time} • ⏳ 天數：{days_count} 天")
                
                if hc2.button("✏️ 繼續編輯", key=f"load_{name}", use_container_width=True):
                    st.session_state.itinerary = data.get('itinerary', [])
                    st.session_state.trip_info = data.get('trip_info', {})
                    st.session_state.preferences = data.get('preferences', None)
                    if data.get('recommendations'):
                        st.session_state.recommendations = pd.DataFrame(data['recommendations'])
                    else:
                        st.session_state.recommendations = None
                    navigate_to(PAGES[3]) # 直接進入規劃頁
                    save_current_state()
                    st.rerun()
                
                if hc3.button("🗑️ 刪除", key=f"del_{name}", type="primary", use_container_width=True):
                    delete_history(name)
                    st.rerun()

# --- 1. 建立旅程 ---

elif st.session_state.current_page == PAGES[1]:
    st.title("📝 步驟 1：建立旅程")
    with st.form("init_form"):
        c1, c2 = st.columns(2)
        trip_name = c1.text_input("旅程名稱", value=st.session_state.trip_info['name'])
        # [Modify] Text input for budget
        budget_str = c2.text_input("總預算 (TWD)", value=str(st.session_state.trip_info['budget']))
        
        c3, c4 = st.columns(2)
        # [Modify] Switch to date input
        default_start = st.session_state.trip_info.get('start_date', datetime.date.today())
        # If it's a string (from JSON), convert back
        if isinstance(default_start, str):
            try: default_start = datetime.datetime.strptime(default_start, "%Y-%m-%d").date()
            except: default_start = datetime.date.today()
            
        # [Fix] Ensure default_start is not in the past relative to min_value (today)
        if default_start < datetime.date.today():
            default_start = datetime.date.today()
            
        default_end = default_start + datetime.timedelta(days=st.session_state.trip_info.get('days', 2)-1)
        
        dates = c3.date_input("選擇旅行日期 (起~迄)", value=[default_start, default_end], min_value=datetime.date.today())
        
        # [Modify] Text input for pre-spent
        pre_spent_str = c4.text_input("已使用預算", value=str(st.session_state.trip_info.get('pre_spent', 0)))
        
        if st.form_submit_button("下一步 ➡️", type="primary"):
            if len(dates) == 2:
                start_d, end_d = dates
                days_calc = (end_d - start_d).days + 1
            else:
                start_d = dates[0]
                days_calc = 1
            
            # Parse inputs
            try: budget = int(budget_str)
            except: budget = 0
            try: pre_spent = int(pre_spent_str)
            except: pre_spent = 0
                
            st.session_state.trip_info.update({
                'name': trip_name, 
                'budget': budget, 
                'days': days_calc, 
                'start_date': str(start_d),
                'pre_spent': pre_spent
            })
            # [Fix] Reset itinerary and candidates to ensure clean state for "New Trip"
            st.session_state.itinerary = []
            st.session_state.candidates = []
            st.session_state.recommendations = None
            save_current_state()
            navigate_to(PAGES[2]); st.rerun()

# --- 2. 旅遊偏好 ---
elif st.session_state.current_page == PAGES[2]:
    st.title("🧩 步驟 2：這次旅行，您想玩什麼？")
    with st.form("quiz_form"):
        saved_prefs = st.session_state.preferences or {}
        
        # [Modify] Custom Scales for Question Context
        scale_nature = ["完全市區派", "偏向市區", "都可以", "偏向自然", "擁抱大自然"]
        scale_interest = ["沒興趣", "不太有興趣", "普通", "有興趣", "非常感興趣"]
        scale_priority = ["不需安排", "可有可無", "看時間", "想去", "一定要去"]

        def get_saved_idx(val):
            if val is None: return 2
            return int(max(0, min(4, val * 4)))
            
        st.markdown("""
        <style>
            /* 
               Refined Radio Fix:
               1. Use Padding ONLY (10px) to create internal buffer for the focus ring.
               2. precise padding-left/right for labels to balance spacing.
               3. Increase line-height to prevent vertical clipping.
            */
            div[role="radiogroup"] {
                padding: 10px;
                /* Note: Removed negative margin as it pulls content back into clipping zone */
            }
            
            div[data-testid="stRadio"] label {
                padding-right: 20px !important;
                line-height: 1.6 !important;
            }
        </style>
        """, unsafe_allow_html=True)

        st.info("💡 為了更精準推薦，我們將問題分為五大面向，請依照您這次的旅遊心情回答：")

        # Row 1
        r1c1, r1c2 = st.columns(2)
        with r1c1:
            st.markdown("##### 1. 自然光譜 🌲")
            st.caption("想去山上海邊透透氣，還是待在市區就好？")
            q1_val = st.radio("nature", scale_nature, index=get_saved_idx(saved_prefs.get('nature')), horizontal=True, label_visibility="collapsed", key="q1")
        with r1c2:
            st.markdown("##### 2. 老靈魂 (歷史/宗教) 🏯")
            st.caption("喜歡古蹟、廟宇、老街的懷舊氛圍嗎？")
            q2_val = st.radio("history", scale_interest, index=get_saved_idx(saved_prefs.get('history')), horizontal=True, label_visibility="collapsed", key="q2")

        # Row 2
        r2c1, r2c2 = st.columns(2)
        with r2c1:
            st.markdown("##### 3. 新潮流 (網美/文創) 🎨")
            st.caption("喜歡駁二、美術館、拍美照的現代景點嗎？")
            q3_val = st.radio("trend", scale_interest, index=get_saved_idx(saved_prefs.get('trend')), horizontal=True, label_visibility="collapsed", key="q3")
        with r2c2:
            st.markdown("##### 4. 玩樂性質 (親子/遊樂) 🎡")
            st.caption("這次有帶小孩，或想去觀光工廠/遊樂園玩嗎？")
            q4_val = st.radio("fun", scale_priority, index=get_saved_idx(saved_prefs.get('fun')), horizontal=True, label_visibility="collapsed", key="q4")
            
        # Row 3
        r3c1, r3c2 = st.columns(2)
        with r3c1:
            st.markdown("##### 5. 都市生活 (逛街/美食) 🛍️")
            st.caption("喜歡逛商圈、吃夜市的熱鬧感覺嗎？")
            q5_val = st.radio("urban", scale_interest, index=get_saved_idx(saved_prefs.get('urban')), horizontal=True, label_visibility="collapsed", key="q5")

        st.markdown("---")
        st.subheader("加分興趣標籤")
        
        tag_options = [
            "🏯 歷史古蹟", "🎨 藝文文創", "🎡 親子樂園", 
            "⛰️ 山林步道", "🌊 海港水域", "🛍️ 逛街美食", "📸 網美打卡",
            "🚂 鐵道交通", "🙏 宗教巡禮", "🚲 單車漫遊",
            "🛖 原民部落", "🏘️ 眷村故事"
        ]
        
        q_tags = st.pills(
            "還有對什麼特別感興趣的嗎？ (可複選)",
            tag_options,
            selection_mode="multi",
            key="q_tags"
        )
        
        col_submit = st.columns([1, 2, 1])
        with col_submit[1]:
            submit = st.form_submit_button("✨ 開始與 AI 規劃行程", type="primary", use_container_width=True)
            
        def process_quiz():
            try: df = load_data()
            except: return
            
            # Map labels back to 0.0 ~ 1.0 using list index
            p_nature = scale_nature.index(q1_val) / 4.0
            p_history = scale_interest.index(q2_val) / 4.0
            p_trend = scale_interest.index(q3_val) / 4.0
            p_fun = scale_priority.index(q4_val) / 4.0
            p_urban = scale_interest.index(q5_val) / 4.0
            
            prefs = {
                'nature': p_nature, 
                'history': p_history, 
                'trend': p_trend, 
                'fun': p_fun, 
                'urban': p_urban
            }
            st.session_state.preferences = prefs
            
            st.session_state.recommendations = calculate_recommendations(
                df, prefs, st.session_state.q_tags, days=st.session_state.trip_info.get('days', 1)
            )
            save_current_state()
            navigate_to(PAGES[3])
            st.rerun()

        # [Fix] Logic now handled by 'submit' variable, no duplicate button
        if submit:
            process_quiz()

# --- 3. 行程規劃 ---
elif st.session_state.current_page == PAGES[3]:
    if st.session_state.recommendations is None:
        st.warning("⚠️ 請先完成測驗！")
        if st.button("⬅️ 回去測驗"): navigate_to(PAGES[2]); st.rerun()
        st.stop()

    st.title("🗓️ 步驟 3：行程規劃")
    
    # --- Helper: 安全新增行程 ---
    def safe_add_item(new_item):
        is_dup = any(
            x['Name'] == new_item['Name'] and 
            x['Day'] == new_item['Day'] and 
            x['Start'] == new_item['Start'] 
            for x in st.session_state.itinerary
        )
        if is_dup:
            st.toast(f"⚠️ 行程 '{new_item['Name']}' 已存在", icon="⚠️")
        else:
            st.session_state.itinerary.append(new_item)
            save_current_state()
            st.toast(f"✅ 已新增：{new_item['Name']}", icon="🎉")

    # --- Callbacks ---
    def move_item_callback(item_idx, new_day):
        if 0 <= item_idx < len(st.session_state.itinerary):
            st.session_state.itinerary[item_idx]['Day'] = new_day
            save_current_state()

    def delete_item_callback(item_idx):
        if 0 <= item_idx < len(st.session_state.itinerary):
            st.session_state.itinerary.pop(item_idx)
            save_current_state()

    # === Split Layout ===
    col_source, col_planner = st.columns([0.4, 0.6], gap="medium")
    
    # === 左側：來源區 ===
    with col_source:
        st.subheader("🎯 景點來源")
        # [Mod] Rename & Add Candidate Tab
        tab_ai, tab_filter, tab_night, tab_custom, tab_fav = st.tabs(["🤖 AI推薦", "🔍 自行選擇", "🌙 夜市專區", "✏️ 手動加入", "❤️ 候選清單"])
        
        # Helper for google maps link
        def gmaps_link(lat, lon, name):
            if lat and lon: query = f"{lat},{lon}"
            else: query = name
            return f"https://www.google.com/maps/search/?api=1&query={query}"
        
        # Prepare Day Options
        day_options = [f"Day {i}" for i in range(1, st.session_state.trip_info['days'] + 1)]

        # [Tab 1] AI 推薦 (Compact)
        with tab_ai:
            if st.session_state.recommendations is not None:
                df_rec = st.session_state.recommendations.copy()
                # Safeguard for stale session state
                if 'district' not in df_rec.columns:
                    df_rec['district'] = "未分類"
                    
                districts = df_rec['district'].unique()
                for dist in districts:
                    dist_items = df_rec[df_rec['district'] == dist]
                    with st.expander(f"📍 {dist} ({len(dist_items)})", expanded=False):
                        for _, row in dist_items.iterrows():
                            with st.container(border=True):
                                c_img, c_info = st.columns([1, 2])
                                with c_img:
                                    if row['image_url']: st.image(row['image_url'], use_container_width=True)
                                    else: st.markdown("📷 無圖")
                                with c_info:
                                    # [Refine] Header Layout: Name (Left) | Heart (Right)
                                    h1, h2 = st.columns([4, 1])
                                    with h1:
                                        st.markdown(f"**{row['name']}**")
                                        st.caption(f"❤️ {int(row['similarity']*100)}% | {', '.join(row.get('mapped_tags',[])[:2])}")
                                    with h2:
                                        if st.button("❤️", key=f"fav_ai_{row['id']}", help="加入候選"):
                                            if row['name'] not in [x['Name'] for x in st.session_state.candidates]:
                                                st.session_state.candidates.append({
                                                    "Name": row['name'], "Note": "AI推薦", "Cost": 0,
                                                    "latitude": row.get('latitude'), "longitude": row.get('longitude'),
                                                    "image_url": row['image_url']
                                                })
                                                # [Fix] Save state to persist candidates
                                                save_current_state()
                                                st.toast(f"已加入候選：{row['name']}")
                                    
                                    # Controls Row: Day | Time | Map | Add
                                    ac1, ac2, ac3, ac4 = st.columns([1.5, 1.2, 0.6, 0.8], vertical_alignment="bottom")
                                    
                                    sel_day_str = ac1.selectbox("加入天數", day_options, key=f"ai_d_{row['id']}", label_visibility="visible")
                                    add_time = ac2.time_input("開始時間", value=datetime.time(10, 0), key=f"ai_t_{row['id']}", label_visibility="visible", step=60)
                                    
                                    # Map Button (Updates internal map)
                                    if ac3.button("📍", key=f"loc_ai_{row['id']}", help="在地圖上顯示"):
                                        st.session_state.map_center = [row.get('latitude', 22.62), row.get('longitude', 120.30)]
                                        st.session_state.focus_spot = {"name": row['name'], "lat": row.get('latitude'), "lon": row.get('longitude')}
                                        # st.rerun() # Rerun might happen auto or we can force it
                                        
                                    # Add
                                    if ac4.button("➕", key=f"ai_btn_{row['id']}", use_container_width=True):
                                        # Extract Day Number
                                        add_day = int(sel_day_str.split(" ")[1])
                                        safe_add_item({
                                            "Name": row['name'], "Day": add_day, "Start": str(add_time)[:5],
                                            "End": str((datetime.datetime.combine(datetime.date.today(), add_time) + datetime.timedelta(minutes=60)).time())[:5],
                                            "Cost": 0, "Note": f"AI推薦 - {dist}",
                                            "latitude": row.get('latitude', 0.0), "longitude": row.get('longitude', 0.0)
                                        })
                                        st.rerun()

        # [Tab 2] 自選 (Compact)
        with tab_filter:
            full_df = load_data()
            all_districts = sorted(full_df['district'].unique().tolist())
            all_categories = list(TAG_MAPPING.keys())
            
            with st.expander("篩選條件", expanded=True):
                sel_districts = st.multiselect("📍 行政區", all_districts)
                sel_categories = st.multiselect("🏷️ 類型", all_categories)
                keyword = st.text_input("🔍 搜尋", placeholder="關鍵字...")
            
            filtered_df = full_df.copy()
            if sel_districts: filtered_df = filtered_df[filtered_df['district'].isin(sel_districts)]
            if sel_categories: filtered_df = filtered_df[filtered_df['mapped_tags'].apply(lambda tags: any(cat in tags for cat in sel_categories))]
            if keyword: filtered_df = filtered_df[filtered_df['name'].str.contains(keyword, na=False)]
            
            if filtered_df.empty: st.info("無結果")
            else:
                st.caption(f"找到 {len(filtered_df)} 筆")
                if len(filtered_df) > 15:
                    st.warning("僅顯示前 15 筆")
                    filtered_df = filtered_df.head(15)
                
                for _, row in filtered_df.iterrows():
                    with st.container(border=True):
                        c_img, c_info = st.columns([1, 2])
                        with c_img:
                            if row['image_url']: st.image(row['image_url'], use_container_width=True)
                        with c_info:
                            # Header
                            h1, h2 = st.columns([4, 1])
                            with h1:
                                st.markdown(f"**{row['name']}**")
                                st.caption(f"{row['district']}")
                            with h2:
                                if st.button("❤️", key=f"fav_sf_{row['id']}", help="加入候選"):
                                    if row['name'] not in [x['Name'] for x in st.session_state.candidates]:
                                        st.session_state.candidates.append({
                                            "Name": row['name'], "Note": "自選", "Cost": 0,
                                            "latitude": row.get('latitude'), "longitude": row.get('longitude'),
                                            "image_url": row['image_url']
                                        })
                                        save_current_state()
                                        st.toast(f"已加入候選：{row['name']}")

                            # Controls
                            ac1, ac2, ac3, ac4 = st.columns([1.5, 1.2, 0.6, 0.8], vertical_alignment="bottom")
                            sel_day_str = ac1.selectbox("加入天數", day_options, key=f"sf_d_{row['id']}")
                            sel_time = ac2.time_input("預計時間", value=datetime.time(14, 0), key=f"sf_t_{row['id']}", step=60)
                            
                            if ac3.button("📍", key=f"loc_sf_{row['id']}", help="在地圖上顯示"):
                                st.session_state.map_center = [row.get('latitude', 22.62), row.get('longitude', 120.30)]
                                st.session_state.focus_spot = {"name": row['name'], "lat": row.get('latitude'), "lon": row.get('longitude')}
                                
                            add_day = int(sel_day_str.split(" ")[1])

                            if ac4.button("➕", key=f"sf_btn_{row['id']}", type="secondary", use_container_width=True):
                                safe_add_item({
                                    "Name": row['name'], "Day": add_day, "Start": str(sel_time)[:5],
                                    "End": str((datetime.datetime.combine(datetime.date.today(), sel_time) + datetime.timedelta(minutes=60)).time())[:5],
                                    "Cost": 0, "Note": f"自選 - {row['district']}",
                                    "latitude": row.get('latitude', 0.0), "longitude": row.get('longitude', 0.0)
                                })
                                st.rerun()

        # [Tab 3] 夜市
        with tab_night:
            df_night = load_night_markets()
            
            # Night Market Filter
            nm_days_map = {"ㄧ": "0", "二": "1", "三": "2", "四": "3", "五": "4", "六": "5", "日": "6"}
            nm_days_list = ["全部", "週一", "週二", "週三", "週四", "週五", "週六", "週日"]
            
            # Default to Today
            today_weekday = datetime.datetime.today().weekday()
            default_ix = today_weekday + 1 # +1 because 0 is "全部"
            
            sel_nm_filter = st.selectbox("📅 營業日篩選", nm_days_list, index=default_ix)
            
            if sel_nm_filter != "全部":
                target_d = str(nm_days_list.index(sel_nm_filter) - 1) # Map back to 0-6
                # Filter logic: check if target_d is in row['days'] column (which is string like "1,3,5")
                # Note: CSV data for days column looks like "1,3,5" or "0,1,2..."
                df_night = df_night[df_night['days'].astype(str).apply(lambda x: target_d in x)]
            
            # [Mod] Format days logic
            def format_days(d_str):
                # 0->日, 1->ㄧ... but CSV assumes 0=Mon or 0=Sun? user said "0123456" is confusing.
                # Assuming standard python weekday 0=Mon, 6=Sun.
                # If "0123456" means Sun-Sat? Let's assume input data 0=Mon for now or check usage.
                # User said "change to Sun Mon...".
                # Standard convention: 0123456 -> usually Mon..Sun or Sun..Sat.
                # Let's map 0->一, 1->二 ... 6->日 if using python default.
                # If originally 0=Sun, 1=Mon...
                # Let's just do a char replacement: 0:一, 1:二... or use a map.
                # Given user request "0123456 -> 日一二三四五六", implies 0=日.
                mapping = {"0":"日", "1":"一", "2":"二", "3":"三", "4":"四", "5":"五", "6":"六"}
                res = ""
                for char in str(d_str):
                    if char in mapping: res += mapping[char] + " "
                    elif char in ", ": pass
                    else: res += char
                return res
            
            if df_night.empty: st.info("無營業夜市")
            
            for _, row in df_night.iterrows():
                with st.container(border=True):
                    c1, c2 = st.columns([1, 2])
                    with c1:
                        if row['image_url']: st.image(row['image_url'], use_container_width=True)
                    with c2:
                        h1, h2 = st.columns([4, 1])
                        with h1:
                            st.markdown(f"**{row['name']}**")
                            st.caption(f"營業：{format_days(row['days'])}") # Display formatted string
                        with h2:
                            if st.button("❤️", key=f"fav_nm_{row['name']}", help="加入候選"):
                                 if row['name'] not in [x['Name'] for x in st.session_state.candidates]:
                                    st.session_state.candidates.append({
                                        "Name": row['name'], "Note": "夜市", "Cost": 300,
                                        "latitude": row.get('latitude'), "longitude": row.get('longitude'),
                                        "image_url": row['image_url']
                                    })
                                    save_current_state()
                                    st.toast(f"已加入候選：{row['name']}")

                        ac1, ac2, ac3, ac4 = st.columns([1.5, 1.2, 0.6, 0.8], vertical_alignment="bottom")
                        nm_day_str = ac1.selectbox("加入天數", day_options, key=f"nm_d_{row['name']}")
                        n_time = ac2.time_input("預計時間", value=datetime.time(18, 0), key=f"nm_{row['name']}", step=60)
                        
                        if ac3.button("📍", key=f"loc_nm_{row['name']}", help="在地圖上顯示"):
                            st.session_state.map_center = [row.get('latitude', 22.62), row.get('longitude', 120.30)]
                            st.session_state.focus_spot = {"name": row['name'], "lat": row.get('latitude'), "lon": row.get('longitude')}

                        add_day = int(nm_day_str.split(" ")[1])

                        if ac4.button("➕", key=f"add_nm_{row['name']}", use_container_width=True):
                            # [Refine 5] Check if operating day matches selected day
                            # Day 1 is start_date.
                            # We need weekday of (start_date + add_day - 1)
                            start_dt = datetime.datetime.strptime(st.session_state.trip_info['start_date'], "%Y-%m-%d").date()
                            target_date = start_dt + datetime.timedelta(days=add_day - 1)
                            target_weekday = target_date.weekday() # 0=Mon, 6=Sun
                            
                            # Row['days'] usually "0,1,2" (if 0=Mon) or based on previous logic.
                            # We used nm_days_map earlier: {"ㄧ": "0", ... "日": "6"} assuming 0=Mon ?
                            # Actually our nm_days_map assumed mapping to whatever the CSV uses.
                            # Let's assume CSV uses 0=Mon, 6=Sun or whatever matches datetime.weekday().
                            # If row['days'] contains str(target_weekday), it is open.
                            
                            # However, 'days' column might be "1,3,5" or "0123456". 
                            # Let's just check if str(target_weekday) is in row['days'].
                            # But wait, earlier we mapped using nm_days_map.
                            # Let's trust the check: if str(target_weekday) not in row['days']: warning.
                            if str(target_weekday) not in str(row['days']):
                                w_map = {0:"一", 1:"二", 2:"三", 3:"四", 4:"五", 5:"六", 6:"日"}
                                st.toast(f"⚠️ 注意：{row['name']} 星期{w_map.get(target_weekday)} 可能沒開！", icon="⚠️")
                                
                            safe_add_item({
                                "Name": row['name'], "Day": add_day, "Start": str(n_time)[:5],
                                "End": str((datetime.datetime.combine(datetime.date.today(), n_time) + datetime.timedelta(minutes=90)).time())[:5],
                                "Cost": 300, "Note": "夜市",
                                "latitude": row.get('latitude', 0.0), "longitude": row.get('longitude', 0.0)
                            })
                            st.rerun()
                            
        # [Tab 4] 手動 (Restore)
        with tab_custom:
            st.caption("輸入地址自動定位")
            with st.form("add_custom_compact"):
                c_name = st.text_input("名稱")
                c_addr = st.text_input("地址 (定位用)")
                
                c1, c2 = st.columns(2)
                c_day_str = c1.selectbox("Day", day_options)
                c_time = c2.time_input("時間", value=datetime.time(9, 0), step=60)
                
                # Change to text_input for "direct input" feel
                # [Mod] Remove cost input for manual add
                # c_cost_str = st.text_input("預算 (TWD)", value="0")
                
                if st.form_submit_button("➕", type="primary", use_container_width=True):
                    add_day = int(c_day_str.split(" ")[1])
                    try:
                        c_cost = int(c_cost_str)
                    except:
                        c_cost = 0
                        
                    lat, lon = 0.0, 0.0
                    note = "自訂"
                    if c_addr:
                        st.toast(f"🔍 搜尋：{c_addr}")
                        coords = get_coordinates(c_addr)
                        if coords:
                            lat, lon = coords
                            note += f" | {c_addr}"
                            st.toast("📍 定位成功")
                        else: st.toast("⚠️ 定位失敗")
                            
                    safe_add_item({
                        "Name": c_name if c_name else "未命名", "Day": add_day,
                        "Start": str(c_time)[:5],
                        "End": str((datetime.datetime.combine(datetime.date.today(), c_time) + datetime.timedelta(minutes=60)).time())[:5],
                        "Name": c_name if c_name else "未命名", "Day": add_day,
                        "Start": str(c_time)[:5],
                        "End": str((datetime.datetime.combine(datetime.date.today(), c_time) + datetime.timedelta(minutes=60)).time())[:5],
                        "Cost": 0, "Note": note, "latitude": lat, "longitude": lon
                    })
                    st.rerun()

        # [Tab 5] 候選清單
        with tab_fav:
            if not st.session_state.candidates:
                st.info("尚未加入任何候選景點。請在其他頁籤點擊 ❤️ 加入。")
            else:
                for i, cand in enumerate(st.session_state.candidates):
                    with st.container(border=True):
                        c1, c2 = st.columns([1, 2])
                        with c1:
                            if cand.get('image_url'):
                                st.image(cand['image_url'], use_container_width=True)
                            else:
                                st.markdown("📷 無圖")
                        
                        with c2:
                            h1, h2 = st.columns([4, 1])
                            with h1:
                                st.markdown(f"**{cand['Name']}**")
                                st.caption(f"📝 {cand.get('Note', '')}")
                            with h2:
                                if st.button("🗑️", key=f"del_fav_{i}", help="移除"):
                                    st.session_state.candidates.pop(i)
                                    save_current_state()
                                    st.rerun()

                            # Controls
                            ac1, ac2, ac3, ac4 = st.columns([1.5, 1.2, 0.6, 0.8], vertical_alignment="bottom")
                            sel_day_str = ac1.selectbox("加入天數", day_options, key=f"fav_d_{i}")
                            n_time = ac2.time_input("預計時間", value=datetime.time(10, 0), key=f"fav_t_{i}", step=60)
                            
                            if ac3.button("📍", key=f"loc_fav_{i}", help="地圖"):
                                st.session_state.map_center = [cand.get('latitude', 22.62), cand.get('longitude', 120.30)]
                                st.session_state.focus_spot = {"name": cand['Name'], "lat": cand.get('latitude'), "lon": cand.get('longitude')}

                            if ac4.button("➕", key=f"add_fav_{i}", type="secondary", use_container_width=True):
                                add_day = int(sel_day_str.split(" ")[1])
                                safe_add_item({
                                    "Name": cand['Name'], "Day": add_day, "Start": str(n_time)[:5],
                                    "End": str((datetime.datetime.combine(datetime.date.today(), n_time) + datetime.timedelta(minutes=60)).time())[:5],
                                    # Copy cost from candidate (e.g. night market 300, others 0)
                                    "Cost": cand.get('Cost', 0), 
                                    "Note": f"候選 - {cand.get('Note', '')}",
                                    "latitude": cand.get('latitude'), "longitude": cand.get('longitude')
                                })
                                st.toast(f"已從候選加入：{cand['Name']}")
                                st.rerun()

    # === 右側：看板區 ===
    with col_planner:
        st.subheader("📋 行程看板")
        
        # Map Expander (Moved here)
        with st.expander("🗺️ 行程地圖", expanded=False):
            if not st.session_state.itinerary: st.info("尚無行程")
            else:
                m = folium.Map(location=[st.session_state.map_center[0], st.session_state.map_center[1]], zoom_start=12)
                # Simple logic to add markers
                # Simple logic to add markers
                # 1. Existing Itinerary Items (Blue)
                for item in st.session_state.itinerary:
                     # Attempt to use lat/lon if exists, else skip or guess
                     flat, flon = item.get('latitude'), item.get('longitude')
                     if flat and flon:
                         folium.Marker([flat, flon], popup=item['Name'], tooltip=item['Name'], icon=folium.Icon(color="blue", icon="info-sign")).add_to(m)
                
                # 2. Focus Spot (Red)
                if st.session_state.focus_spot:
                    f = st.session_state.focus_spot
                    if f.get('lat') and f.get('lon'):
                        folium.Marker([f['lat'], f['lon']], popup=f['name'], tooltip=f"📍 {f['name']}", icon=folium.Icon(color="red", icon="star")).add_to(m)

                st_folium(m, height=300, use_container_width=True)

        # Kanban
        total_days = st.session_state.trip_info['days']
        if st.toggle("↔️ 啟用水平捲動模式 (當天數多時推薦)", value=True):
            # [Fix] Scoped CSS using a specific marker class
            # We inject a marker div, then use :has() selector to target the sibling HorizontalBlock
            st.markdown("""
                <style>
                /* Scope: Only target HorizontalBlock inside a VerticalBlock that HAS the itinerary-marker */
                div[data-testid="stVerticalBlock"]:has(.itinerary-marker) > div[data-testid="stHorizontalBlock"] {
                    overflow-x: auto !important;
                    flex-wrap: nowrap !important;
                    padding-bottom: 10px;
                }
                div[data-testid="stVerticalBlock"]:has(.itinerary-marker) > div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
                    flex: 0 0 auto !important;
                    min-width: 300px !important;
                }
                </style>
            """, unsafe_allow_html=True)
            
        # Marker for CSS scoping
        st.markdown('<div class="itinerary-marker"></div>', unsafe_allow_html=True)
        day_cols = st.columns(total_days)
        start_dt = datetime.datetime.strptime(st.session_state.trip_info['start_date'], "%Y-%m-%d").date()
        w_map = {0:"一", 1:"二", 2:"三", 3:"四", 4:"五", 5:"六", 6:"日"}
        
        sorted_items = sorted(st.session_state.itinerary, key=lambda x: x.get('Start', '00:00'))
        
        for day_i, col in enumerate(day_cols, 1):
            # Calculate current date
            curr_date = start_dt + datetime.timedelta(days=day_i - 1)
            curr_w = w_map[curr_date.weekday()]
            
            with col:
                st.markdown(f"#### Day {day_i}")
                st.caption(f"{curr_date.strftime('%m/%d')} ({curr_w})")
                day_items = [x for x in sorted_items if x['Day'] == day_i]
                for item in day_items:
                    real_idx = st.session_state.itinerary.index(item)
                    with st.container(border=True):
                        st.markdown(f"**{item['Name']}**")
                        st.caption(f"{item.get('Start')}-{item.get('End')}")
                        if item.get('Cost'): st.markdown(f":green[${item['Cost']}]")
                        
                        # [Refine 1] Wallet button for detailed budget
                        # [Refine 2] Settings button
                        # Use 5 columns for precise control: [Spacer, Btn1, Gap, Btn2, Spacer]
                        # Ratios: [1, 2, 0.5, 2, 1] puts a 0.5 gap in the middle
                        btns = st.columns([1, 2, 0.5, 2, 1]) 
                        with btns[1]:
                             with st.popover("💰", use_container_width=True):
                                 # Budget Wallet UI
                                 ensure_sub_budgets(item)
                                 st.markdown(f"#### {item['Name']} - 費用管理")
                                 
                                 # 1. Add New Item
                                 with st.form(f"add_sub_{real_idx}"):
                                     c_sub1, c_sub2 = st.columns([1, 1.5])
                                     s_cat = c_sub1.selectbox("類別", CATEGORY_OPTIONS, key=f"scat_{real_idx}_{day_i}") 
                                     s_cost = c_sub2.text_input("金額 (TWD)", placeholder="0", key=f"sval_{real_idx}_{day_i}")
                                     s_note = st.text_input("備註", placeholder="例：門票", key=f"snote_{real_idx}")
                                     
                                     if st.form_submit_button("➕ 新增費用"):
                                         # [Mod] Validation: no negative, int check
                                         try: 
                                             cost_v = int(s_cost)
                                             if cost_v < 0: 
                                                 st.error("金額不能為負")
                                                 st.stop()
                                         except: 
                                             st.error("請輸入有效數字")
                                             st.stop()
                                         item['SubBudgets'].append({"Category": s_cat, "Cost": cost_v, "Note": s_note})
                                         item['Cost'] = sum(x['Cost'] for x in item['SubBudgets']) # Update total
                                         save_current_state()
                                         st.rerun()

                                 # 2. List Items (Editable)
                                 st.divider()
                                 if item['SubBudgets']:
                                     for idx, sub in enumerate(item['SubBudgets']):
                                         # Edit Mode
                                         # Layout: [Cat Select] [Cost Input] [Del Button]
                                         # But limited space. Let's show text and enable edit if needed?
                                         # User requested "Enable modification".
                                         
                                         ec1, ec2, ec3 = st.columns([1.2, 1, 0.5])
                                         
                                         # If we make everything editable directly in list:
                                         new_sub_cat = ec1.selectbox("類別", CATEGORY_OPTIONS, index=CATEGORY_OPTIONS.index(sub.get("Category", "其他")), key=f"ecat_{real_idx}_{idx}", label_visibility="collapsed")
                                         new_sub_cost_str = ec2.text_input("金額", value=str(sub.get("Cost", 0)), key=f"ecost_{real_idx}_{idx}", label_visibility="collapsed")
                                         
                                         # Check for changes
                                         try: new_sub_cost = int(new_sub_cost_str)
                                         except: new_sub_cost = sub.get("Cost", 0)
                                         
                                         if new_sub_cat != sub.get("Category") or new_sub_cost != sub.get("Cost"):
                                             sub['Category'] = new_sub_cat
                                             sub['Cost'] = new_sub_cost
                                             item['Cost'] = sum(x['Cost'] for x in item['SubBudgets'])
                                             save_current_state()
                                             
                                             # Trick: To avoid continuous rerun on every keystroke, users usually click away or Enter.
                                             # Streamlit inputs trigger rerun on blur/enter.
                                             # Should be fine.
                                         
                                         if ec3.button("❌", key=f"del_sub_{real_idx}_{idx}"):
                                             item['SubBudgets'].pop(idx)
                                             item['Cost'] = sum(x['Cost'] for x in item['SubBudgets'])
                                             save_current_state()
                                             st.rerun()
                                 else:
                                     st.caption("尚無細項")

                        with btns[3]:
                            with st.popover("⚙️", use_container_width=True):
                                new_start = st.time_input("開始", value=datetime.datetime.strptime(item.get('Start', '10:00'), "%H:%M").time(), key=f"ks_{real_idx}", step=60)
                                new_end = st.time_input("結束", value=datetime.datetime.strptime(item.get('End', '11:00'), "%H:%M").time(), key=f"ke_{real_idx}", step=60)
                                new_note = st.text_input("備註", value=item.get('Note', ''), key=f"kn_{real_idx}")
                                
                                # [Refine 3] Clarity on Move
                                target_day = st.selectbox("移動至...", [f"Day {d}" for d in range(1, total_days+1)], index=day_i-1, key=f"kmv_{real_idx}")
                                target_day_int = int(target_day.split(" ")[1])
                                
                                c1, c2 = st.columns(2)
                                if c1.button("存", key=f"ksv_{real_idx}"):
                                    st.session_state.itinerary[real_idx].update({
                                        'Start': str(new_start)[:5], 'End': str(new_end)[:5],
                                        'Note': new_note, 'Day': target_day_int
                                    })
                                    save_current_state(); st.rerun()
                                if c2.button("刪", key=f"kdel_{real_idx}", type="primary"):
                                    st.session_state.itinerary.pop(real_idx)
                                    save_current_state(); st.rerun()

    st.divider()
    if st.button("完成規劃，查看總覽 ➡️", type="primary", use_container_width=True):
        navigate_to(PAGES[4]); st.rerun()

# --- 4. 總覽與輸出 ---
elif st.session_state.current_page == PAGES[4]:
    st.title("📊 步驟 4：行程總覽與輸出")
    
    if not st.session_state.itinerary:
        st.warning("行程是空的！請先去規劃。")
        if st.button("⬅️ 回去規劃"): navigate_to(PAGES[3]); st.rerun()
    else:
        # 計算統計
        # [Refine] Chart Logic: Use actual SubBudgets data
        # Aggregate logic: Iterate all items -> iterate SubBudgets -> sum by Category.
        # Fallback: if no SubBudgets but has Cost, put in "Other" or item's main category?
        # But our app now enforces SubBudgets for costs basically.
        
        cat_stats = {}
        for item in st.session_state.itinerary:
            if 'SubBudgets' in item and item['SubBudgets']:
                for sub in item['SubBudgets']:
                    c = sub.get('Category', '其他')
                    v = sub.get('Cost', 0)
                    cat_stats[c] = cat_stats.get(c, 0) + v
            else:
                 # Minimal fallback for legacy items
                 c = item.get('Category', '其他')
                 v = item.get('Cost', 0)
                 if v > 0:
                     cat_stats[c] = cat_stats.get(c, 0) + v
                     
        # Create DataFrame for Chart
        chart_data = pd.DataFrame(list(cat_stats.items()), columns=['Category', 'Cost'])
        
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("💰 預算分析")
            start_date = datetime.datetime.strptime(st.session_state.trip_info['start_date'], "%Y-%m-%d").date()
            end_date = start_date + datetime.timedelta(days=st.session_state.trip_info['days'] - 1)
            st.info(f"📅 日期：{start_date} ~ {end_date} (共 {st.session_state.trip_info['days']} 天)")
            
            total_cost = sum(chart_data['Cost'])
            budget = st.session_state.trip_info['budget']
            pre_spent = st.session_state.trip_info.get('pre_spent', 0)
            
            # Donut Chart
            if not chart_data.empty and total_cost > 0:
                base = alt.Chart(chart_data).encode(
                    theta=alt.Theta("Cost", stack=True),
                    color=alt.Color("Category")
                )
                pie = base.mark_arc(outerRadius=120)
                text = base.mark_text(radius=140).encode(
                    text=alt.Text("Cost"), # label only cost to keep simple
                    order=alt.Order("Cost", sort="descending")
                )
                st.altair_chart(pie + text, use_container_width=True)
            else:
                st.caption("尚無花費數據")

        with c2:
            st.subheader("📊 收支概況")
            col_metrics = st.columns(2)
            col_metrics[0].metric("總預算", f"${budget:,}")
            col_metrics[1].metric("已使用 (含前置)", f"${pre_spent + total_cost:,}")
            
            remaining = budget - pre_spent - total_cost
            st.metric("剩餘預算", f"${remaining:,}", delta=f"{remaining:,}", delta_color="normal" if remaining>=0 else "inverse")
            
        if total_cost > 0:
                st.markdown("#### 花費細項")
                st.dataframe(chart_data.sort_values('Cost', ascending=False), use_container_width=True, hide_index=True)
        
        # [Fix] Prepare DataFrame for CSV
        if st.session_state.itinerary:
            # Create a copy to avoid modifying session state in place
            export_data = []
            for item in st.session_state.itinerary:
                # Flat copy
                row = item.copy()
                
                # Format SubBudgets to readable string
                # e.g. [{'Category': '飲食', 'Cost': 100}] -> "飲食: $100"
                subs = row.get('SubBudgets', [])
                if isinstance(subs, list) and subs:
                    # Join meaningful parts
                    desc_list = []
                    for s in subs:
                        c = s.get('Category', '其他')
                        v = s.get('Cost', 0)
                        n = s.get('Note', '')
                        note_str = f"({n})" if n else ""
                        desc_list.append(f"{c}{note_str}: ${v}")
                    row['SubBudgets'] = " | ".join(desc_list)
                else:
                    row['SubBudgets'] = ""
                export_data.append(row)

            final_df = pd.DataFrame(export_data)
            
            # Ensure columns exist even if empty
            cols_to_keep = ['Day', 'Start', 'End', 'Name', 'Note', 'Cost', 'SubBudgets']
            for c in cols_to_keep:
                if c not in final_df.columns: final_df[c] = ""
            final_df = final_df[cols_to_keep] # Reorder
            
            # Rename for display
            final_df.columns = ['天數', '開始時間', '結束時間', '景點名稱', '備註', '總花費', '預算細項']
            
        else:
            final_df = pd.DataFrame(columns=['天數', '開始時間', '結束時間', '景點名稱', '備註', '總花費', '預算細項'])

        st.header("📤 匯出行程")
        with st.container(border=True):
            st.markdown("##### 📋 行程預覽")
            st.dataframe(final_df, use_container_width=True, hide_index=True)
            st.divider()
            
            ec1, ec2 = st.columns(2)
            with ec1:
                st.markdown("##### 表格式 (CSV)")
                st.caption("適合匯入 Excel 進行詳細編輯")
                csv = final_df.to_csv(index=False).encode('utf-8-sig')
                st.download_button("下載 CSV", csv, "trip.csv", "text/csv", use_container_width=True)
                
            with ec2:
                st.markdown("##### 文字檔 (TXT)")
                st.caption("適合直接傳給朋友或列印")
                if st.button("產生 TXT 預覽與下載", use_container_width=True):
                     txt_bytes = create_txt(st.session_state.itinerary, st.session_state.trip_info['name'], st.session_state.trip_info['budget'])
                     st.download_button("✅ 點擊下載 TXT", txt_bytes, "trip.txt", "text/plain", type="primary", use_container_width=True)
    
    st.divider()
    st.subheader("💾 儲存此行程")
    with st.container(border=True):
        sc1, sc2 = st.columns([3, 1], vertical_alignment="bottom")
        save_name = sc1.text_input("設定存檔名稱", value=f"{st.session_state.trip_info['name']} {datetime.date.today()}")
        if sc2.button("儲存到歷史紀錄", type="primary", use_container_width=True):
            if save_name:
                save_to_history(save_name)
            else:
                st.error("請輸入名稱")

    st.divider()

