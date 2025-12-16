import pandas as pd
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import streamlit as st
import os
import requests
import datetime
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

# 定義標籤映射 (將 CSV 雜亂標籤歸類為標準類別)
TAG_MAPPING = {
    "🏯 歷史古蹟": ["古蹟", "歷史", "眷村", "老街", "紀念", "廢墟風", "孔廟", "書院"],
    "🎨 藝文文創": ["藝文", "文創", "美術館", "展覽", "音樂", "閱讀", "設計", "電影", "圖書館", "藝術"],
    "🎡 親子樂園": ["親子", "樂園", "觀光工廠", "體驗", "DIY", "動物", "科普"],
    "⛰️ 山林步道": ["登山", "山", "步道", "古道", "原住民", "溫泉", "蝴蝶", "泥火山", "地質", "森林", "茶園", "生態"],
    "🌊 海港水域": ["海邊", "港", "碼頭", "遊船", "玩水", "湖", "瀑布", "濕地", "濱海", "水母"],
    "🛍️ 逛街美食": ["購物", "商圈", "美食", "夜市", "小吃", "百貨", "海鮮"],
    "📸 網美打卡": ["打卡點", "景觀", "夜景", "地標", "彩繪", "裝置藝術", "建築", "夕陽"],
    "🚂 鐵道交通": ["鐵道", "車站", "火車", "捷運", "輕軌", "飛機"],
    "🙏 宗教巡禮": ["廟宇", "教堂", "教會", "天后宮", "佛光山", "修道院"],
    "🚲 單車漫遊": ["自行車", "單車", "鐵馬"],
    "🛖 原民部落": ["原住民", "部落", "原鄉", "祭典", "石板屋", "琉璃珠", "那瑪夏", "茂林", "桃源"],
    "🏘️ 眷村故事": ["眷村", "軍事", "老屋", "日式", "海軍", "空軍", "陸軍"]
}

@st.cache_data
def load_data():
    """讀取景點資料庫 CSV 檔案"""
    file_path = 'data/data.csv'
    try:
        df = pd.read_csv(file_path, encoding='utf-8')
    except Exception as e:
        st.error(f"無法讀取資料庫，請確認 '{file_path}' 是否存在。錯誤: {e}")
        return pd.DataFrame()

    if 'tags' in df.columns: df['tags'] = df['tags'].fillna('')
    if 'image_url' not in df.columns: df['image_url'] = ""
    if 'latitude' not in df.columns: df['latitude'] = 0.0
    if 'longitude' not in df.columns: df['longitude'] = 0.0
    if 'district' not in df.columns: df['district'] = "未分類"
    else: df['district'] = df['district'].fillna("未分類")

    # 產生 mapped_tags
    def get_mapped_tags(raw_tags):
        mapped = set()
        for tag in str(raw_tags).split(','):
            t = tag.strip()
            for category, keywords in TAG_MAPPING.items():
                if t in keywords or any(k in t for k in keywords):
                    mapped.add(category)
        return list(mapped)
    
    df['mapped_tags'] = df['tags'].apply(get_mapped_tags)
    return df

@st.cache_data
def load_night_markets():
    """讀取夜市資料庫 CSV"""
    # [Fix] Point to the correct data folder
    file_path = os.path.join(os.path.dirname(__file__), "data", "night_markets.csv")
    
    if not os.path.exists(file_path):
        # Fallback to root if data folder version missing (backward compatibility)
        file_path = os.path.join(os.path.dirname(__file__), "night_markets.csv")
        
    if not os.path.exists(file_path):
        return pd.DataFrame()
        
    try:
        df = pd.read_csv(file_path)
        if 'image_url' not in df.columns: df['image_url'] = ""
        df['image_url'] = df['image_url'].fillna("")
        
        # Default Taiwan Night Market Image
        default_img = "https://images.unsplash.com/photo-1528164344705-47542687000d?q=80&w=600&auto=format&fit=crop"
        
        # Apply default to empty strings
        df.loc[df['image_url'].str.strip() == "", 'image_url'] = default_img
        return df
    except Exception as e:
        print(f"Error loading night markets: {e}")
        return pd.DataFrame()

