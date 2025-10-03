import streamlit as st
import pandas as pd
import requests
import datetime
from concurrent.futures import ThreadPoolExecutor
import pytz

st.set_page_config(page_title="SHOWROOM イベント参加者戦闘力チェック", layout="wide")
JST = pytz.timezone("Asia/Tokyo")
HEADERS = {"User-Agent": "Mozilla/5.0"}
ARCHIVE_URL = "https://mksoul-pro.com/showroom/file/sr-event-archive.csv"

# --- キャッシュ付きAPI ---
@st.cache_data(ttl=3600)
def get_event_search():
    """最新イベント一覧を取得"""
    url = "https://www.showroom-live.com/api/event/search"
    params = {"statuses": [1, 3, 4], "limit": 200}
    res = requests.get(url, headers=HEADERS, params=params)
    res.raise_for_status()
    return res.json().get("events", [])

@st.cache_data(ttl=3600)
def get_archive_events():
    """バックアップCSV"""
    try:
        df = pd.read_csv(ARCHIVE_URL)
        return df.to_dict("records")
    except:
        return []

@st.cache_data(ttl=3600)
def get_event_room_list(event_id):
    """イベント参加者リスト"""
    url = f"https://www.showroom-live.com/api/event/room_list?event_id={event_id}"
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    data = res.json().get("list", [])
    return data

@st.cache_data(ttl=3600)
def get_room_profile(room_id):
    """ルームプロフィール"""
    url = f"https://www.showroom-live.com/api/room/profile?room_id={room_id}"
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    return res.json()

def get_room_event_history(room_id, events):
    """特定ルームが出場していたイベントを検索"""
    history = []
    for ev in events:
        url_key = ev.get("event_url_key")
        event_id = ev.get("event_id")
        if not url_key or not event_id:
            continue
        found = False
        for page in range(1, 6):
            rank_url = f"https://www.showroom-live.com/api/event/{url_key}/ranking?page={page}"
            try:
                r = requests.get(rank_url, headers=HEADERS, timeout=10)
                if r.status_code != 200:
                    break
                ranking_list = r.json().get("ranking", [])
                for item in ranking_list:
                    if str(item.get("room_id")) == str(room_id):
                        # /room_listで詳細を取得
                        room_list = get_event_room_list(event_id)
                        for rl in room_list:
                            if str(rl.get("room_id")) == str(room_id):
                                history.append({
                                    "event_name": ev.get("event_name"),
                                    "url": f"https://www.showroom-live.com/event/{url_key}",
                                    "start": ev.get("start_at"),
                                    "end": ev.get("end_at"),
                                    "rank": rl.get("rank", "圏外"),
                                    "point": rl.get("point", 0),
                                    "quest_level": rl.get("quest_level", "-")
                                })
                                found = True
                                break
                    if found:
                        break
            except Exception:
                continue
    return history

# --- UI ---
st.title("🎯 SHOWROOM イベント参加者戦闘力チェック")

# イベント一覧取得
with st.spinner("イベント一覧を取得中..."):
    latest = get_event_search()
    archive = get_archive_events()

# 最新 + バックアップ統合
event_df = pd.DataFrame(latest)
if archive:
    archive_df = pd.DataFrame(archive)
    event_df = pd.concat([event_df, archive_df], ignore_index=True).drop_duplicates(subset=["event_id"], keep="first")

event_df = event_df[event_df["start_at"] >= "2023-09-01"]
event_df = event_df.sort_values("start_at", ascending=False)

selected_event = st.selectbox(
    "イベントを選択してください：",
    options=[f"{row['event_name']}（{row['start_at']}〜{row['end_at']}）" for _, row in event_df.iterrows()]
)

if selected_event:
    event_row = event_df.iloc[[i for i, x in enumerate(
        [f"{r['event_name']}（{r['start_at']}〜{r['end_at']}）" for _, r in event_df.iterrows()]
    ) if x == selected_event][0]]

    event_id = int(event_row["event_id"])
    event_url_key = event_row["event_url_key"]

    st.subheader(f"イベント参加者一覧（Event ID: {event_id}）")

    room_list = get_event_room_list(event_id)
    st.write(f"参加ルーム数：{len(room_list)}")

    if room_list:
        top_rooms = sorted(room_list, key=lambda x: (-int(x.get("show_rank_subdivided", 0)),
                                                     -int(x.get("room_level", 0)),
                                                     -int(x.get("follower_num", 0))))[:10]
        for room in top_rooms:
            room_id = room["room_id"]
            prof = get_room_profile(room_id)
            link = f"https://www.showroom-live.com/room/profile?room_id={room_id}"
            st.markdown(f"#### [{prof.get('room_name')}]({link})")
            st.write(f"SHOWランク: {prof.get('show_rank_subdivided')}, ルームLv: {prof.get('room_level')}, フォロワー: {prof.get('follower_num')}")
            st.write("過去イベント履歴を検索中...")
            history = get_room_event_history(room_id, event_df.to_dict("records"))
            if history:
                hist_df = pd.DataFrame(history)
                st.dataframe(hist_df, hide_index=True)
            else:
                st.info("過去イベント履歴が見つかりません。")
