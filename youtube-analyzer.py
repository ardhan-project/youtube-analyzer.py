import streamlit as st
import requests
from datetime import datetime, timezone

# === Konfigurasi Awal ===
st.set_page_config(page_title="YouTube Trending Explorer", layout="wide")

st.title("🎬 YouTube Trending Explorer")
st.write("Temukan video trending dan populer dari seluruh dunia")

# === Input API Key ===
if "api_key" not in st.session_state:
    st.session_state.api_key = ""

with st.sidebar:
    st.header("⚙️ Pengaturan")
    api_key = st.text_input("YouTube Data API Key", st.session_state.api_key, type="password")
    if st.button("Simpan"):
        st.session_state.api_key = api_key
        st.success("API Key berhasil disimpan!")

if not st.session_state.api_key:
    st.warning("⚠️ Masukkan API Key di sidebar untuk mulai")
    st.stop()

# === Form Input ===
with st.form("youtube_form"):
    keyword = st.text_input("Kata Kunci", placeholder="healing flute meditation")
    sort_option = st.selectbox("Urutkan:", ["Paling Relevan", "Paling Banyak Ditonton", "Terbaru", "VPH Tertinggi"])
    submit = st.form_submit_button("🔍 Cari Video")

# === Fungsi Utility ===
def hitung_vph(views, publishedAt):
    published_time = datetime.strptime(publishedAt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - published_time).total_seconds() / 3600
    return round(views / hours, 2) if hours > 0 else 0

def format_views(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def format_time(publishedAt):
    dt = datetime.strptime(publishedAt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    delta = (datetime.now(timezone.utc) - dt).days
    if delta < 1:
        return "Hari ini"
    elif delta < 30:
        return f"{delta} hari lalu"
    elif delta < 365:
        return f"{delta//30} bulan lalu"
    return f"{delta//365} tahun lalu"

# === Panggil API YouTube ===
def get_youtube_videos(api_key, query, max_results=15):
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "key": api_key
    }
    r = requests.get(url, params=params).json()

    videos = []
    video_ids = [item["id"]["videoId"] for item in r.get("items", [])]

    if not video_ids:
        return []

    stats_url = "https://www.googleapis.com/youtube/v3/videos"
    stats_params = {
        "part": "statistics,snippet",
        "id": ",".join(video_ids),
        "key": api_key
    }
    stats_r = requests.get(stats_url, params=stats_params).json()

    for item in stats_r.get("items", []):
        vid = {
            "id": item["id"],
            "title": item["snippet"]["title"],
            "channel": item["snippet"]["channelTitle"],
            "publishedAt": item["snippet"]["publishedAt"],
            "views": int(item["statistics"].get("viewCount", 0)),
            "thumbnail": item["snippet"]["thumbnails"]["high"]["url"],
        }
        vid["vph"] = hitung_vph(vid["views"], vid["publishedAt"])
        videos.append(vid)

    return videos

# === Sorting ===
def urutkan_video(data, mode):
    if mode == "Paling Banyak Ditonton":
        return sorted(data, key=lambda x: x["views"], reverse=True)
    elif mode == "Terbaru":
        return sorted(data, key=lambda x: x["publishedAt"], reverse=True)
    elif mode == "VPH Tertinggi":
        return sorted(data, key=lambda x: x["vph"], reverse=True)
    else:  # relevan (default API)
        return data

# === Jalankan pencarian ===
if submit:
    with st.spinner("Mengambil data dari YouTube..."):
        videos = get_youtube_videos(st.session_state.api_key, keyword)
        videos = urutkan_video(videos, sort_option)

    if not videos:
        st.error("❌ Tidak ada video ditemukan")
    else:
        st.success(f"{len(videos)} video ditemukan")

        # === Tampilkan Video dengan Badge ===
        cols = st.columns(3)
        all_titles = []
        for i, v in enumerate(videos):
            with cols[i % 3]:
                st.image(v["thumbnail"])
                st.markdown(f"**[{v['title']}]({'https://www.youtube.com/watch?v=' + v['id']})**")
                st.caption(v["channel"])

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(
                        f"<div style='background:#ff4b4b;color:white;padding:4px 8px;border-radius:6px;display:inline-block'>👁 {format_views(v['views'])} views</div>",
                        unsafe_allow_html=True
                    )
                with col2:
                    st.markdown(
                        f"<div style='background:#4b8bff;color:white;padding:4px 8px;border-radius:6px;display:inline-block'>⚡ {v['vph']} VPH</div>",
                        unsafe_allow_html=True
                    )
                with col3:
                    st.markdown(
                        f"<div style='background:#4caf50;color:white;padding:4px 8px;border-radius:6px;display:inline-block'>⏱ {format_time(v['publishedAt'])}</div>",
                        unsafe_allow_html=True
                    )

            all_titles.append(v["title"])

        # === Rekomendasi Judul ===
        st.subheader("💡 Rekomendasi Judul untuk Dipakai")
        for r in all_titles[:5]:
            st.text_input("Copy Judul", r)

        # === Auto Tag 500 karakter ===
        st.subheader("🏷️ Rekomendasi Tag (Max 500 karakter)")
        kata_unik = []
        for t in all_titles:
            for w in t.split():
                w = w.lower().strip("|,.-")
                if w not in kata_unik:
                    kata_unik.append(w)

        tag_string = ", ".join(kata_unik)
        if len(tag_string) > 500:
            tag_string = tag_string[:497] + "..."

        st.code(tag_string, language="text")
        st.text_input("Copy Tag", tag_string)
