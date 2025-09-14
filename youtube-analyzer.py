import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import re
import io
import zipfile
import html as html_lib

st.set_page_config(page_title="YouTube Trending Explorer", layout="wide")
st.title("🎬 YouTube Trending Explorer")

STOPWORDS = set("""
a an and the for of to in on with from by at as or & | - live official lyrics lyric audio video music mix hour hours relax relaxing study sleep deep best new latest 4k 8k
""".split())

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# ---------------- Session init ----------------
if "api_key" not in st.session_state: st.session_state.api_key = ""
if "gemini_api" not in st.session_state: st.session_state.gemini_api = ""
if "auto_ideas" not in st.session_state: st.session_state.auto_ideas = None
if "last_results" not in st.session_state: st.session_state.last_results = []
if "popup_video" not in st.session_state: st.session_state.popup_video = None
if "ai_cache" not in st.session_state: st.session_state.ai_cache = {}   # {video_id: {task: text}}

# ---------------- Sidebar ----------------
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

# ---------------- Tabs ----------------
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

# ---------------- Utils ----------------
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

# ---------------- API ----------------
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

# ---------------- Sort & Filter ----------------
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

# ---------------- Judul Generator (list 10) ----------------
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

# ---------------- Gemini helpers ----------------
def use_gemini():
    return bool(st.session_state.gemini_api)

def gemini_generate(prompt: str) -> str:
    if not use_gemini():
        return ""
    try:
        import google.generativeai as genai
        genai.configure(api_key=st.session_state.gemini_api)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt)
        return resp.text if hasattr(resp, "text") else ""
    except Exception as e:
        return f"❌ Error Gemini: {e}"

def content_type(v):
    if v.get("live") == "live": return "Live"
    if v.get("duration_sec", 0) <= 60: return "Short"
    return "Regular"

# ---------------- AI tasks (with fallback) ----------------
def ai_summary(v):
    title, desc, ch = v["title"], v.get("description",""), v.get("channel","")
    base = f"Judul: {title}\nChannel: {ch}\nDeskripsi:\n{desc[:3000]}"
    if use_gemini():
        return gemini_generate(
            f"Ringkas video berdasarkan informasi berikut menjadi 5 poin bullet berbahasa Indonesia, fokus manfaat untuk penonton, hindari klaim berlebihan:\n\n{base}"
        )
    # fallback ringkas: ambil kalimat pertama/utama
    sentences = re.split(r'(?<=[.!?])\s+', desc)[:5]
    if not sentences: sentences = [title]
    bullets = "\n".join(f"- {s}" for s in sentences)
    return f"**Ringkasan (heuristik)**\n{bullets}"

def ai_alt_titles(v):
    ct = content_type(v)
    if use_gemini():
        return gemini_generate(
            f"Buat 10 judul alternatif YouTube (≤100 karakter) dalam bahasa Indonesia untuk video '{v['title']}'. "
            f"Format konten: {ct}. Variasikan gaya (angka, kurung, pertanyaan), klik-worthy namun bukan clickbait. Output sebagai daftar bernomor."
        )
    # fallback: variasi sederhana
    base = v["title"]
    variants = [
        trim_to_100(base),
        trim_to_100(f"{base} | Versi Lengkap"),
        trim_to_100(f"{base} (Tips & Trik)"),
        trim_to_100(f"Rahasia {base} Terungkap!"),
        trim_to_100(f"{base}: Cara Praktis"),
        trim_to_100(f"{base} untuk Pemula"),
        trim_to_100(f"{base} dalam 60 Detik"),
        trim_to_100(f"{base} [Panduan 2025]"),
        trim_to_100(f"Kenapa {base}? Jawabannya di Sini"),
        trim_to_100(f"{base} | Tutorial Singkat")
    ]
    return "\n".join(f"{i+1}. {t}" for i, t in enumerate(variants))

