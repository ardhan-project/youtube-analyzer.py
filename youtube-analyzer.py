import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import re
import io
import zipfile
import html as html_lib
from statistics import mean, median
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from collections import Counter

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
if "gemini_model" not in st.session_state: st.session_state.gemini_model = "gemini-1.5-flash-8b"
if "gemini_blocked" not in st.session_state: st.session_state.gemini_blocked = False
if "gemini_last_error" not in st.session_state: st.session_state.gemini_last_error = ""
if "auto_ideas" not in st.session_state: st.session_state.auto_ideas = None
if "last_results" not in st.session_state: st.session_state.last_results = []
if "popup_video" not in st.session_state: st.session_state.popup_video = None
if "ai_cache" not in st.session_state: st.session_state.ai_cache = {}   # {video_id: {task: text}}
if "keyword_input" not in st.session_state: st.session_state.keyword_input = ""

# Info banner kalau quota sudah keblok
if st.session_state.get("gemini_blocked"):
    st.info("ℹ️ Fitur Gemini dibatasi hari ini (quota tercapai). App otomatis pakai fallback lokal agar tetap jalan.")

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("⚙️ Pengaturan")
    api_key = st.text_input("YouTube Data API Key", st.session_state.api_key, type="password", key="yt_api_key")
    gemini_api = st.text_input("Gemini API Key (Opsional)", st.session_state.gemini_api, type="password", key="gemini_api_key")
    gemini_model = st.selectbox("Gemini Model", ["gemini-1.5-flash-8b", "gemini-1.5-flash", "gemini-1.5-pro"], index=0, key="gemini_model")
    st.caption("Belum punya Gemini API Key? 👉 [Buat di sini](https://aistudio.google.com/app/apikey)")
    max_per_order = st.slider("Jumlah video per kategori", 5, 30, 15, 1, key="max_per_order")
    if st.button("Simpan", key="save_api"):
        st.session_state.api_key = api_key
        st.session_state.gemini_api = gemini_api
        st.success("🔑 API Key & Model berhasil disimpan!")

if not st.session_state.api_key:
    st.warning("⚠️ Masukkan API Key di sidebar untuk mulai")
    st.stop()

# ---------------- Tabs ----------------
tab1, tab2 = st.tabs(["🔍 Cari Video", "💡 Ide Video"])

with tab1:
    with st.form("youtube_form"):
        keyword = st.text_input("Kata Kunci (kosongkan untuk Trending)", placeholder="healing flute meditation", key="keyword_form_input")
        # urutan opsi disesuaikan: VPH, Terbaru, Views, Relevan
        sort_option = st.selectbox("Urutkan:", ["VPH Tertinggi", "Terbaru", "Paling Banyak Ditonton", "Paling Relevan"], key="sort_option")
        video_type = st.radio("Tipe Video", ["Semua", "Regular", "Short", "Live"], horizontal=True, key="video_type")
        submit = st.form_submit_button("🔍 Cari Video", key="search_video")

with tab2:
    st.subheader("💡 Rekomendasi Ide Video (otomatis dari hasil pencarian)")

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

