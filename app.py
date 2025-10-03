import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta

st.set_page_config(page_title="SHOWROOM イベント参加者戦闘力チェック", layout="wide")

# --- 定数定義 ---
EVENT_SEARCH_API = "https://www.showroom-live.com/api/event/search"
EVENT_BACKUP_URL = "https://mksoul-pro.com/showroom/file/sr-event-archive.csv"

# --- JST変換関数 ---
def ts_to_jst(ts):
    try:
        return datetime.fromtimestamp(int(ts), timezone(timedelta(hours=9)))
    except Exception:
        return None

# --- イベントデータ取得関数 ---
@st.cache_data(ttl=300)
def fetch_event_data():
    all_events = []

    # ① APIから取得
    try:
        res = requests.get(EVENT_SEARCH_API, timeout=10)
        if res.status_code == 200:
            api_data = res.json()
            if isinstance(api_data, dict) and "events" in api_data:
                all_events.extend(api_data["events"])
    except Exception as e:
        st.warning(f"イベントAPI取得に失敗しました: {e}")

    # ② バックアップCSVも統合
    try:
        backup_df = pd.read_csv(EVENT_BACKUP_URL)
        backup_df = backup_df.rename(columns=str.lower)
        all_events.extend(backup_df.to_dict(orient="records"))
    except Exception as e:
        st.warning(f"バックアップ読み込みに失敗しました: {e}")

    return pd.DataFrame(all_events)

# --- メイン処理 ---
st.title("🎯 SHOWROOM イベント参加者戦闘力チェック")

event_df = fetch_event_data()

if event_df.empty:
    st.error("イベント情報を取得できませんでした。")
    st.stop()

# --- UnixタイムをJST変換 ---
if "started_at" in event_df.columns:
    event_df["start_dt"] = event_df["started_at"].apply(ts_to_jst)
if "ended_at" in event_df.columns:
    event_df["end_dt"] = event_df["ended_at"].apply(ts_to_jst)

# --- 日付フィルタ（2023/09/01以降） ---
cutoff = datetime(2023, 9, 1, tzinfo=timezone(timedelta(hours=9)))
if "start_dt" in event_df.columns:
    event_df = event_df[event_df["start_dt"] >= cutoff]

# --- 表示整形 ---
event_df = event_df.sort_values("start_dt", ascending=False)
event_df["event_link"] = event_df.apply(lambda x: f"[🔗 {x.get('event_name', '不明')}]('https://www.showroom-live.com/event/{x.get('event_url_key', '')}')", axis=1)
event_df["期間"] = event_df.apply(lambda x: f"{x['start_dt'].strftime('%Y/%m/%d %H:%M')}〜{x['end_dt'].strftime('%Y/%m/%d %H:%M')}" if pd.notnull(x.get('start_dt')) and pd.notnull(x.get('end_dt')) else "-", axis=1)

# --- 一覧表示 ---
columns_to_show = ["event_link", "event_block_label", "期間", "event_id"]
st.dataframe(event_df[columns_to_show].rename(columns={
    "event_link": "イベント名",
    "event_block_label": "対象",
    "event_id": "ID"
}), use_container_width=True)

# --- イベント選択 ---
event_options = event_df[["event_name", "event_id", "event_url_key"]].dropna()
selected_event = st.selectbox("分析するイベントを選択", options=event_options["event_name"].tolist())

if selected_event:
    selected_row = event_options[event_options["event_name"] == selected_event].iloc[0]
    event_id = selected_row["event_id"]
    event_url_key = selected_row["event_url_key"]

    st.info(f"選択中イベント: {selected_event}\nURL: https://www.showroom-live.com/event/{event_url_key}")

    # --- 参加ルームリストを取得 ---
    room_api = f"https://www.showroom-live.com/api/event/room_list?event_id={event_id}"
    try:
        res = requests.get(room_api, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict) and "list" in data:
                rooms = data["list"]

                # --- 最大10ルームを選出（SHOWランク > レベル > フォロワー数） ---
                df_rooms = pd.DataFrame(rooms)
                df_rooms = df_rooms.sort_values(by=["show_rank_subdivided", "room_level", "follower_num"], ascending=[False, False, False]).head(10)

                df_rooms["room_link"] = df_rooms.apply(lambda x: f"[🔗 {x.get('room_name', '不明')}]('https://www.showroom-live.com/room/profile?room_id={x.get('room_id', '')}')", axis=1)

                st.subheader("🏠 参加ルーム情報（上位10ルーム）")
                st.dataframe(df_rooms[["room_link", "room_level", "show_rank_subdivided", "follower_num", "live_continuous_days", "room_id"]].rename(columns={
                    "room_link": "ルーム名",
                    "room_level": "ルームレベル",
                    "show_rank_subdivided": "SHOWランク",
                    "follower_num": "フォロワー数",
                    "live_continuous_days": "毎日配信継続日数",
                    "room_id": "ルームID"
                }), use_container_width=True)

            else:
                st.warning("ルームリストデータが見つかりませんでした。")
        else:
            st.error(f"ルームリストの取得に失敗しました（status={res.status_code}）。")
    except Exception as e:
        st.error(f"ルームリスト取得でエラーが発生しました: {e}")
