# sr_event_participants_check.py
import streamlit as st
import requests
import pandas as pd
import io
import re
from datetime import datetime, date, timedelta
import pytz
import time

# --- 設定 / 定数 ---
JST = pytz.timezone("Asia/Tokyo")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; EventParticipantsChecker/1.0; +https://example.com)"
}

API_EVENT_SEARCH_URL = "https://www.showroom-live.com/api/event/search"
API_EVENT_ROOM_LIST_BASE = "https://www.showroom-live.com/api/event/room_list"  # ?event_id=xxx&page=1
API_ROOM_PROFILE = "https://www.showroom-live.com/api/room/profile?room_id={room_id}"
BACKUP_EVENTS_CSV = "https://mksoul-pro.com/showroom/file/sr-event-archive.csv"  # backup
# cut-off date: only include events started_at >= 2023-09-01
CUTOFF_DATE = datetime(2023, 9, 1, tzinfo=JST)

# --- ヘルパー: event_id 正規化（既存ツールと同一ロジック） ---
def normalize_event_id_val(val):
    if val is None:
        return None
    try:
        if isinstance(val, (int,)):
            return str(val)
        if isinstance(val, float):
            if val.is_integer():
                return str(int(val))
            return str(val).strip()
        s = str(val).strip()
        if re.match(r'^\d+(\.0+)?$', s):
            return str(int(float(s)))
        if s == "":
            return None
        return s
    except Exception:
        try:
            return str(val).strip()
        except Exception:
            return None

# --- イベント取得（API: statuses = [1,3,4]） ---
@st.cache_data(ttl=600)
def get_events_from_api(statuses=(1,3,4)):
    all_events = []
    for status in statuses:
        page = 1
        while True:
            params = {"status": status, "page": page}
            try:
                r = requests.get(API_EVENT_SEARCH_URL, headers=HEADERS, params=params, timeout=10)
                r.raise_for_status()
                d = r.json()
                page_events = d.get('events') or d.get('event_list') or []
                if not page_events:
                    break
                all_events.extend(page_events)
                page += 1
                time.sleep(0.08)
            except requests.exceptions.RequestException:
                # 失敗時はそのステータスを飛ばす
                break
            except ValueError:
                break
            if page > 50:  # safety cap
                break
    return all_events

# --- バックアップCSV読み込み ---
@st.cache_data(ttl=600)
def get_events_from_backup():
    try:
        r = requests.get(BACKUP_EVENTS_CSV, headers=HEADERS, timeout=10)
        r.raise_for_status()
        text = r.content.decode('utf-8-sig')
        df = pd.read_csv(io.StringIO(text), dtype=str)
        # ensure expected columns exist, but we will handle missing fields defensively
        records = df.to_dict('records')
        # convert keys to expected names similar to API where possible
        # backup presumably contains "event_id","event_name","started_at","ended_at","event_url_key","image_m","is_event_block","is_entry_scope_inner","show_ranking"
        return records
    except Exception:
        return []

# --- マージ：API優先、バックアップを併用してユニーク化 ---
@st.cache_data(ttl=600)
def build_combined_event_list():
    api_events = get_events_from_api()
    backup_events = get_events_from_backup()

    # Normalize event_id and convert to dict by id
    combined = {}
    # Add API events first (take precedence)
    for e in api_events:
        eid = normalize_event_id_val(e.get('event_id') or e.get('id') or e.get('event_id'))
        if eid is None:
            continue
        e['event_id'] = eid
        combined[eid] = e

    # Add backup events if id not present
    for e in backup_events:
        eid = normalize_event_id_val(e.get('event_id') or e.get('id'))
        if eid is None:
            continue
        if eid not in combined:
            combined[eid] = e

    # Convert to list and filter start date >= cutoff
    result = list(combined.values())

    # normalize started_at/ended_at to ints if possible
    filtered = []
    for e in result:
        # try multiple field names
        sa = e.get('started_at') or e.get('start_at') or e.get('startedAt') or e.get('start') or None
        ea = e.get('ended_at') or e.get('end_at') or e.get('endedAt') or e.get('end') or None
        try:
            sa_int = int(float(sa)) if sa is not None else None
        except Exception:
            sa_int = None
        try:
            ea_int = int(float(ea)) if ea is not None else None
        except Exception:
            ea_int = None
        # attach normalized numeric timestamps
        e['_started_at'] = sa_int
        e['_ended_at'] = ea_int
        # include only events with started_at >= cutoff
        if sa_int is None:
            continue
        dt = datetime.fromtimestamp(sa_int, JST)
        if dt >= CUTOFF_DATE:
            filtered.append(e)
    # sort by started_at desc (recent first)
    filtered.sort(key=lambda x: x.get('_started_at') or 0, reverse=True)
    return filtered