def asia_jakarta_hour(dt_utc_str: str) -> int | None:
    try:
        dt = datetime.strptime(dt_utc_str,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if ZoneInfo:
            return dt.astimezone(ZoneInfo("Asia/Jakarta")).hour
        return ((dt.hour + 7) % 24)
    except:
        return None

# ---------- Lang detect (ID/EN) ----------
IND_HINT = {
    "yang","dan","di","ke","dari","untuk","pada","kami","kamu","anda","saja","bisa","tidak","cara","apa",
    "bagaimana","mengapa","gratis","terbaru","banget","sangat","dengan","tanpa","lebih","menjadi","agar","supaya"
}
ENG_HINT = {
    "the","and","for","with","to","from","you","your","how","why","what","best","guide","review","tips",
    "tricks","new","free","without","vs","top","in","on","of"
}
def detect_lang(text: str) -> str:
    t = text.lower()
    toks = re.findall(r"\w+", t)
    id_score = sum(1 for w in toks if (w in IND_HINT) or w.startswith(("meng","men","mem","me","ber","ter","per","se")))
    en_score = sum(1 for w in toks if w in ENG_HINT)
    return "id" if id_score >= en_score else "en"

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

# ---------------- Relevance & helpers ----------------
def _tokenize(txt: str):
    return [w for w in re.split(r"[^\w]+", (txt or "").lower()) if len(w) >= 3 and w not in STOPWORDS]

def relevance_score(title: str, desc: str, keyword: str) -> int:
    if not keyword: return 0
    q = set(_tokenize(keyword))
    if not q: return 0
    doc = _tokenize((title or "") + " " + (desc or ""))
    overlap = sum(1 for w in doc if w in q)
    all_match_bonus = 5 if q.issubset(set(doc)) else 0
    return overlap + all_match_bonus

def pub_ts(v):
    try:
        return datetime.strptime(v.get("publishedAt",""), "%Y-%m-%dT%H:%M:%SZ").timestamp()
    except:
        return 0.0

# ---------------- Sort & Filter ----------------
def map_sort_option(sort_option: str):
    if sort_option == "Paling Banyak Ditonton": return "viewCount"
    if sort_option == "Terbaru": return "date"
    if sort_option == "Paling Relevan": return "relevance"
    if sort_option == "VPH Tertinggi": return "date"
    return "relevance"

def apply_client_sort(items, sort_option: str, keyword: str = ""):
    if sort_option == "VPH Tertinggi":
        return sorted(items, key=lambda x: (
            x.get("vph", 0.0),
            pub_ts(x),
            x.get("views", 0),
            relevance_score(x.get("title",""), x.get("description",""), keyword)
        ), reverse=True)
    if sort_option == "Terbaru":
        return sorted(items, key=lambda x: (
            pub_ts(x),
            x.get("vph", 0.0),
            x.get("views", 0),
            relevance_score(x.get("title",""), x.get("description",""), keyword)
        ), reverse=True)
    if sort_option == "Paling Banyak Ditonton":
        return sorted(items, key=lambda x: (
            x.get("views", 0),
            x.get("vph", 0.0),
            pub_ts(x),
            relevance_score(x.get("title",""), x.get("description",""), keyword)
        ), reverse=True)
    if sort_option == "Paling Relevan":
        return sorted(items, key=lambda x: (
            relevance_score(x.get("title",""), x.get("description",""), keyword),
            x.get("vph", 0.0),
            pub_ts(x),
            x.get("views", 0)
        ), reverse=True)
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
        sorted_videos = sorted(videos, key=lambda x: pub_ts(x), reverse=True)
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

def gemini_generate(prompt: str, retries: int = 1) -> str:
    if not use_gemini() or st.session_state.get("gemini_blocked", False):
        return ""
    try:
        import google.generativeai as genai
        genai.configure(api_key=st.session_state.gemini_api)
        model_name = st.session_state.get("gemini_model", "gemini-1.5-flash-8b")
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(prompt)
        return resp.text if hasattr(resp, "text") and resp.text else ""
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower():
            st.session_state["gemini_blocked"] = True
            st.session_state["gemini_last_error"] = "Batas harian Gemini tercapai. Menggunakan fallback lokal."
            return ""
        if retries > 0:
            return gemini_generate(prompt, retries - 1)
        st.session_state["gemini_last_error"] = msg
        return ""

def content_type(v):
    if v.get("live") == "live": return "Live"
    if v.get("duration_sec", 0) <= 60: return "Short"
    return "Regular"

# ---------------- AI tasks ----------------
def ai_summary(v):
    title, desc, ch = v["title"], v.get("description",""), v.get("channel","")
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        res = gemini_generate(
            f"Ringkas video YouTube berikut menjadi 5 poin bullet berbahasa Indonesia, fokus manfaat untuk penonton, hindari klaim berlebihan.\n"
            f"Judul: {title}\nChannel: {ch}\nDeskripsi:\n{desc[:3000]}"
        )
        if res: return res
    sentences = re.split(r'(?<=[.!?])\s+', desc)[:5]
    if not sentences: sentences = [title]
    bullets = "\n".join(f"- {s}" for s in sentences)
    return f"**Ringkasan (fallback lokal)**\n{bullets}"

def ai_alt_titles(v):
    ct = content_type(v)
    lang = detect_lang(v["title"])
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        if lang == "en":
            res = gemini_generate(
                f"Write 10 alternative YouTube titles (≤100 chars) in ENGLISH for the video '{v['title']}'. "
                f"Keep the same topic. Mix styles: numbers, brackets, questions, power words. "
                f"Content format: {ct}. Output as a numbered list."
            )
        else:
            res = gemini_generate(
                f"Buat 10 judul alternatif YouTube (≤100 karakter) dalam BAHASA INDONESIA untuk video '{v['title']}'. "
                f"Sesuai topik asli. Variasikan gaya (angka, kurung, pertanyaan). "
                f"Format konten: {ct}. Tulis sebagai daftar bernomor."
            )
        if res: return res
    base = v["title"]
    if lang == "en":
        variants = [
            trim_to_100(base),
            trim_to_100(f"{base} | Full Guide"),
            trim_to_100(f"{base} (Tips & Tricks)"),
            trim_to_100(f"{base}: Step-by-Step"),
            trim_to_100(f"Master {base} in Minutes"),
            trim_to_100(f"{base} for Beginners"),
            trim_to_100(f"{base} Explained!"),
            trim_to_100(f"Top 5 {base} Hacks"),
            trim_to_100(f"{base} [2025 Update]"),
            trim_to_100(f"Why {base}? The Truth")
        ]
    else:
        variants = [
            trim_to_100(base),
            trim_to_100(f"{base} | Panduan Lengkap"),
            trim_to_100(f"{base} (Tips & Trik)"),
            trim_to_100(f"{base}: Langkah demi Langkah"),
            trim_to_100(f"Kuasi {base} dalam Hitungan Menit"),
            trim_to_100(f"{base} untuk Pemula"),
            trim_to_100(f"{base} Tuntas!"),
            trim_to_100(f"5 Trik {base} Teratas"),
            trim_to_100(f"{base} [Update 2025]"),
            trim_to_100(f"Kenapa {base}? Ini Alasannya")
        ]
    return "\n".join(f"{i+1}. {t}" for i, t in enumerate(variants))

def ai_script_outline(v):
    ct = content_type(v)
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        res = gemini_generate(
            f"Buat kerangka skrip YouTube berbahasa Indonesia untuk '{v['title']}'. "
            f"Format: {ct}. Sertakan: HOOK, Intro, 3–6 bagian utama, CTA. "
            f"Untuk Short ≤60 detik; untuk Live tambahkan segmen (pembuka, agenda, interaksi chat, checkpoint, closing)."
        )
        if res: return res
    if ct == "Short":
        return "HOOK (0-3s) → INTI cepat (3-50s, 3 poin) → CTA (50-60s)"
    if ct == "Live":
        return "Opening • Agenda • Interaksi Chat • Checkpoint • Closing"
    return "Hook → Intro → 3 Bagian → Rekap → CTA"

def ai_thumb_ideas(v):
    title = v["title"]
    kw = ", ".join(sorted({w for w in re.split(r"[^\w]+", (title + " " + v.get('description','')).lower())
                           if len(w)>=4 and w not in STOPWORDS})[:8])
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        res = gemini_generate(
            f"Buat 5 ide thumbnail berbahasa Indonesia untuk '{title}'. Setiap ide 1 baris: konsep + gaya + komposisi + teks ≤3 kata. "
            f"Sertakan 1 prompt generatif per ide (gaya Midjourney). Kata kunci: {kw}."
        )
        if res: return res
    ideas = [
        f"Close-up objek utama + teks 2 kata\nPrompt: ultra-detailed close-up, dramatic lighting, high contrast, bold 2-word overlay",
        f"Before/After split screen\nPrompt: split-screen comparison, left dull, right vibrant, cinematic, 16:9, bold arrow",
        f"Wajah ekspresif menunjuk objek\nPrompt: person pointing, surprised face, shallow depth, crisp text label",
        f"Minimalis ikon + latar kontras\nPrompt: flat icon center, vivid gradient background, clean typography",
        f"Diagram sederhana 3 langkah\nPrompt: step-by-step infographic, large numbers 1-2-3, bright colors"
    ]
    return "\n\n".join(ideas)

def ai_seo_tags(v):
    title = v["title"]
    desc = v.get("description","")
    lang = detect_lang(title)
    base_text = (title + " " + desc).lower()
    words = [w for w in re.split(r"[^\w]+", base_text) if len(w)>=3 and w not in STOPWORDS]
    uniq = list(dict.fromkeys(words))[:40]
    fallback = ", ".join(uniq)[:500]
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        if lang == "en":
            text = gemini_generate(
                "Generate comma-separated YouTube SEO tags in ENGLISH (≤500 chars). "
                f"Use keywords from the title/description.\nTitle: {title}\nDescription: {desc[:1500]}"
            )
        else:
            text = gemini_generate(
                "Buat daftar tag SEO YouTube berbahasa INDONESIA (dipisahkan koma, ≤500 karakter). "
                f"Gunakan kata kunci dari judul/deskripsi.\nJudul: {title}\nDeskripsi: {desc[:1500]}"
            )
        return text if text else fallback
    return fallback

# ---------------- Niche summary (untuk Tab Ide) ----------------
def relevant_videos(videos, keyword):
    rel = [v for v in videos if relevance_score(v.get("title",""), v.get("description",""), keyword) > 0]
    return rel if rel else videos

def format_share(videos):
    s = sum(1 for v in videos if v.get("duration_sec",0) <= 60 and v.get("live","none")=="none")
    l = sum(1 for v in videos if v.get("live","none")=="live")
    r = len(videos) - s - l
    return s, l, r

def core_tokens(videos, topn=12):
    allw=[]
    for v in videos:
        allw += _tokenize(v.get("title","")) + _tokenize(v.get("description",""))
    cnt = Counter(w for w in allw if w not in STOPWORDS)
    return [w for w,_ in cnt.most_common(topn)]

def format_label_from_tokens(tokens:set):
    med_keys = {"432hz","meditation","meditasi","sleep","tidur","calm","relax","healing","anxiety","buddha","chakra","zen","mantra","sound","frequency"}
    if tokens & med_keys:
        return "Meditasi / Healing Music 432Hz"
    return "Niche berdasarkan kata kunci"

def publish_hour_stats(videos):
    hours=[]
    for v in videos:
        h = asia_jakarta_hour(v.get("publishedAt",""))
        if h is not None: hours.append(h)
    if not hours:
        return {"avg": None, "top": []}
    avg_h = round(mean(hours))
    top = Counter(hours).most_common(3)
    return {"avg": avg_h, "top": top}

def views_stats(videos):
    vs=[int(v.get("views",0)) for v in videos if isinstance(v.get("views",0), int)]
    vph=[float(v.get("vph",0.0)) for v in videos]
    if not vs:
        return {"avg":0,"med":0,"vph":0.0,"n":0}
    return {"avg": int(mean(vs)), "med": int(median(vs)), "vph": round(mean(vph),2), "n": len(vs)}

def window_hour(h): return f"{h:02d}:00–{(h+1)%24:02d}:59"

def render_niche_summary(videos, keyword: str) -> str:
    vids = relevant_videos(videos, keyword)
    s,l,r = format_share(vids)
    tokens = set(core_tokens(vids, topn=12))
    label = format_label_from_tokens(tokens)
    hrs = publish_hour_stats(vids)
    stat = views_stats(vids)

    if "432hz" in tokens or "meditation" in tokens or "meditasi" in tokens:
        siapa = (
            "- Usia: 18–44\n"
            "- Gender: Campuran\n"
            "- Lokasi: Global (WIB kuat untuk pasar ID)\n"
            "- Status: Mahasiswa/pekerja, pencari ketenangan\n"
            "- Masalah: Stres, sulit tidur, susah fokus\n"
            "- Harapan: Tenang, tidur lelap, energi positif"
        )
        apa = (
            "- Kebutuhan/Minat: Musik meditasi, suara alam, 432Hz, chakra, fokus belajar\n"
            "- Jenis Konten: Track 10–60 mnt, panduan napas singkat, live relaksasi mingguan"
        )
        bagaimana = (
            "- Gaya: Tenang, minim bicara, visual menenangkan\n"
            "- Bentuk: Reguler (mix panjang), Shorts (teknik 60 detik), Live (Q&A & breathing)\n"
            "- Durasi: 30–60 mnt (reguler) / ≤60 dtk (short)\n"
            "- Frekuensi: 3–5x/minggu + 1 live/minggu"
        )
    else:
        siapa = (
            "- Usia: 18–40\n- Gender: Campuran\n- Lokasi: Global\n- Status: Pemula di niche\n"
            "- Masalah: Kurang referensi & struktur belajar\n- Harapan: Panduan ringkas & jelas"
        )
        apa = "- Kebutuhan/Minat: Tutorial praktis, ringkasan topik, rekomendasi alat\n- Jenis Konten: How-to, listicle, live Q&A"
        bagaimana = "- Gaya: Langsung ke poin • Bentuk: Reguler/Short • Durasi: 5–15 mnt (reg) / ≤60 dtk (short) • Frekuensi: 3x/minggu"

    if hrs["top"]:
        top_list = ", ".join(f"{h:02d} (n={c})" for h,c in hrs["top"])
        saran = ", ".join(window_hour(h) for h,_ in hrs["top"][:2])
        jam_md = f"**Rata-rata:** {hrs['avg']:02d}:00 WIB • **Puncak:** {top_list}\n**Saran upload:** {saran}"
    else:
        jam_md = "Data jam publish tidak cukup."

    total = max(len(vids),1)
    fmt_md = f"Short: {s} • Live: {l} • Reguler: {r} (total {total})"
    tok_md = ", ".join(sorted(list(tokens))[:12])

    bullets = [
        f"Niche: **{label}** • Format dominan → {('Reguler' if r>=max(s,l) else 'Short' if s>=max(l,r) else 'Live')}",
        f"Sampel: **{stat['n']}** video • Rata-rata views **{format_views(stat['avg'])}** • Median **{format_views(stat['med'])}** • VPH rata-rata **{stat['vph']}**",
        f"Topik kunci: {tok_md}",
        f"Waktu publish efektif (WIB): {jam_md}",
        "Strategi: jaga konsistensi format dominan + selingi variasi yang cepat perform (Short/Live)."
    ]

    md = (
        "### 📊 Ringkasan Niche (otomatis)\n"
        f"- **Label:** {label}\n"
        f"- **Distribusi Format:** {fmt_md}\n\n"
        "### 🧠 SIAPA (Target)\n" + siapa + "\n\n"
        "### 📚 APA (Minat)\n" + apa + "\n\n"
        "### 🎯 BAGAIMANA (Eksekusi)\n" + bagaimana + "\n\n"
        f"### 🕒 Rata-rata Jam Publish (WIB)\n{jam_md}\n\n"
        "### 📈 Metrik Ringkas\n"
        f"- Sampel: **{stat['n']}**\n"
        f"- Rata-rata Views: **{format_views(stat['avg'])}** • Median: **{format_views(stat['med'])}**\n"
        f"- VPH rata-rata: **{stat['vph']}**\n\n"
        "### 📌 Rangkuman Ketat\n"
        + "\n".join(f"- {b}" for b in bullets)
    )
    return md

# ---------------- Handle submit: fetch & store ----------------
if submit:
    st.session_state.keyword_input = keyword
    if not keyword.strip():
        st.info("📈 Menampilkan trending (default US)")
        videos_all = get_trending(st.session_state.api_key, st.session_state.get("max_per_order", 15))
    else:
        st.info(f"🔎 Riset keyword: {keyword}")
        order = map_sort_option(sort_option)
        ids = yt_search_ids(st.session_state.api_key, keyword, order, st.session_state.get("max_per_order", 15))
        videos_all = yt_videos_detail(st.session_state.api_key, ids)

    videos_all = filter_by_video_type(videos_all, st.session_state.get("video_type","Semua"))
    videos_all = apply_client_sort(videos_all, sort_option, st.session_state.keyword_input)
    st.session_state.last_results = videos_all

    # Auto IDE (tetap Indonesia) – skip Gemini jika blocked/quota
    st.session_state.auto_ideas = None
    if videos_all and use_gemini() and not st.session_state.get("gemini_blocked", False):
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
            ) or ""
        except Exception:
            st.session_state.auto_ideas = ""

    # Fallback lokal agar tab ide tetap berguna jika kosong/blocked
    if (st.session_state.auto_ideas is None) or (st.session_state.auto_ideas.strip() == ""):
        if st.session_state.last_results:
            sample = st.session_state.last_results[:5]
            fmt_dom = "Short (≤60 detik)" if sum(1 for v in sample if v.get("duration_sec",0)<=60) > 2 else \
                      "Live Streaming" if sum(1 for v in sample if v.get("live","none")=="live") > 2 else "Video Reguler (5–30 menit)"
            kws = []
            for v in sample:
                for w in re.split(r"[^\w]+", v["title"].lower()):
                    if len(w) >= 4 and w not in STOPWORDS:
                        kws.append(w)
            kws = ", ".join(sorted(set(kws))[:10])
            st.session_state.auto_ideas = (
                f"**Format dominan:** {fmt_dom}\n\n"
                f"**Kata kunci turunan:** {kws}\n\n"
                "### 5 Ide Video (fallback lokal)\n"
                "1) **SIAPA**: Pemula • **APA**: Panduan cepat • **BAGAIMANA**: 3 langkah praktis • **Visual**: close-up + teks 2 kata\n"
                "2) **SIAPA**: Pekerja sibuk • **APA**: Trik hemat waktu • **BAGAIMANA**: tips 1 menit • **Visual**: before/after split\n"
                "3) **SIAPA**: Konten kreator • **APA**: Optimasi judul & tag • **BAGAIMANA**: checklist • **Visual**: ikon + gradient\n"
                "4) **SIAPA**: Penonton live • **APA**: Q&A topik tren • **BAGAIMANA**: rundown segmen • **Visual**: agenda + emoji chat\n"
                "5) **SIAPA**: Pemula editing • **APA**: Efek instan • **BAGAIMANA**: step-by-step • **Visual**: infografik 1-2-3\n"
            )