def calculate_recommendations(df, user_prefs, specific_tags=[], days=1):
    """計算推薦景點"""
    if df.empty: return None

    # 1. 計算類別分數 (根據 mapped_tags)
    # 1. 計算類別分數 (根據 mapped_tags)
    def calculate_score(row):
        score = 0
        tags = row['mapped_tags']
        
        # 基礎偏好權重 (5大面向)
        # 1. 自然光譜
        if "⛰️ 山林步道" in tags or "🌊 海港水域" in tags or "🛖 原民部落" in tags: 
            score += user_prefs.get('nature', 0.5)
            
        # 2. 老靈魂 (歷史/宗教)
        if "🏯 歷史古蹟" in tags or "🙏 宗教巡禮" in tags or "🏘️ 眷村故事" in tags or "🛖 原民部落" in tags:
            score += user_prefs.get('history', 0.5)
            
        # 3. 新潮流 (網美/文創)
        if "🎨 藝文文創" in tags or "📸 網美打卡" in tags or "🏘️ 眷村故事" in tags:
            score += user_prefs.get('trend', 0.5)
            
        # 4. 玩樂性質 (親子)
        if "🎡 親子樂園" in tags:
            score += user_prefs.get('fun', 0.5)
            
        # 5. 都市生活 (逛街/美食)
        if "🛍️ 逛街美食" in tags:
            score += user_prefs.get('urban', 0.5)
        
        # 特定標籤加權 (來自使用者選取的 Pill Tags)
        for t in specific_tags:
            if t in tags:
                score += 0.3 # 選中標籤加分
        
        return score

    df['score'] = df.apply(calculate_score, axis=1)
    
    # 正規化分數
    if df['score'].max() > 0:
        df['similarity'] = df['score'] / df['score'].max()
    else:
        df['similarity'] = 0

    # 依照分數排序
    rec_limit = max(10, days * 6) # 動態限制數量
    recommendations = df.sort_values(by='similarity', ascending=False).head(rec_limit)
    return recommendations

def get_static_map_image(itinerary_data, api_key):
    """取得 Google Static Maps 圖片"""
    if not api_key: return None
    base_url = "https://maps.googleapis.com/maps/api/staticmap?"
    markers_str = ""
    # 只取前 15 個點以免 URL 過長
    for item in itinerary_data[:15]: 
        # 注意：這裡假設 itinerary_data 裡面還沒有自動填入 lat/lon，
        # 如果未來有加入，可以直接用。目前是用名稱去猜或忽略。
        pass
        
    # 範例回傳 None (因需要重寫完整座標邏輯)
    return None

def create_txt(itinerary, trip_name, total_budget):
    """
    Generates a text file for the itinerary.
    """
    lines = []
    lines.append(f"=== {trip_name} 行程表 ===")
    lines.append(f"總預算: ${total_budget}")
    
    total_cost = sum(item.get('Cost', 0) for item in itinerary)
    lines.append(f"預估花費: ${total_cost}")
    lines.append(f"剩餘預算: ${total_budget - total_cost}")
    lines.append("-" * 30)
    
    # Group by Day
    days = sorted(list(set(item['Day'] for item in itinerary)))
    
    for day in days:
        lines.append(f"\n[Day {day}]")
        day_items = sorted([i for i in itinerary if i['Day'] == day], key=lambda x: x.get('Start', '00:00'))
        
        for item in day_items:
            start = item.get('Start', '00:00')
            end = item.get('End', '00:00')
            name = item['Name']
            cost = item.get('Cost', 0)
            note = item.get('Note', '')
            
            line = f"{start}-{end} | {name} | ${cost}"
            if note:
                line += f" | 備註: {note}"
            lines.append(line)
            
            # Sub-budgets if any
            if 'SubBudgets' in item and item['SubBudgets']:
                for sub in item['SubBudgets']:
                     lines.append(f"    - {sub['Category']}: ${sub['Cost']} ({sub.get('Note','')})")
    
    lines.append("\n" + "="*30)
    lines.append("Generated by Travel Planner AI")
    
    return "\n".join(lines).encode('utf-8')

@st.cache_data
def get_coordinates(address):
    """
    使用 OpenStreetMap (Nominatim) 將地址轉換為經緯度
    具備自動降級搜尋功能 (完整地址 -> 路名 -> 失敗)
    """
    try:
        geolocator = Nominatim(user_agent="kaohsiung_travel_planner_app_v1")
        
        # Helper to ensure region context
        def format_addr(addr):
            # 強制加上台灣，避免搜尋到中國同名地點
            prefix = ""
            if "台灣" not in addr and "臺灣" not in addr:
                prefix += "台灣"
            if "高雄" not in addr:
                prefix += "高雄市"
            
            return f"{prefix}{addr}" if prefix else addr

        # 1. 嘗試完整地址
        targets = [address]
        
        # 2. 嘗試去除門牌號碼 (簡易正則：去除數字+號)
        import re
        road_only = re.sub(r'\d+號?', '', address).strip()
        if road_only and road_only != address:
            targets.append(road_only)
            
        # 3. 嘗試去除 "高雄市" 等前綴後的關鍵字
        # simple_name = address.replace("高雄市", "").replace("台灣", "")
        # targets.append(simple_name)

        for target in targets:
            full_query = format_addr(target)
            location = geolocator.geocode(full_query, timeout=10)
            if location:
                return location.latitude, location.longitude
                
        return None
    except Exception as e:
        print(f"Geocoding error: {e}")
        return None