# --- 参加ルーム数取得 (room_list?event_id=) ---
@st.cache_data(ttl=300)
def get_total_entries_for_event(event_id):
    try:
        params = {"event_id": event_id, "page": 1}
        r = requests.get(API_EVENT_ROOM_LIST_BASE, headers=HEADERS, params=params, timeout=8)
        if r.status_code == 404:
            return 0
        r.raise_for_status()
        d = r.json()
        # API may return 'total_entries' or 'total' or provide 'list' length; try best-effort
        if isinstance(d, dict):
            if 'total_entries' in d:
                return int(d.get('total_entries') or 0)
            if 'total' in d:
                return int(d.get('total') or 0)
            if 'list' in d and isinstance(d['list'], list):
                # API may not include total; fallback to list len for page 1
                return len(d['list'])
        if isinstance(d, list):
            return len(d)
    except Exception:
        return "N/A"
    return "N/A"

# --- イベント参加ルーム取得 (room_list API) ---
def fetch_event_room_list(event_id):
    rooms = []
    page = 1
    max_pages = 3  # usually room_list only returns up to 30 results; keep small
    while page <= max_pages:
        try:
            params = {"event_id": event_id, "page": page}
            r = requests.get(API_EVENT_ROOM_LIST_BASE, headers=HEADERS, params=params, timeout=8)
            if r.status_code == 404:
                break
            r.raise_for_status()
            d = r.json()
            if isinstance(d, dict):
                arr = d.get('list') or d.get('data') or d.get('event_list') or d.get('ranking')
            elif isinstance(d, list):
                arr = d
            else:
                arr = None
            if not arr:
                break
            rooms.extend(arr)
            # if list length smaller than page size probably last page
            if isinstance(arr, list) and len(arr) < 30:
                break
            page += 1
        except Exception:
            break
    return rooms

# --- ルームプロフィールを取得して必要項目を取り出す ---
def fetch_room_profile(room_id):
    try:
        r = requests.get(API_ROOM_PROFILE.format(room_id=room_id), headers=HEADERS, timeout=6)
        r.raise_for_status()
        d = r.json()
        # possible keys: room_name, room_level, show_rank_subdivided, follower_num, live_continuous_days, room_id
        room_name = d.get('room_name') or d.get('name') or d.get('performer_name') or ""
        room_level = d.get('room_level') or d.get('level') or d.get('lv') or None
        # show rank: several shapes possible
        show_rank = d.get('show_rank_subdivided') or d.get('show_rank') or d.get('show_rank_sub') or d.get('show_rank_name') or None
        follower = d.get('follower_num') or d.get('follower_count') or d.get('followers') or None
        live_continuous = d.get('live_continuous_days') or d.get('live_continuous') or None
        return {
            'room_name': room_name,
            'room_level': int(room_level) if room_level is not None else None,
            'show_rank': show_rank,
            'follower_num': int(follower) if follower is not None else None,
            'live_continuous_days': int(live_continuous) if live_continuous is not None else None,
            'room_id': str(room_id)
        }
    except Exception:
        # return partial structure on failure
        return {
            'room_name': f"room_{room_id}",
            'room_level': None,
            'show_rank': None,
            'follower_num': None,
            'live_continuous_days': None,
            'room_id': str(room_id)
        }

# --- UI ---
st.set_page_config(page_title="SHOWROOM イベント参加者チェック", layout="wide")
st.title("🎯 SHOWROOM イベント参加者 戦闘力チェック（フェーズ1）")

st.info("イベントは API とバックアップCSV を併用して取得します。一覧からイベントを選んで『参加者を取得』してください。")

# fetch combined events
with st.spinner("イベント一覧を取得中..."):
    events = build_combined_event_list()

if not events:
    st.warning("イベントが取得できませんでした。APIまたはバックアップにアクセスできるか確認してください。")
    st.stop()

# Build display table for events
display_rows = []
for e in events:
    eid = e.get('event_id') or e.get('eventId') or None
    title = e.get('event_name') or e.get('event_name_jp') or e.get('name') or e.get('eventTitle') or "(no title)"
    is_entry_inner = e.get('is_entry_scope_inner') or e.get('is_entry_scope_inner') or e.get('is_entry_scope_inner') or False
    target = "対象者限定" if str(is_entry_inner).lower() in ['true','1','yes'] else "全ライバー"
    started_ts = e.get('_started_at')
    ended_ts = e.get('_ended_at')
    started_str = datetime.fromtimestamp(started_ts, JST).strftime('%Y/%m/%d %H:%M') if started_ts else ""
    ended_str = datetime.fromtimestamp(ended_ts, JST).strftime('%Y/%m/%d %H:%M') if ended_ts else ""
    participant_count = get_total_entries_for_event(eid) if eid is not None else "N/A"
    event_url_key = e.get('event_url_key') or e.get('event_url') or ""
    display_rows.append({
        'event_id': eid,
        'event_name': title,
        'event_url_key': event_url_key,
        'target': target,
        'started_at': started_str,
        'ended_at': ended_str,
        'participants': participant_count
    })

events_df = pd.DataFrame(display_rows)
# Show as table (sortable)
st.subheader("イベント一覧（開始日 >= 2023-09-01）")
# Provide selection by event name (show "event_name (event_id)" in selectbox)
events_df_display = events_df.copy()
events_df_display['link'] = events_df_display.apply(lambda r: f"{r['event_name']}  (id:{r['event_id']})", axis=1)
st.dataframe(events_df_display[['link','target','started_at','ended_at','participants']].rename(columns={'link':'イベント'}), use_container_width=True)