# ---------------- CSS (YouTube-like) ----------------
st.markdown("""
<style>
/* --- YouTube-like card --- */
.yt-card{
  display:flex; flex-direction:column; gap:8px;
  padding: 4px; border-radius: 12px;
}
.yt-thumb{ position:relative; border-radius:12px; overflow:hidden;
  aspect-ratio: 16/9; background:#101114; }
.yt-thumb img{ width:100%; height:100%; object-fit:cover; display:block; }

/* badges */
.yt-badge{ position:absolute; right:8px; bottom:8px;
  background: rgba(0,0,0,.85); color:#fff; font-size:12px; padding:2px 6px; border-radius:4px; }
.yt-badge.live{ left:8px; right:auto; top:8px; bottom:auto; background:#cc0000; font-weight:700; }
.yt-badge.short{ left:8px; right:auto; top:8px; bottom:auto; background:#1e88e5; }

/* title button looks like link, clamp 2 lines */
.yt-title{ margin-top:4px; }
.yt-title > button{
  background:none !important; border:none !important; padding:0 !important;
  text-align:left; width:100%; cursor:pointer;
  color:#e6e6e6; font-weight:600; font-size:15px; line-height:1.25;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
}
.yt-title > button:hover{ text-decoration:underline; }

/* channel + meta */
.yt-channel{ color:#9aa0a6; font-size:13px; }
.yt-meta{ color:#9aa0a6; font-size:12px; }
.yt-dot::before{ content:"•"; margin:0 .35rem; color:#9aa0a6; }

/* full width buttons inside dialog */
.dialog-actions > div > button{ width:100%; }
</style>
""", unsafe_allow_html=True)