def ai_script_outline(v):
    ct = content_type(v)
    title = v["title"]
    if use_gemini():
        return gemini_generate(
            f"Buat kerangka skrip video YouTube berbahasa Indonesia untuk '{title}'. "
            f"Format: {ct}. Sertakan: HOOK, Intro, 3–6 Bagian Utama, CTA. "
            f"Untuk Short, buat super ringkas (≤60 detik); untuk Live, gunakan segmen pembuka, agenda, interaksi chat, checkpoint, closing."
        )
    # fallback
    if ct == "Short":
        return ("HOOK (0-3s): Pancing rasa penasaran.\n"
                "INTI (3-50s): 3 poin cepat bernilai.\n"
                "CTA (50-60s): Ajak follow/subscribe & video terkait.")
    if ct == "Live":
        return ("Opening (0-2m): Sapaan & value promise.\n"
                "Agenda: 3-5 topik.\n"
                "Interaksi: Q&A tiap 10-15 menit.\n"
                "Checkpoint: ringkas poin utama.\n"
                "Closing: CTA & jadwal live berikutnya.")
    return ("Hook (0-10s) → Intro (10-30s) → Bagian 1 → Bagian 2 → Bagian 3 → "
            "Rekap → CTA (subscribe/next video).")

def ai_thumb_ideas(v):
    title = v["title"]
    kw = ", ".join(sorted({w for w in re.split(r"[^\w]+", (title + " " + v.get('description','')).lower())
                           if len(w)>=4 and w not in STOPWORDS})[:8])
    if use_gemini():
        return gemini_generate(
            f"Buat 5 ide thumbnail untuk video '{title}' dalam bahasa Indonesia. "
            f"Setiap ide berupa 1 baris: konsep visual + gaya + komposisi + teks singkat (≤3 kata). "
            f"Berikan juga 1 prompt contoh generatif (Midjourney-style) per ide. Kata kunci: {kw}."
        )
    # fallback
    ideas = [
        f"Close-up objek utama + teks 2 kata kuat\nPrompt: ultra-detailed close-up, dramatic lighting, high contrast, bold 2-word overlay",
        f"Before/After split screen\nPrompt: split-screen comparison, left dull, right vibrant, cinematic, 16:9, bold arrow",
        f"Wajah ekspresif menunjuk objek\nPrompt: person pointing, surprised face, shallow depth, crisp text label",
        f"Minimalis ikon + latar kontras\nPrompt: flat icon center, vivid gradient background, clean typography",
        f"Diagram sederhana 3 langkah\nPrompt: step-by-step infographic, large numbers 1-2-3, bright colors"
    ]
    return "\n\n".join(ideas)

def ai_seo_tags(v):
    base_text = (v["title"] + " " + v.get("description","")).lower()
    words = [w for w in re.split(r"[^\w]+", base_text) if len(w)>=3 and w not in STOPWORDS]
    uniq = list(dict.fromkeys(words))[:40]
    fallback = ", ".join(uniq)[:500]
    if use_gemini():
        text = gemini_generate(
            f"Buat daftar tag SEO (dipisahkan koma, max 500 karakter) untuk video YouTube berbahasa Indonesia. "
            f"Gunakan kata kunci dari judul & deskripsi berikut:\n\nJudul: {v['title']}\nDeskripsi: {v.get('description','')[:1500]}\n"
        )
        return text if text else fallback
    return fallback

