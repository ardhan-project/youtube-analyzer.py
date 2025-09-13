import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
from collections import Counter
import re

st.set_page_config(page_title="YouTube Trending Explorer", layout="wide")
st.title("🎬 YouTube Trending Explorer")

# ================== Region List ==================
YOUTUBE_REGIONS = {
    "Worldwide (US Default)":"US","Argentina":"AR","Australia":"AU","Austria":"AT","Bahrain":"BH","Bangladesh":"BD","Belgium":"BE","Bolivia":"BO",
    "Brazil":"BR","Bulgaria":"BG","Canada":"CA","Chile":"CL","Colombia":"CO","Costa Rica":"CR","Croatia":"HR","Cyprus":"CY","Czech Republic":"CZ",
    "Denmark":"DK","Dominican Republic":"DO","Ecuador":"EC","Egypt":"EG","Finland":"FI","France":"FR","Germany":"DE","Greece":"GR","Guatemala":"GT",
    "Hong Kong":"HK","Hungary":"HU","India":"IN","Indonesia":"ID","Ireland":"IE","Israel":"IL","Italy":"IT","Japan":"JP","Kenya":"KE","Kuwait":"KW",
    "Latvia":"LV","Lebanon":"LB","Lithuania":"LT","Luxembourg":"LU","Malaysia":"MY","Mexico":"MX","Morocco":"MA","Nepal":"NP","Netherlands":"NL",
    "New Zealand":"NZ","Nigeria":"NG","Norway":"NO","Pakistan":"PK","Peru":"PE","Philippines":"PH","Poland":"PL","Portugal":"PT","Qatar":"QA",
    "Romania":"RO","Russia":"RU","Saudi Arabia":"SA","Singapore":"SG","Slovakia":"SK","Slovenia":"SI","South Africa":"ZA","South Korea":"KR",
    "Spain":"ES","Sri Lanka":"LK","Sweden":"SE","Switzerland":"CH","Taiwan":"TW","Thailand":"TH","Turkey":"TR","Ukraine":"UA","UAE":"AE",
    "United Kingdom":"GB","United States":"US","Vietnam":"VN","Zimbabwe":"ZW"
}

STOPWORDS = set("""
a an and the for of to in on with from by at as or & | - live official lyrics lyric audio video music mix hour hours relax relaxing study sleep deep best new latest 4k 8k
""".split())

SEARCH_URL="https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL="https://www.googleapis.com/youtube/v3/videos"

# ================== Sidebar ==================
if "api_key" not in st.session_state:
    st.session_state.api_key = ""

with st.sidebar:
    st.header("⚙️ Pengaturan")
    api_key = st.text_input("YouTube Data API Key", st.session_state.api_key, type="password")
    max_per_order = st.slider("Jumlah video per kategori", 5, 30, 15, 1)
    region_name = st.selectbox("Pilih Negara", list(YOUTUBE_REGIONS.keys()))
    region = YOUTUBE_REGIONS[region_name]
    if st.button("Simpan"):
        st.session_state.api_key = api_key
        st.success("API Key berhasil disimpan!")

if not st.session_state.api_key:
    st.warning("⚠️ Masukkan API Key di sidebar untuk mulai")
    st.stop()

# ================== Form Input ==================
with st.form("youtube_form"):
    keyword = st.text_input("Kata Kunci (kosongkan untuk Trending)", placeholder="healing flute meditation")
    sort_option = st.selectbox("Urutkan:", ["Paling Relevan","Paling Banyak Ditonton","Terbaru","VPH Tertinggi"])
    video_type = st.radio("Tipe Video", ["Semua","Regular","Short","Live"])
    submit = st.form_submit_button("🔍 Cari Video")

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
def yt_search_ids(api_key, query, order, max_results, video_type="all"):
    params={"part":"snippet","q":query,"type":"video","order":order,"maxResults":max_results,"key":api_key}
    if video_type=="short": params["videoDuration"]="short"
    elif video_type=="regular": params["videoDuration"]="medium"
    elif video_type=="live": params["eventType"]="live"
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

def get_trending(api_key, region="US", max_results=15):
    params={"part":"snippet,statistics,contentDetails","chart":"mostPopular","regionCode":region,"maxResults":max_results,"key":api_key}
    r=requests.get(VIDEOS_URL,params=params).json()
    return yt_videos_detail(api_key,[it["id"] for it in r.get("items",[])])

# ================== Sort & Filter Helpers ==================
def map_sort_option(sort_option: str):
    if sort_option == "Paling Banyak Ditonton":
        return "viewCount"
    elif sort_option == "Terbaru":
        return "date"
    elif sort_option == "VPH Tertinggi":
        return "date"  # ambil by date, sort manual di client
    else:
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
def top_keywords_from_titles(titles, topk=8):
    words=[]
    for t in titles:
        for w in re.split(r"[^\w]+",t.lower()):
            if len(w)>=3 and w not in STOPWORDS and not w.isdigit():
                words.append(w)
    cnt=Counter(words)
    return [w for w,_ in cnt.most_common(topk)]

def derive_duration_phrase(videos):
    secs=[v["duration_sec"] for v in videos if v.get("duration_sec",0)>0]
    if not secs: return "3 Hours"
    avg=sum(secs)/len(secs)
    if avg>=2*3600: return "3 Hours"
    if avg>=3600: return "2 Hours"
    if avg>=1800: return "1 Hour"
    return "30 Minutes"

def ensure_len(s, min_len=66):
    if len(s)>=min_len: return s
    pad=" | Focus • Study • Relax • Deep Sleep"
    return s+pad