# selection widget
selected = st.selectbox("解析対象イベントを選択してください:", options=events_df_display['event_id'].astype(str).tolist(), format_func=lambda x: events_df_display[events_df_display['event_id']==x]['event_name'].values[0] if x in events_df_display['event_id'].astype(str).tolist() else x)

if st.button("参加者を取得して表示"):
    if not selected:
        st.error("イベントを選択してください。")
    else:
        st.info("参加ルームを取得しています（room_list API）...")
        event_row = next((e for e in events if normalize_event_id_val(e.get('event_id')) == normalize_event_id_val(selected)), None)
        event_id_for_api = selected
        # fetch room list via room_list API
        rooms_raw = fetch_event_room_list(event_id_for_api)
        if not rooms_raw:
            st.warning("参加ルーム情報が取得できませんでした（room_list が空）。")
        else:
            # rooms_raw is list of dicts; try to extract room_id from variety of shapes
            room_entries = []
            for r in rooms_raw:
                if not isinstance(r, dict):
                    continue
                # candidate keys
                rid = r.get('room_id') or r.get('id') or None
                # sometimes nested under 'room'
                if rid is None and 'room' in r and isinstance(r['room'], dict):
                    rid = r['room'].get('room_id') or r['room'].get('id')
                if rid is None:
                    continue
                room_entries.append(str(rid))
            # dedupe order-preserving
            seen = set()
            room_entries = [x for x in room_entries if not (x in seen or seen.add(x))]

            # For each room_id fetch profile (but limit calls for performance)
            st.info(f"{len(room_entries)} ルームが見つかりました。プロフィールを取得します（上限30件）。")
            profiles = []
            for rid in room_entries[:50]:  # fetch up to 50 safety, but will pick top10 later
                prof = fetch_room_profile(rid)
                profiles.append(prof)
                time.sleep(0.06)  # slight throttle

            # sorting priority: SHOWランク (higher better) > room_level (higher) > follower_num (higher)
            # Show-rank is not numeric; define a mapping order if possible. We'll try to parse rank name like "S4","A1", etc.
            def rank_key(sr):
                if not sr:
                    return (-1, )
                s = str(sr)
                # common patterns: S1,S2,S3,S4 / A1~A5 / B~ etc. We'll attempt to map alphabetic part then numeric.
                m = re.match(r'^([A-Za-z]+)(\d*)', s)
                if m:
                    a = m.group(1).upper()
                    n = int(m.group(2)) if m.group(2).isdigit() else 0
                    # ranking priority map (custom, bigger -> stronger)
                    order_map = {'SS': 12, 'S': 11, 'A': 10, 'B': 9, 'C': 8, 'D': 7, 'E': 6}
                    score = order_map.get(a, 5)  # unknown -> 5
                    return (score, n)
                # fallback: try numeric in string
                nums = re.findall(r'\d+', s)
                if nums:
                    return (5, int(nums[0]))
                return (0, )

            # build DataFrame
            prof_df = pd.DataFrame(profiles)
            # ensure numeric conversions
            prof_df['room_level'] = pd.to_numeric(prof_df['room_level'], errors='coerce').fillna(-1).astype(int)
            prof_df['follower_num'] = pd.to_numeric(prof_df['follower_num'], errors='coerce').fillna(-1).astype(int)
            prof_df['rank_key'] = prof_df['show_rank'].apply(rank_key)

            # sort by rank_key (descending), room_level desc, follower desc
            prof_df = prof_df.sort_values(by=['rank_key','room_level','follower_num'], ascending=[False, False, False])
            # top 10
            top_df = prof_df.head(10).copy()

            # Format for display
            def make_room_link(rid, name):
                url = f"https://www.showroom-live.com/room/profile?room_id={rid}"
                return f"{name} ({rid})\n{url}"

            top_df['room_link'] = top_df.apply(lambda r: make_room_link(r['room_id'], r['room_name'] or f"room_{r['room_id']}"), axis=1)
            display_cols = ['room_link','room_level','show_rank','follower_num','live_continuous_days','room_id']
            st.subheader("参加者（上位10件：SHOWランク＞ルームレベル＞フォロワー数）")
            st.dataframe(top_df[display_cols].rename(columns={
                'room_link':'ルーム (リンク付き表示は下を参照)',
                'room_level':'ルームレベル',
                'show_rank':'SHOWランク',
                'follower_num':'フォロワー数',
                'live_continuous_days':'毎日配信継続日数',
                'room_id':'room_id'
            }), use_container_width=True)

            st.markdown("#### ルームリンク（クリックして別タブで開けます）")
            for _, row in top_df.iterrows():
                name = row['room_name'] or f"room_{row['room_id']}"
                rid = row['room_id']
                url = f"https://www.showroom-live.com/room/profile?room_id={rid}"
                st.markdown(f"- [{name} ({rid})]({url})")
