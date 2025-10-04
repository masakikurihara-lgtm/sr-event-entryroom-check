import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed

# ページ設定
st.set_page_config(page_title="SHOWROOM イベント参加者戦闘力チェック", layout="wide")

JST = pytz.timezone("Asia/Tokyo")

# --- 共通関数群 ---------------------------------------------------

def ts_to_jst(ts):
    """UNIXタイムを日本時間のYYYY-MM-DD HH:MM形式に変換"""
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts), JST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"

def safe_get(url):
    """requests.get の安全ラッパ"""
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def get_event_rooms_count(event_id):
    """イベントの参加ルーム数を取得"""
    url = f"https://www.showroom-live.com/api/event/room_list?event_id={event_id}"
    data = safe_get(url)
    if isinstance(data, dict) and "list" in data:
        return len(data["list"])
    return 0

def fetch_events_by_status(status):
    """指定ステータスのイベントを取得"""
    url = f"https://www.showroom-live.com/api/event/search?status={status}"
    data = safe_get(url)
    if not data or "events" not in data:
        return []
    return data["events"]

# --- メイン処理 ---------------------------------------------------

st.markdown("<h1 style='text-align:center;'>🎯 SHOWROOM イベント参加者戦闘力チェック</h1>", unsafe_allow_html=True)
st.caption("※対象：開催前および開催中イベント（status=1,3）")

with st.spinner("イベント情報を取得中..."):
    # status=1（開催前）と3（開催中）を並列取得
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(fetch_events_by_status, s): s for s in [1, 3]}
        results = []
        for f in as_completed(futures):
            results.extend(f.result())
    
    all_events = results

# --- データフレーム整形 ---
if not all_events:
    st.warning("イベント情報を取得できませんでした。")
    st.stop()

df = pd.DataFrame(all_events)

# 必要な列を抽出
df = df[[
    "event_id",
    "event_url_key",
    "event_name",
    "event_block_label",
    "started_at",
    "ended_at",
    "status"
]]

# 日付変換
df["開催開始"] = df["started_at"].apply(ts_to_jst)
df["開催終了"] = df["ended_at"].apply(ts_to_jst)
df["ステータス"] = df["status"].apply(lambda x: "開催中" if x == 3 else "開催前")

# 参加ルーム数の取得（並列処理）
st.info("参加ルーム数を取得しています...")
room_counts = {}
with ThreadPoolExecutor(max_workers=10) as executor:
    futures = {executor.submit(get_event_rooms_count, eid): eid for eid in df["event_id"]}
    for future in as_completed(futures):
        eid = futures[future]
        try:
            room_counts[eid] = future.result()
        except Exception:
            room_counts[eid] = None
df["参加ルーム数"] = df["event_id"].map(room_counts)

# イベントURLリンク
df["イベント名"] = df.apply(
    lambda r: f"<a href='https://www.showroom-live.com/event/{r['event_url_key']}' target='_blank'>{r['event_name']}</a>", axis=1
)

# 並び順: 開催中→開催前、開始日時の新しい順
df["status_order"] = df["ステータス"].map({"開催中": 0, "開催前": 1})
df = df.sort_values(by=["status_order", "started_at"], ascending=[True, False])

# 表示列
display_cols = ["イベント名", "event_block_label", "開催開始", "開催終了", "参加ルーム数", "ステータス"]
df_display = df[display_cols].rename(columns={
    "event_block_label": "対象",
})

# --- 表示 ---
st.markdown("### 📋 イベント一覧（開催前＋開催中）")
st.markdown(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)
