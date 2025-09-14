import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import re
import io
import zipfile

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
if "auto_ideas" not in st.session_state:
    st.session_state.auto_ideas = None

with st.sidebar:
    st.header("⚙️ Pengaturan")
    api_key = st.text_input("YouTube Data API Key", st.session_state.api_key, type="password", key="yt_api_key")
    gemini_api = st.text_input("Gemini API Key (Opsional)", st.session_state.gemini_api, type="password", key="gemini_api_key")
    st.caption("Belum punya Gemini API Key? 👉 [Buat di sini](https://aistudio.google.com/app/apikey)")
    max_per_order = st.slider("Jumlah video per kategori", 5, 30, 15, 1, key="max_per_order")
    if st.button("Simpan", key="save_api"):
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
        keyword = st.text_input("Kata Kunci (kosongkan untuk Trending)", placeholder="healing flute meditation", key="keyword_input")
        sort_option = st.selectbox("Urutkan:", ["Paling Relevan", "Paling Banyak Ditonton", "Terbaru", "VPH Tertinggi"], key="sort_option")
        video_type = st.radio("Tipe Video", ["Semua", "Regular", "Short", "Live"], horizontal=True, key="video_type")
        submit = st.form_submit_button("🔍 Cari Video", key="search_video")

with tab2:
    st.subheader("💡 Rekomendasi Ide Video (otomatis dari hasil pencarian)")
    if st.session_state.auto_ideas:
        st.markdown(st.session_state.auto_ideas)
    else:
        st.info("⚠️ Belum ada ide. Silakan cari video dulu di tab 🔍.")

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
            "channelId": snip.get("channelId",""),
            "description": snip.get("description",""),
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
        return [v for v in items if v.get("duration_sec", 0) <= 60 and v.get("live", "none") == "none"]
    if video_type_label == "Regular":
        return [v for v in items if v.get("duration_sec", 0) > 60 and v.get("live", "none") == "none"]
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

# ================== MAIN ==================
if submit:
    if not keyword.strip():
        st.info("📈 Menampilkan trending (default US)")
        videos_all = get_trending(st.session_state.api_key, max_per_order)
    else:
        st.info(f"🔎 Riset keyword: {keyword}")
        order = map_sort_option(sort_option)
        ids = yt_search_ids(st.session_state.api_key, keyword, order, max_per_order)
        videos_all = yt_videos_detail(st.session_state.api_key, ids)

    # Filter & sort
    videos_all = filter_by_video_type(videos_all, video_type)
    videos_all = apply_client_sort(videos_all, sort_option)

    if not videos_all:
        st.error("❌ Tidak ada video ditemukan")
    else:
        cols = st.columns(3)
        all_titles, rows_for_csv = [], []
        for i, v in enumerate(videos_all):
            with cols[i % 3]:
                # Thumbnail klik → popup
                if v["thumbnail"]:
                    if st.button(f"thumb_{i}", key=f"thumb_btn_{i}"):
                        st.session_state["popup_video"] = v
                    st.markdown(
                        f"""
                        <div style="cursor:pointer;" onclick="window.parent.postMessage({{'setVideo':'{v['id']}' }}, '*')">
                            <img src="{v['thumbnail']}" style="width:100%;border-radius:10px;">
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                # Judul klik → popup
                if st.button(v["title"], key=f"title_btn_{i}"):
                    st.session_state["popup_video"] = v

                st.caption(v["channel"])
                st.caption(f"👁 {format_views(v['views'])} | ⚡ {v['vph']} VPH | ⏱ {format_rel_time(v['publishedAt'])}")
                st.caption(f"📅 {format_jam_utc(v['publishedAt'])} • ⏳ {v.get('duration','-')}")

            all_titles.append(v["title"])
            rows_for_csv.append({
                "Judul": v["title"],
                "Channel": v["channel"],
                "Views": v["views"],
                "VPH": v["vph"],
                "Tanggal (relatif)": format_rel_time(v["publishedAt"]),
                "Jam Publish (UTC)": format_jam_utc(v["publishedAt"]),
                "Durasi": v.get("duration","-"),
                "Link": f"https://www.youtube.com/watch?v={v['id']}"
            })

        # ===== Popup Video Detail =====
        if "popup_video" in st.session_state:
            v = st.session_state["popup_video"]
            st.markdown("---")
            st.subheader("📺 Video Detail")
            st.video(f"https://www.youtube.com/watch?v={v['id']}")
            st.markdown(f"### {v['title']}")
            st.caption(v["channel"])
            st.write(v.get("description", "Tidak ada deskripsi."))

            st.subheader("✨ Asisten Konten AI")
            col1, col2 = st.columns(2)
            with col1: st.button("📑 Ringkas Video Ini", key="ai_summary")
            with col2: st.button("✍️ Buat Judul Alternatif", key="ai_titles")
            col3, col4 = st.columns(2)
            with col3: st.button("📝 Buat Kerangka Skrip", key="ai_script")
            with col4: st.button("🖼️ Buat Ide Thumbnail", key="ai_thumb")
            st.button("🏷️ Buat Tag SEO", key="ai_tags")

            if v.get("channelId"):
                st.markdown(f"[🌐 Kunjungi Channel YouTube](https://www.youtube.com/channel/{v['channelId']})")

            if st.button("❌ Tutup", key="close_popup"):
                del st.session_state["popup_video"]

        # ===== Rekomendasi Judul =====
        st.subheader("💡 Rekomendasi Judul (10 Judul, ≤100 Karakter)")
        rec_titles = generate_titles_from_data(videos_all, sort_option)
        for idx, rt in enumerate(rec_titles, 1):
            col1, col2, col3 = st.columns([6, 1, 1])
            with col1: st.text_input(f"Judul {idx}", rt, key=f"judul_{idx}")
            with col2: st.markdown(f"<span style='font-size:12px;color:gray'>{len(rt)}/100</span>", unsafe_allow_html=True)
            with col3: st.button("📋", key=f"copy_judul_{idx}", on_click=lambda t=rt: st.session_state.update({"copied": t}))

        if "copied" in st.session_state:
            st.success(f"Judul tersalin: {st.session_state['copied']}")
            st.session_state.pop("copied")

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
        with col1: st.text_area("Tag", tag_string, height=100, key="tag_area")
        with col2: st.button("📋", key="copy_tag", on_click=lambda t=tag_string: st.session_state.update({"copied_tag": t}))

        if "copied_tag" in st.session_state:
            st.success("✅ Tag tersalin!")
            st.session_state.pop("copied_tag")

        # ===== Download Data =====
        st.subheader("⬇️ Download Data")
        df = pd.DataFrame(rows_for_csv)
        csv_video_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV (Video)", csv_video_bytes, "youtube_riset.csv", "text/csv", key="dl_csv")

        if st.session_state.auto_ideas:
            ideas_txt_bytes = st.session_state.auto_ideas.encode("utf-8")
            st.download_button("Download Ide (TXT)", ideas_txt_bytes, "auto_ideas.txt", "text/plain", key="dl_txt")

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("youtube_riset.csv", csv_video_bytes)
                zf.writestr("auto_ideas.txt", ideas_txt_bytes)
            st.download_button("Download Paket (ZIP)", zip_buffer.getvalue(), "paket_riset.zip", "application/zip", key="dl_zip")
