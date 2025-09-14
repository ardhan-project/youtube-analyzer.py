import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import re

st.set_page_config(page_title="YouTube Trending Explorer", layout="wide")
st.title("🎬 YouTube Trending Explorer")

STOPWORDS = set("""
a an and the for of to in on with from by at as or & | - live official lyrics lyric audio video music mix hour hours relax relaxing study sleep deep best new latest 4k 8k
""".split())

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# ================== Sidebar ==================
if "api_key" not in st.session_state:
    st.session_state.api_key = ""
if "gemini_api" not in st.session_state:
    st.session_state.gemini_api = ""

with st.sidebar:
    st.header("⚙️ Pengaturan")
    api_key = st.text_input("YouTube Data API Key", st.session_state.api_key, type="password")
    gemini_api = st.text_input("Gemini API Key (Opsional)", st.session_state.gemini_api, type="password")
    st.caption("Belum punya Gemini API Key? 👉 [Buat di sini](https://aistudio.google.com/app/apikey)")
    max_per_order = st.slider("Jumlah video per kategori", 5, 30, 15, 1)
    if st.button("Simpan"):
        st.session_state.api_key = api_key
        st.session_state.gemini_api = gemini_api
        st.success("🔑 API Key berhasil disimpan!")

if not st.session_state.api_key:
    st.warning("⚠️ Masukkan API Key di sidebar untuk mulai")
    st.stop()

# ================== Tabs ==================
tab1, tab2 = st.tabs(["🔍 Cari Video", "💡 Ide Video"])

with tab1:
    with st.form("youtube_form"):
        keyword = st.text_input("Kata Kunci (kosongkan untuk Trending)", placeholder="healing flute meditation")
        sort_option = st.selectbox("Urutkan:", ["Paling Relevan", "Paling Banyak Ditonton", "Terbaru", "VPH Tertinggi"])
        video_type = st.radio("Tipe Video", ["Semua", "Regular", "Short", "Live"], horizontal=True)
        submit = st.form_submit_button("🔍 Cari Video")

with tab2:
    st.subheader("💡 Rekomendasi Ide Video dari Gemini AI")
    g_keyword = st.text_input("Masukkan Kata Kunci (untuk ide)", placeholder="contoh: bamboo flute meditation", key="g_kw")
    if st.button("Dapatkan Ide Video"):
        if not st.session_state.gemini_api:
            st.error("⚠️ Masukkan Gemini API Key di sidebar dulu.")
        else:
            try:
                import google.generativeai as genai
                genai.configure(api_key=st.session_state.gemini_api)
                model = genai.GenerativeModel("gemini-1.5-flash")
                prompt = f"""
Buatkan 5 ide konten video YouTube berbasis kata kunci: "{g_keyword}".
Format markdown bernomor. Sertakan untuk tiap ide:
- Judul ide
- Konsep konten (2-3 kalimat)
- Target audiens: usia, lokasi, minat
"""
                resp = model.generate_content(prompt)
                st.markdown(resp.text if hasattr(resp, "text") else "Tidak ada respons dari Gemini.")
            except Exception as e:
                st.error(f"❌ Error Gemini: {e}")