# ---------------- Dialog (modal) popup ----------------
HAS_DIALOG = hasattr(st, "dialog")

if HAS_DIALOG:
    @st.dialog("📺 Video Preview")
    def video_preview_dialog():
        v = st.session_state.get("popup_video")
        if not v:
            st.write("Tidak ada video.")
            return
        vid = v["id"]
        yt_url = f"https://www.youtube.com/watch?v={vid}"
        ch_url = f"https://www.youtube.com/channel/{v.get('channelId','')}" if v.get("channelId") else None

        # Header: title + chips
        st.markdown(f"### {v['title']}")
        chips = (
            f"<span class='chip chip-views'>👁 {format_views(v['views'])}</span>"
            f"<span class='chip chip-vph'>⚡ {v['vph']}</span>"
            f"<span class='chip chip-time'>⏱ {format_rel_time(v['publishedAt'])}</span>"
            f"<span class='chip chip-dur'>⏳ {v.get('duration','-')}</span>"
        )
        st.markdown(chips, unsafe_allow_html=True)

        # two columns: video + actions
        c1, c2 = st.columns([2,1])
        with c1:
            st.video(yt_url)
        with c2:
            st.markdown("#### Aksi")
            st.link_button("▶️ Buka di YouTube", yt_url, use_container_width=True)
            if ch_url:
                st.link_button("🌐 Kunjungi Channel", ch_url, use_container_width=True)
            st.text_input("Link Video", yt_url, key=f"copy_url_{vid}")
            st.markdown("---")
            st.caption(f"Channel: {v['channel']}")
            if v.get("live") == "live":
                st.success("🔴 LIVE content terdeteksi")
            elif v.get("duration_sec",0) <= 60:
                st.info("🟦 SHORT (≤60 detik)")
            else:
                st.caption("📼 Video reguler")

        # Tabs inside dialog
        t1, t2, t3 = st.tabs(["ℹ️ Info", "✨ Asisten Konten AI", "📈 Analytics"])
        with t1:
            with st.expander("Deskripsi", expanded=False):
                st.write(v.get("description","Tidak ada deskripsi."))
            st.caption(f"Publish: {format_jam_utc(v['publishedAt'])} • ID: {vid}")

        def cache_get(task): return st.session_state.ai_cache.get(vid, {}).get(task)
        def cache_set(task, text):
            st.session_state.ai_cache.setdefault(vid, {})[task] = text

        with t2:
            a1, a2 = st.columns(2)
            with a1:
                if st.button("🧾 Ringkas Video Ini", key=f"d_summary_{vid}"):
                    cache_set("summary", ai_summary(v))
                if cache_get("summary"): st.markdown(cache_get("summary"))

                if st.button("🔑 Buat Tag SEO", key=f"d_tags_{vid}"):
                    cache_set("tags", ai_seo_tags(v))
                if cache_get("tags"): st.text_area("Tag SEO", cache_get("tags"), height=120, key=f"d_tags_area_{vid}")
            with a2:
                if st.button("📝 Buat Kerangka Skrip", key=f"d_script_{vid}"):
                    cache_set("script", ai_script_outline(v))
                if cache_get("script"): st.markdown(cache_get("script"))

                if st.button("✍️ Buat Judul Alternatif", key=f"d_titles_{vid}"):
                    cache_set("alt_titles", ai_alt_titles(v))
                if cache_get("alt_titles"): st.markdown(cache_get("alt_titles"))

                if st.button("🖼️ Ide Thumbnail", key=f"d_thumb_{vid}"):
                    cache_set("thumbs", ai_thumb_ideas(v))
                if cache_get("thumbs"): st.markdown(cache_get("thumbs"))

        with t3:
            st.write("**Metrik Video**")
            colm = st.columns(4)
            colm[0].metric("Views", format_views(v["views"]))
            colm[1].metric("VPH", v["vph"])
            colm[2].metric("Durasi", v.get("duration","-"))
            colm[3].metric("Publish (rel)", format_rel_time(v["publishedAt"]))

        st.markdown("---")
        if st.button("❌ Tutup", key="close_dialog"):
            st.session_state.popup_video = None
            st.rerun()