# ---------------- Handle submit: fetch & store ----------------
if submit:
    if not keyword.strip():
        st.info("📈 Menampilkan trending (default US)")
        videos_all = get_trending(st.session_state.api_key, st.session_state.get("max_per_order", 15))
    else:
        st.info(f"🔎 Riset keyword: {keyword}")
        order = map_sort_option(sort_option)
        ids = yt_search_ids(st.session_state.api_key, keyword, order, st.session_state.get("max_per_order", 15))
        videos_all = yt_videos_detail(st.session_state.api_key, ids)

    videos_all = filter_by_video_type(videos_all, video_type)
    videos_all = apply_client_sort(videos_all, sort_option)
    st.session_state.last_results = videos_all

    # Auto ideas (opsional)
    st.session_state.auto_ideas = None
    if videos_all and use_gemini():
        try:
            top_titles = [v["title"] for v in videos_all[:5]]
            titles_text = "\n".join([f"- {t}" for t in top_titles])
            keywords = []
            for t in top_titles:
                for w in re.split(r"[^\w]+", t.lower()):
                    if len(w) >= 4 and w not in STOPWORDS:
                        keywords.append(w)
            derived_kw = ", ".join(sorted(set(keywords))[:10])
            short_count = sum(1 for v in videos_all if v.get("duration_sec", 0) <= 60)
            live_count = sum(1 for v in videos_all if v.get("live", "none") == "live")
            regular_count = len(videos_all) - short_count - live_count
            if short_count > max(live_count, regular_count): fmt = "Short (≤60 detik)"
            elif live_count > max(short_count, regular_count): fmt = "Live Streaming"
            else: fmt = "Video Reguler (5–30 menit)"
            st.session_state.auto_ideas = gemini_generate(
                f"Berdasarkan judul-judul:\n{titles_text}\n\nKata kunci turunan: {derived_kw}\n"
                f"Jenis konten dominan: {fmt}\nBuatkan 5 ide video lengkap dengan SIAPA/APA/BAGAIMANA dan IDE VISUAL (1 per ide)."
            )
        except Exception as e:
            st.session_state.auto_ideas = f"❌ Error Gemini: {e}"

# ---------------- Render results ----------------
videos_to_show = st.session_state.last_results

# CSS sederhana (badge & link-like title)
st.markdown("""
<style>
.badge { position:absolute; top:8px; left:8px; color:white; padding:2px 6px; font-size:12px;
         border-radius:4px; font-weight:700; }
.badge-live { background:#e53935; }
.badge-short{ background:#1e88e5; }
.thumbwrap { position:relative; }
img.thumb { width:100%; border-radius:10px; display:block; }
.linklike > button { background:none !important; border:none !important; padding:0 !important;
                     color:#1f6feb; text-decoration:none; font-weight:600; cursor:pointer; }
.linklike > button:hover { text-decoration:underline; }
</style>
""", unsafe_allow_html=True)