# ================== Utils ==================
def iso8601_to_seconds(duration: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not m: return 0
    h, mi, s = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
    return h*3600 + mi*60 + s

def fmt_duration(sec: int) -> str:
    if sec <= 0: return "-"
    h, m, s = sec//3600, (sec%3600)//60, sec%60
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

def hitung_vph(views, publishedAt):
    try:
        t = datetime.strptime(publishedAt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except:
        return 0.0
    hrs = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    return round(views/hrs, 2) if hrs > 0 else 0.0

def format_views(n):
    try: n = int(n)
    except: return str(n)
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000: return f"{n/1_000:.1f}K"
    return str(n)

def format_rel_time(publishedAt):
    try:
        dt = datetime.strptime(publishedAt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except: return "-"
    d = (datetime.now(timezone.utc) - dt).days
    if d < 1: return "Hari ini"
    if d < 30: return f"{d} hari lalu"
    if d < 365: return f"{d//30} bulan lalu"
    return f"{d//365} tahun lalu"

def format_jam_utc(publishedAt):
    try:
        dt = datetime.strptime(publishedAt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except: return "-"

# ================== API ==================
def yt_search_ids(api_key, query, order, max_results):
    params = {"part":"snippet","q":query,"type":"video","order":order,"maxResults":max_results,"key":api_key}
    r = requests.get(SEARCH_URL, params=params).json()
    return [it["id"]["videoId"] for it in r.get("items",[]) if it.get("id",{}).get("videoId")]

def yt_videos_detail(api_key, ids:list):
    if not ids: return []
    params = {"part":"statistics,snippet,contentDetails","id":",".join(ids),"key":api_key}
    r = requests.get(VIDEOS_URL, params=params).json()
    out = []
    for it in r.get("items",[]):
        snip, stats, det = it.get("snippet",{}), it.get("statistics",{}), it.get("contentDetails",{})
        views = int(stats.get("viewCount", 0)) if stats.get("viewCount") else 0
        dur_s = iso8601_to_seconds(det.get("duration", ""))
        rec = {
            "id": it.get("id"),
            "title": snip.get("title",""),
            "channel": snip.get("channelTitle",""),
            "publishedAt": snip.get("publishedAt",""),
            "views": views,
            "thumbnail": (snip.get("thumbnails",{}).get("high") or {}).get("url",""),
            "duration_sec": dur_s,
            "duration": fmt_duration(dur_s),
            "live": snip.get("liveBroadcastContent","none")
        }
        rec["vph"] = hitung_vph(rec["views"], rec["publishedAt"])
        out.append(rec)
    return out

def get_trending(api_key, max_results=15):
    params = {"part":"snippet,statistics,contentDetails","chart":"mostPopular","regionCode":"US","maxResults":max_results,"key":api_key}
    r = requests.get(VIDEOS_URL, params=params).json()
    return yt_videos_detail(api_key, [it["id"] for it in r.get("items",[])])

# ================== Sort & Filter ==================
def map_sort_option(sort_option: str):
    if sort_option == "Paling Banyak Ditonton": return "viewCount"
    if sort_option == "Terbaru": return "date"
    if sort_option == "VPH Tertinggi": return "date"
    return "relevance"

def apply_client_sort(items, sort_option: str):
    if sort_option == "Paling Banyak Ditonton":
        return sorted(items, key=lambda x: x.get("views", 0), reverse=True)
    if sort_option == "Terbaru":
        return sorted(items, key=lambda x: x.get("publishedAt", ""), reverse=True)
    if sort_option == "VPH Tertinggi":
        return sorted(items, key=lambda x: x.get("vph", 0.0), reverse=True)
    return items

def filter_by_video_type(items, video_type_label: str):
    if video_type_label == "Short":
        return [v for v in items if v.get("duration_sec", 0) <= 60]
    if video_type_label == "Regular":
        return [v for v in items if v.get("duration_sec", 0) > 60]
    if video_type_label == "Live":
        return [v for v in items if v.get("live", "none") == "live"]
    return items

# ================== Judul Generator ==================
def trim_to_100(text):
    if len(text) <= 100: return text
    trimmed = text[:100]
    if " " in trimmed: trimmed = trimmed[:trimmed.rfind(" ")]
    return trimmed

def generate_titles_from_data(videos, sort_option):
    if not videos: return []
    if sort_option == "Paling Banyak Ditonton":
        sorted_videos = sorted(videos, key=lambda x: x["views"], reverse=True)
    elif sort_option == "Terbaru":
        sorted_videos = sorted(videos, key=lambda x: x["publishedAt"], reverse=True)
    elif sort_option == "VPH Tertinggi":
        sorted_videos = sorted(videos, key=lambda x: x["vph"], reverse=True)
    else:
        sorted_videos = videos
    top_titles = [v["title"] for v in sorted_videos[:5]]
    rekomendasi = []
    for i in range(len(top_titles)):
        base = top_titles[i]
        extra = top_titles[(i+1) % len(top_titles)]
        combined = f"{base} | {extra}"
        if len(combined) < 66: combined += " | Koleksi Lengkap"
        rekomendasi.append(trim_to_100(combined))
    gabungan = " • ".join(top_titles[:3])
    if len(gabungan) < 66: gabungan += " | Terpopuler"
    rekomendasi.append(trim_to_100(gabungan))
    return [trim_to_100(t) for t in rekomendasi[:10]]

# ================== MAIN Cari Video ==================
if submit:
    if not keyword.strip():
        st.info("📈 Menampilkan trending (default US)")
        videos_all = get_trending(st.session_state.api_key, max_per_order)
    else:
        st.info(f"🔎 Riset keyword: {keyword}")
        order = map_sort_option(sort_option)
        ids = yt_search_ids(st.session_state.api_key, keyword, order, max_per_order)
        videos_all = yt_videos_detail(st.session_state.api_key, ids)

    videos_all = filter_by_video_type(videos_all, video_type)
    videos_all = apply_client_sort(videos_all, sort_option)

    if not videos_all:
        st.error("❌ Tidak ada video ditemukan")
    else:
        cols = st.columns(3)
        all_titles, rows_for_csv = [], []
        for i, v in enumerate(videos_all):
            with cols[i % 3]:
                # Thumbnail + Badge
                badge = ""
                if v["live"] == "live":
                    badge = "<div style='position:absolute;top:6px;left:6px;background:#e53935;color:white;padding:2px 6px;font-size:12px;border-radius:4px;font-weight:600;'>LIVE</div>"
                elif v.get("duration_sec", 0) <= 60:
                    badge = "<div style='position:absolute;top:6px;left:6px;background:#1e88e5;color:white;padding:2px 6px;font-size:12px;border-radius:4px;font-weight:600;'>SHORT</div>"
                if v["thumbnail"]:
                    st.markdown(f"<div style='position:relative;display:inline-block;width:100%;'>{badge}<img src='{v['thumbnail']}' style='width:100%;border-radius:10px;display:block;'></div>", unsafe_allow_html=True)

                st.markdown(f"**[{v['title']}]({'https://www.youtube.com/watch?v='+v['id']})**")
                st.caption(v["channel"])
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f"<div style='font-size:13px;background:#ff4b4b;color:white;padding:3px 8px;border-radius:8px;display:inline-block;'>👁 {format_views(v['views'])} views</div>", unsafe_allow_html=True)
                with c2:
                    st.markdown(f"<div style='font-size:13px;background:#4b8bff;color:white;padding:3px 8px;border-radius:8px;display:inline-block;'>⚡ {v['vph']} VPH</div>", unsafe_allow_html=True)
                with c3:
                    st.markdown(f"<div style='font-size:13px;background:#4caf50;color:white;padding:3px 8px;border-radius:8px;display:inline-block;'>⏱ {format_rel_time(v['publishedAt'])}</div>", unsafe_allow_html=True)
                st.caption(f"📅 {format_jam_utc(v['publishedAt'])} • ⏳ {v.get('duration','-')}")

            all_titles.append(v["title"])
            rows_for_csv.append({
                "Judul": v["title"], "Channel": v["channel"], "Views": v["views"],
                "VPH": v["vph"], "Tanggal": format_rel_time(v["publishedAt"]),
                "Jam Publish (UTC)": format_jam_utc(v["publishedAt"]),
                "Durasi": v.get("duration","-"), "Link": f"https://www.youtube.com/watch?v={v['id']}"
            })

        # ===== Rekomendasi Judul =====
        st.subheader("💡 Rekomendasi Judul (10 Judul, ≤100 Karakter)")
        rec_titles = generate_titles_from_data(videos_all, sort_option)
        if rec_titles:
            for idx, rt in enumerate(rec_titles, 1):
                col1, col2, col3 = st.columns([6, 1, 1])
                with col1: st.text_input(f"Judul {idx}", rt, key=f"judul_{idx}")
                with col2: st.markdown(f"<span style='font-size:12px;color:gray'>{len(rt)}/100</span>", unsafe_allow_html=True)
                with col3: st.button("📋", key=f"copy_judul_{idx}", help="Salin judul", on_click=lambda t=rt: st.session_state.update({"copied": t}))
            if "copied" in st.session_state:
                st.success(f"Judul tersalin: {st.session_state['copied']}")
                st.session_state.pop("copied")
        else:
            st.info("⚠️ Tidak ada judul yang bisa direkomendasikan.")

        # ===== Rekomendasi Tag =====
        st.subheader("🏷️ Rekomendasi Tag (max 500 karakter)")
        uniq_words, seen = [], set()
        for t in all_titles:
            for w in re.split(r"[^\w]+", t.lower()):
                if len(w) >= 3 and w not in STOPWORDS and w not in seen:
                    uniq_words.append(w); seen.add(w)
        tag_string = ", ".join(uniq_words)
        if len(tag_string) > 500: tag_string = tag_string[:497] + "..."
        col1, col2 = st.columns([8, 1])
        with col1: st.text_area("Tag", tag_string, height=100)
        with col2: st.button("📋", key="copy_tag", help="Salin tag", on_click=lambda t=tag_string: st.session_state.update({"copied_tag": t}))
        if "copied_tag" in st.session_state:
            st.success("✅ Tag tersalin!")
            st.session_state.pop("copied_tag")

        # ===== Download CSV =====
        st.subheader("⬇️ Download Data")
        df = pd.DataFrame(rows_for_csv)
        st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), "youtube_riset.csv", "text/csv")