# ---------------- Render results (YouTube-like cards) ----------------
videos_to_show = st.session_state.last_results

if videos_to_show:
    cols = st.columns(3)
    all_titles, rows_for_csv = [], []

    for i, v in enumerate(videos_to_show):
        with cols[i % 3]:
            st.markdown("<div class='yt-card'>", unsafe_allow_html=True)

            # THUMBNAIL 16:9 + BADGES
            dur_badge = f"<span class='yt-badge'>{v.get('duration','-')}</span>"
            live_badge = "<span class='yt-badge live'>LIVE</span>" if v.get("live")=="live" else ""
            short_badge = "<span class='yt-badge short'>SHORT</span>" if (v.get("duration_sec",0)<=60 and v.get("live")=='none') else ""
            badge_left = live_badge or short_badge  # prioritas LIVE

            if v.get("thumbnail"):
                st.markdown(
                    f"""
                    <div class='yt-thumb'>
                      <img src="{v['thumbnail']}" alt="thumb">
                      {badge_left}
                      {"" if v.get("live")=="live" else dur_badge}
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            # TITLE (klik → popup)
            st.markdown("<div class='yt-title'>", unsafe_allow_html=True)
            if st.button(v["title"], key=f"title_btn_{i}", help="Klik untuk preview"):
                st.session_state.popup_video = v
                if HAS_DIALOG:
                    video_preview_dialog()
            st.markdown("</div>", unsafe_allow_html=True)

            # CHANNEL + META (views • waktu lalu)
            st.markdown(f"<div class='yt-channel'>{v['channel']}</div>", unsafe_allow_html=True)
            meta = f"{format_views(v['views'])} x ditonton <span class='yt-dot'></span> {format_rel_time(v['publishedAt'])}"
            st.markdown(f"<div class='yt-meta'>{meta}</div>", unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)  # end .yt-card

        all_titles.append(v["title"])
        rows_for_csv.append({
            "Judul": v["title"], "Channel": v["channel"], "Views": v["views"], "VPH": v["vph"],
            "Tanggal (relatif)": format_rel_time(v["publishedAt"]), "Jam Publish (UTC)": format_jam_utc(v["publishedAt"]),
            "Durasi": v.get("duration","-"), "Link": f"https://www.youtube.com/watch?v={v['id']}"
        })

    # -------- Fallback inline detail (jika Streamlit belum mendukung st.dialog) --------
    if (not HAS_DIALOG) and st.session_state.popup_video:
        v = st.session_state.popup_video
        vid = v["id"]
        st.markdown("---")
        st.subheader("📺 Video Detail")
        st.video(f"https://www.youtube.com/watch?v={vid}")
        st.markdown(f"### {v['title']}")
        st.caption(v["channel"])
        st.write(v.get("description", "Tidak ada deskripsi."))

        st.subheader("✨ Asisten Konten AI")
        def cache_get(task): return st.session_state.ai_cache.get(vid, {}).get(task)
        def cache_set(task, text): st.session_state.ai_cache.setdefault(vid, {})[task] = text
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🧾 Ringkas Video Ini", key=f"btn_summary_{vid}"):
                cache_set("summary", ai_summary(v))
            if cache_get("summary"): st.markdown(cache_get("summary"))
            if st.button("🔑 Buat Tag SEO", key=f"btn_tags_{vid}"):
                cache_set("tags", ai_seo_tags(v))
            if cache_get("tags"): st.text_area("Tag SEO", cache_get("tags"), height=120, key=f"tags_area_{vid}")
        with c2:
            if st.button("📝 Buat Kerangka Skrip", key=f"btn_script_{vid}"):
                cache_set("script", ai_script_outline(v))
            if cache_get("script"): st.markdown(cache_get("script"))
            if st.button("✍️ Buat Judul Alternatif", key=f"btn_titles_{vid}"):
                cache_set("alt_titles", ai_alt_titles(v))
            if cache_get("alt_titles"): st.markdown(cache_get("alt_titles"))
            if st.button("🖼️ Buat Ide Thumbnail", key=f"btn_thumb_{vid}"):
                cache_set("thumbs", ai_thumb_ideas(v))
            if cache_get("thumbs"): st.markdown(cache_get("thumbs"))
        if v.get("channelId"):
            st.markdown(f"[🌐 Kunjungi Channel YouTube](https://www.youtube.com/channel/{v['channelId']})")
        if st.button("❌ Tutup", key="close_popup"):
            st.session_state.popup_video = None
            st.rerun()

    # -------- Tab Ide: ringkasan niche + ide --------
    with tab2:
        vids = st.session_state.get("last_results", [])
        kw = st.session_state.get("keyword_input", "")
        if vids:
            st.markdown(render_niche_summary(vids, kw))
        else:
            st.info("Belum ada data. Silakan cari video dulu di tab 🔍.")
        if st.session_state.auto_ideas:
            st.markdown(st.session_state.auto_ideas)
        else:
            st.caption("Tips: aktifkan Gemini untuk ide yang lebih variatif. Tanpa Gemini, gunakan ringkasan niche di atas sebagai acuan.")

    # -------- Rekomendasi Judul --------
    st.subheader("💡 Rekomendasi Judul (10 Judul, ≤100 Karakter)")
    rec_titles = generate_titles_from_data(videos_to_show, st.session_state.get("sort_option", "VPH Tertinggi"))
    for idx, rt in enumerate(rec_titles, 1):
        col1, col2, col3 = st.columns([6, 1, 1])
        with col1: st.text_input(f"Judul {idx}", rt, key=f"judul_{idx}")
        with col2: st.markdown(f"<span style='font-size:12px;color:gray'>{len(rt)}/100</span>", unsafe_allow_html=True)
        with col3: st.button("📋", key=f"copy_judul_{idx}", on_click=lambda t=rt: st.session_state.update({"copied": t}))
    if "copied" in st.session_state:
        st.success(f"Judul tersalin: {st.session_state['copied']}")
        st.session_state.pop("copied")

    # -------- Rekomendasi Tag --------
    st.subheader("🏷️ Rekomendasi Tag (max 500 karakter)")
    if st.session_state.popup_video and not HAS_DIALOG:
        vprev = st.session_state.popup_video
        preview_tags = ai_seo_tags(vprev)
        st.text_area("Tag (berdasarkan video yang dipreview)", preview_tags[:500], height=120, key=f"tag_area_preview_{vprev['id']}")
    else:
        uniq_words, seen = [], set()
        for t in all_titles:
            for w in re.split(r"[^\w]+", t.lower()):
                if len(w) >= 3 and w not in STOPWORDS and w not in seen:
                    uniq_words.append(w); seen.add(w)
        tag_string = ", ".join(uniq_words)
        if len(tag_string) > 500: tag_string = tag_string[:497] + "..."
        st.text_area("Tag (gabungan hasil pencarian)", tag_string, height=100, key="tag_area_global")
    col1, col2 = st.columns([8, 1])
    with col2:
        def _copy_current_tags():
            if st.session_state.popup_video and not HAS_DIALOG:
                vprev = st.session_state.popup_video
                st.session_state["copied_tag"] = st.session_state.get(f"tag_area_preview_{vprev['id']}", "")
            else:
                st.session_state["copied_tag"] = st.session_state.get("tag_area_global", "")
        st.button("📋", key="copy_tag_btn", on_click=_copy_current_tags)
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
    st.info("Mulai dengan melakukan pencarian di tab 🔍, lalu klik **judul** untuk membuka popup.")