if videos_to_show:
    cols = st.columns(3)
    all_titles, rows_for_csv = [], []
    for i, v in enumerate(videos_to_show):
        with cols[i % 3]:
            # Thumbnail (display)
            badge_html = ""
            if v.get("live") == "live": badge_html = '<div class="badge badge-live">LIVE</div>'
            elif v.get("duration_sec", 0) <= 60: badge_html = '<div class="badge badge-short">SHORT</div>'
            if v.get("thumbnail"):
                st.markdown(f'<div class="thumbwrap">{badge_html}<img class="thumb" src="{v["thumbnail"]}"></div>',
                            unsafe_allow_html=True)

            # Title as link-like button → open popup (no navigation)
            st.markdown('<div class="linklike">', unsafe_allow_html=True)
            if st.button(v["title"], key=f"title_btn_{i}", help="Klik untuk preview"):
                st.session_state.popup_video = v
            st.markdown('</div>', unsafe_allow_html=True)

            st.caption(v["channel"])
            c1, c2, c3 = st.columns(3)
            with c1: st.markdown(f"<div style='font-size:13px;background:#ff4b4b;color:white;padding:3px 8px;border-radius:8px;'>👁 {format_views(v['views'])}</div>", unsafe_allow_html=True)
            with c2: st.markdown(f"<div style='font-size:13px;background:#4b8bff;color:white;padding:3px 8px;border-radius:8px;'>⚡ {v['vph']}</div>", unsafe_allow_html=True)
            with c3: st.markdown(f"<div style='font-size:13px;background:#4caf50;color:white;padding:3px 8px;border-radius:8px;'>⏱ {format_rel_time(v['publishedAt'])}</div>", unsafe_allow_html=True)
            st.caption(f"📅 {format_jam_utc(v['publishedAt'])} • ⏳ {v.get('duration','-')}")

        all_titles.append(v["title"])
        rows_for_csv.append({
            "Judul": v["title"], "Channel": v["channel"], "Views": v["views"], "VPH": v["vph"],
            "Tanggal (relatif)": format_rel_time(v["publishedAt"]), "Jam Publish (UTC)": format_jam_utc(v["publishedAt"]),
            "Durasi": v.get("duration","-"), "Link": f"https://www.youtube.com/watch?v={v['id']}"
        })

    # -------- Popup detail + AI tools --------
    if st.session_state.popup_video:
        v = st.session_state.popup_video
        vid = v["id"]
        st.markdown("---")
        st.subheader("📺 Video Detail")
        st.video(f"https://www.youtube.com/watch?v={vid}")
        st.markdown(f"### {v['title']}")
        st.caption(v["channel"])
        st.write(v.get("description", "Tidak ada deskripsi."))

        st.subheader("✨ Asisten Konten AI")

        # helper cache accessors
        def cache_get(task): return st.session_state.ai_cache.get(vid, {}).get(task)
        def cache_set(task, text):
            st.session_state.ai_cache.setdefault(vid, {})[task] = text

        c1, c2 = st.columns(2)

        with c1:
            if st.button("🧾 Ringkas Video Ini", key=f"btn_summary_{vid}"):
                cache_set("summary", ai_summary(v))
            if cache_get("summary"):
                st.markdown(cache_get("summary"))

            if st.button("📝 Buat Kerangka Skrip", key=f"btn_script_{vid}"):
                cache_set("script", ai_script_outline(v))
            if cache_get("script"):
                st.markdown(cache_get("script"))

            if st.button("🔑 Buat Tag SEO", key=f"btn_tags_{vid}"):
                cache_set("tags", ai_seo_tags(v))
            if cache_get("tags"):
                st.text_area("Tag SEO", cache_get("tags"), height=120, key=f"tags_area_{vid}")

        with c2:
            if st.button("✍️ Buat Judul Alternatif", key=f"btn_titles_{vid}"):
                cache_set("alt_titles", ai_alt_titles(v))
            if cache_get("alt_titles"):
                st.markdown(cache_get("alt_titles"))

            if st.button("🖼️ Buat Ide Thumbnail", key=f"btn_thumb_{vid}"):
                cache_set("thumbs", ai_thumb_ideas(v))
            if cache_get("thumbs"):
                st.markdown(cache_get("thumbs"))

        if v.get("channelId"):
            st.markdown(f"[🌐 Kunjungi Channel YouTube](https://www.youtube.com/channel/{v['channelId']})")

        if st.button("❌ Tutup", key="close_popup"):
            st.session_state.popup_video = None
            st.rerun()

    # -------- Rekomendasi Judul & Tag --------
    st.subheader("💡 Rekomendasi Judul (10 Judul, ≤100 Karakter)")
    rec_titles = generate_titles_from_data(videos_to_show, st.session_state.get("sort_option", "Paling Relevan"))
    for idx, rt in enumerate(rec_titles, 1):
        col1, col2, col3 = st.columns([6, 1, 1])
        with col1: st.text_input(f"Judul {idx}", rt, key=f"judul_{idx}")
        with col2: st.markdown(f"<span style='font-size:12px;color:gray'>{len(rt)}/100</span>", unsafe_allow_html=True)
        with col3: st.button("📋", key=f"copy_judul_{idx}", on_click=lambda t=rt: st.session_state.update({"copied": t}))
    if "copied" in st.session_state:
        st.success(f"Judul tersalin: {st.session_state['copied']}")
        st.session_state.pop("copied")

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

    # -------- Downloads --------
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
else:
    st.info("Mulai dengan melakukan pencarian di tab 🔍, lalu klik judul untuk membuka preview. Tombol Asisten Konten AI ada di dalam popup.")
