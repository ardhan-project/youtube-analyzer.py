import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import re
import google.generativeai as genai

st.set_page_config(page_title="YouTube Trending Explorer", layout="wide")
st.title("🎬 YouTube Trending Explorer")

STOPWORDS = set("""
a an and the for of to in on with from by at as or & | - live official lyrics lyric audio video music mix hour hours relax relaxing study sleep deep best new latest 4k 8k
""".split())

SEARCH_URL="https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL="https://www.googleapis.com/youtube/v3/videos"

# ================== Sidebar ==================
if "api_key" not in st.session_state:
    st.session_state.api_key = ""
if "gemini_api" not in st.session_state:
    st.session_state.gemini_api = ""

with st.sidebar:
    st.header("⚙️ Pengaturan")
    api_key = st.text_input("YouTube Data API Key", st.session_state.api_key, type="password")
    gemini_api = st.text_input("Gemini API Key (Opsional)", st.session_state.gemini_api, type="password")
    max_per_order = st.slider("Jumlah video per kategori", 5, 30, 15, 1)
    if st.button("Simpan"):
        st.session_state.api_key = api_key
        st.session_state.gemini_api = gemini_api
        st.success("🔑 API Key berhasil disimpan!")

if not st.session_state.api_key:
    st.warning("⚠️ Masukkan API Key di sidebar untuk mulai")
    st.stop()

# ================== Form Input ==================
tab1, tab2 = st.tabs(["🔍 Cari Video", "💡 Ide Video"])

with tab1:
    with st.form("youtube_form"):
        keyword = st.text_input("Kata Kunci (kosongkan untuk Trending)", placeholder="healing flute meditation")
        sort_option = st.selectbox("Urutkan:", ["Paling Relevan","Paling Banyak Ditonton","Terbaru","VPH Tertinggi"])
        video_type = st.radio("Tipe Video", ["Semua","Regular","Short","Live"])
        submit = st.form_submit_button("Cari Video")

with tab2:
    st.subheader("💡 Rekomendasi Ide Video dari Gemini AI")
    if not st.session_state.gemini_api:
        st.info("⚠️ Masukkan Gemini API Key untuk menggunakan fitur ini")
    else:
        g_keyword = st.text_input("Masukkan Kata Kunci", placeholder="contoh: bamboo flute meditation")
        if st.button("Dapatkan Ide Video"):
            try:
                genai.configure(api_key=st.session_state.gemini_api)
                model = genai.GenerativeModel("gemini-1.5-flash")
                prompt = f"Buatkan 5 ide konten video YouTube berdasarkan kata kunci '{g_keyword}'. Sertakan target audiens (usia, lokasi, interest)."
                response = model.generate_content(prompt)
                st.write(response.text)
            except Exception as e:
                st.error(f"❌ Error Gemini: {e}")

# ================== Utils ==================
def iso8601_to_seconds(duration: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not m: return 0
    h, mi, s = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
    return h*3600+mi*60+s

def fmt_duration(sec: int) -> str:
    if sec<=0: return "-"
    h, m, s = sec//3600, (sec%3600)//60, sec%60
    return f"{h}:{m:02d}:{s:02d}" if h>0 else f"{m}:{s:02d}"

def hitung_vph(views, publishedAt):
    try: t = datetime.strptime(publishedAt,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except: return 0.0
    hrs=(datetime.now(timezone.utc)-t).total_seconds()/3600
    return round(views/hrs,2) if hrs>0 else 0.0

def format_views(n):
    try: n=int(n)
    except: return str(n)
    if n>=1_000_000: return f"{n/1_000_000:.1f}M"
    if n>=1_000: return f"{n/1_000:.1f}K"
    return str(n)

def format_rel_time(publishedAt):
    try: dt=datetime.strptime(publishedAt,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except: return "-"
    d=(datetime.now(timezone.utc)-dt).days
    if d<1: return "Hari ini"
    if d<30: return f"{d} hari lalu"
    if d<365: return f"{d//30} bulan lalu"
    return f"{d//365} tahun lalu"

def format_jam_utc(publishedAt):
    try: dt=datetime.strptime(publishedAt,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc); return dt.strftime("%Y-%m-%d %H:%M UTC")
    except: return "-"

# ================== API ==================
def yt_search_ids(api_key, query, order, max_results):
    params={"part":"snippet","q":query,"type":"video","order":order,"maxResults":max_results,"key":api_key}
    r=requests.get(SEARCH_URL,params=params).json()
    return [it["id"]["videoId"] for it in r.get("items",[]) if it.get("id",{}).get("videoId")]

def yt_videos_detail(api_key, ids:list):
    if not ids: return []
    params={"part":"statistics,snippet,contentDetails","id":",".join(ids),"key":api_key}
    r=requests.get(VIDEOS_URL,params=params).json()
    out=[]
    for it in r.get("items",[]):
        snip,stats,det=it.get("snippet",{}),it.get("statistics",{}),it.get("contentDetails",{})
        views=int(stats.get("viewCount",0)) if stats.get("viewCount") else 0
        dur_s=iso8601_to_seconds(det.get("duration",""))
        rec={
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
        rec["vph"]=hitung_vph(rec["views"], rec["publishedAt"])
        out.append(rec)
    return out

# ================== MAIN ==================
if tab1 and submit:
    if not keyword.strip():
        st.info("📈 Menampilkan trending (default US)")
        ids=[]
        params={"part":"snippet","chart":"mostPopular","regionCode":"US","maxResults":max_per_order,"key":st.session_state.api_key}
        r=requests.get(VIDEOS_URL,params=params).json()
        ids=[it["id"] for it in r.get("items",[])]
        videos_all=yt_videos_detail(st.session_state.api_key,ids)
    else:
        st.info(f"🔎 Riset keyword: {keyword}")
        order="relevance"
        ids=yt_search_ids(st.session_state.api_key,keyword,order,max_per_order)
        videos_all=yt_videos_detail(st.session_state.api_key,ids)

    if not videos_all:
        st.error("❌ Tidak ada video ditemukan")
    else:
        cols=st.columns(3)
        for i,v in enumerate(videos_all):
            with cols[i%3]:
                if v["thumbnail"]:
                    # Thumbnail + Badge
                    badge=""
                    if v["live"]=="live":
                        badge="<div style='position:absolute;top:5px;left:5px;background:red;color:white;padding:2px 5px;font-size:12px;border-radius:3px;'>LIVE</div>"
                    elif v["duration_sec"]<=60:
                        badge="<div style='position:absolute;top:5px;left:5px;background:blue;color:white;padding:2px 5px;font-size:12px;border-radius:3px;'>SHORT</div>"
                    st.markdown(f"<div style='position:relative;display:inline-block;'>{badge}<img src='{v['thumbnail']}' style='width:100%;border-radius:8px;'></div>",unsafe_allow_html=True)
                st.markdown(f"**[{v['title']}]({'https://www.youtube.com/watch?v='+v['id']})**")
                st.caption(v["channel"])