def generate_titles_structured(keyword_main,videos,titles_all):
    kw=keyword_main.strip() or "Healing Flute Meditation"
    topk=top_keywords_from_titles(titles_all,8)
    k1=(topk[0] if topk else "Relaxation").title()
    k2=(topk[1] if len(topk)>1 else "Sleep").title()
    dur=derive_duration_phrase(videos)
    titles=[]
    titles.append(f"Eliminate Stress & Anxiety | {kw.title()} for Deep Relaxation and Inner Peace")
    titles.append(f"Heal Faster & Clear Mind | {kw.title()} Therapy for {k1} and {k2}")
    titles.append(f"Emotional Detox & Calm | {kw.title()} – Release Negativity, Find Balance")
    titles.append(f"{kw.title()} | Deep Calm and Healing Energy for Sleep & Meditation")
    titles.append(f"{kw.title()} | Stress Relief and Emotional Reset for Night Routine")
    titles.append(f"{kw.title()} | Gentle Sounds to Focus, Study and Inner Healing")
    titles.append(f"{dur} | {kw.title()} – Reduce Overthinking, Fall Asleep Fast")
    titles.append(f"{dur} Non-Stop | {kw.title()} – Relax Mind, Boost Serotonin")
    titles.append(f"10 Hours Loop | {kw.title()} – Deep Meditation and Emotional Balance")
    titles.append(f"{dur} | {kw.title()} – Perfect for Yoga, Sleep and Stress Detox")
    return [ensure_len(t) for t in titles[:10]]

# ================== MAIN ==================
if submit:
    if not keyword.strip():
        st.info(f"📈 Menampilkan trending di {region_name}")
        videos_all=get_trending(st.session_state.api_key,region=region,max_results=max_per_order)
    else:
        st.info(f"🔎 Riset keyword: {keyword}")
        vtype_map={"Semua":"all","Regular":"regular","Short":"short","Live":"live"}
        order=map_sort_option(sort_option)
        ids=yt_search_ids(st.session_state.api_key,keyword,order,max_per_order,vtype_map[video_type])
        videos_all=yt_videos_detail(st.session_state.api_key,ids)

    # Terapkan filter tipe video & sort manual di client
    videos_all = filter_by_video_type(videos_all, video_type)
    videos_all = apply_client_sort(videos_all, sort_option)

    if not videos_all:
        st.error("❌ Tidak ada video ditemukan")
    else:
        # tampilkan video
        cols=st.columns(3)
        all_titles=[]; rows_for_csv=[]
        for i,v in enumerate(videos_all):
            with cols[i%3]:
                if v["thumbnail"]: st.image(v["thumbnail"])
                st.markdown(f"**[{v['title']}]({'https://www.youtube.com/watch?v='+v['id']})**")
                st.caption(v["channel"])
                c1,c2,c3=st.columns(3)
                with c1: st.markdown(f"<div style='background:#ff4b4b;color:white;padding:4px 8px;border-radius:6px;display:inline-block'>👁 {format_views(v['views'])} views</div>",unsafe_allow_html=True)
                with c2: st.markdown(f"<div style='background:#4b8bff;color:white;padding:4px 8px;border-radius:6px;display:inline-block'>⚡ {v['vph']} VPH</div>",unsafe_allow_html=True)
                with c3: st.markdown(f"<div style='background:#4caf50;color:white;padding:4px 8px;border-radius:6px;display:inline-block'>⏱ {format_rel_time(v['publishedAt'])}</div>",unsafe_allow_html=True)
                st.caption(f"📅 {format_jam_utc(v['publishedAt'])} • ⏳ {v.get('duration','-')}")
            all_titles.append(v["title"])
            rows_for_csv.append({"Judul":v["title"],"Channel":v["channel"],"Views":v["views"],"VPH":v["vph"],
                "Tanggal":format_rel_time(v["publishedAt"]),"Jam Publish":format_jam_utc(v["publishedAt"]),
                "Durasi":v.get("duration","-"),"Link":f"https://www.youtube.com/watch?v={v['id']}"})

        # Judul
        st.subheader("💡 Rekomendasi Judul (10 Judul)")
        rec_titles=generate_titles_structured(keyword,videos_all,all_titles)
        for idx,rt in enumerate(rec_titles,1):
            col1,col2=st.columns([8,1])
            with col1: st.text_input(f"Judul {idx}",rt,key=f"judul_{idx}")
            with col2: st.button("📋",key=f"copy_judul_{idx}",help="Salin judul",on_click=lambda t=rt: st.session_state.update({"copied":t}))

        if "copied" in st.session_state:
            st.success(f"Judul tersalin: {st.session_state['copied']}")
            st.session_state.pop("copied")

        # Tag
        st.subheader("🏷️ Rekomendasi Tag (max 500 karakter)")
        uniq_words,seen=[],set()
        for t in all_titles:
            for w in re.split(r"[^\w]+",t.lower()):
                if len(w)>=3 and w not in STOPWORDS and w not in seen:
                    uniq_words.append(w); seen.add(w)
        tag_string=", ".join(uniq_words)
        if len(tag_string)>500: tag_string=tag_string[:497]+"..."
        col1,col2=st.columns([8,1])
        with col1: st.text_area("Tag",tag_string,height=100)
        with col2: st.button("📋",key="copy_tag",help="Salin tag",on_click=lambda t=tag_string: st.session_state.update({"copied_tag":t}))
        if "copied_tag" in st.session_state:
            st.success("✅ Tag tersalin!")
            st.session_state.pop("copied_tag")

        # Download CSV
        st.subheader("⬇️ Download Data")
        df=pd.DataFrame(rows_for_csv)
        st.download_button("Download CSV",df.to_csv(index=False).encode("utf-8"),"youtube_riset.csv","text/csv")
