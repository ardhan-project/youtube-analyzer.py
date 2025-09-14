import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import re
import io
import zipfile
from statistics import mean, median
from collections import Counter
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

st.set_page_config(page_title="YouTube Trending Explorer", layout="wide")
st.title("🎬 YouTube Trending Explorer")

STOPWORDS = set("""
a an and the for of to in on with from by at as or & | - live official lyrics lyric audio video music mix hour hours relax relaxing study sleep deep best new latest 4k 8k
""".split())

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# ---------------- Session init ----------------
ss = st.session_state
ss.setdefault("api_key", "")
ss.setdefault("gemini_api", "")
ss.setdefault("gemini_model", "gemini-1.5-flash-8b")
ss.setdefault("gemini_blocked", False)
ss.setdefault("gemini_last_error", "")
ss.setdefault("auto_ideas", None)
ss.setdefault("last_results", [])
ss.setdefault("popup_video", None)
ss.setdefault("ai_cache", {})   # {video_id: {task: text}}
ss.setdefault("keyword_input", "")
ss.setdefault("max_per_order", 15)

if ss.get("gemini_blocked"):
    st.info("ℹ️ Fitur Gemini dibatasi hari ini (quota tercapai). App pakai fallback lokal agar tetap jalan.")

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("⚙️ Pengaturan")
    api_key = st.text_input("YouTube Data API Key", ss.api_key, type="password", key="yt_api_key")
    gemini_api = st.text_input("Gemini API Key (Opsional)", ss.gemini_api, type="password", key="gemini_api_key")
    gemini_model = st.selectbox("Gemini Model", ["gemini-1.5-flash-8b", "gemini-1.5-flash", "gemini-1.5-pro"], index=0, key="gemini_model")
    st.caption("Belum punya Gemini API Key? 👉 [Buat di sini](https://aistudio.google.com/app/apikey)")
    ss.max_per_order = st.slider("Jumlah video per kategori", 5, 30, ss.max_per_order, 1, key="max_per_order_slider")
    if st.button("Simpan", key="save_api"):
        ss.api_key = api_key
        ss.gemini_api = gemini_api
        st.success("🔑 API Key & Model tersimpan!")

if not ss.api_key:
    st.warning("⚠️ Masukkan API Key di sidebar untuk mulai")
    st.stop()

# ---------------- Tabs ----------------
tab1, tab2 = st.tabs(["🔍 Cari Video", "💡 Ide Video"])

with tab1:
    with st.form("youtube_form"):
        keyword = st.text_input("Kata Kunci (kosongkan untuk Trending)", placeholder="healing flute meditation", key="keyword_form_input")
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

# ---------- Lang detect ----------
IND_HINT = {"yang","dan","di","ke","dari","untuk","pada","kami","kamu","anda","saja","bisa","tidak","cara","apa","bagaimana","mengapa","gratis","terbaru","banget","sangat","dengan","tanpa","lebih","menjadi","agar","supaya"}
ENG_HINT = {"the","and","for","with","to","from","you","your","how","why","what","best","guide","review","tips","tricks","new","free","without","vs","top","in","on","of"}
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

# ---------------- relevance/sort/filter ----------------
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

def map_sort_option(sort_option: str):
    if sort_option == "Paling Banyak Ditonton": return "viewCount"
    if sort_option == "Terbaru": return "date"
    if sort_option == "Paling Relevan": return "relevance"
    if sort_option == "VPH Tertinggi": return "date"  # ambil terbaru, lalu sort lokal VPH
    return "relevance"

def apply_client_sort(items, sort_option: str, keyword: str = ""):
    if sort_option == "VPH Tertinggi":
        return sorted(items, key=lambda x: (x.get("vph", 0.0), pub_ts(x), x.get("views", 0), relevance_score(x.get("title",""), x.get("description",""), keyword)), reverse=True)
    if sort_option == "Terbaru":
        return sorted(items, key=lambda x: (pub_ts(x), x.get("vph", 0.0), x.get("views", 0), relevance_score(x.get("title",""), x.get("description",""), keyword)), reverse=True)
    if sort_option == "Paling Banyak Ditonton":
        return sorted(items, key=lambda x: (x.get("views", 0), x.get("vph", 0.0), pub_ts(x), relevance_score(x.get("title",""), x.get("description",""), keyword)), reverse=True)
    if sort_option == "Paling Relevan":
        return sorted(items, key=lambda x: (relevance_score(x.get("title",""), x.get("description",""), keyword), x.get("vph", 0.0), pub_ts(x), x.get("views", 0)), reverse=True)
    return items

def filter_by_video_type(items, video_type_label: str):
    if video_type_label == "Short":
        return [v for v in items if v.get("duration_sec", 0) <= 60 and v.get("live", "none") == "none"]
    if video_type_label == "Regular":
        return [v for v in items if v.get("duration_sec", 0) > 60 and v.get("live", "none") == "none"]
    if video_type_label == "Live":
        return [v for v in items if v.get("live", "none") == "live"]
    return items

# ---------------- judul rekom (list 10) ----------------
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

# ---------------- Gemini helpers & AI tasks ----------------
def use_gemini():
    return bool(ss.gemini_api)

def gemini_generate(prompt: str, retries: int = 1) -> str:
    if not use_gemini() or ss.get("gemini_blocked", False):
        return ""
    try:
        import google.generativeai as genai
        genai.configure(api_key=ss.gemini_api)
        model = genai.GenerativeModel(ss.gemini_model)
        resp = model.generate_content(prompt)
        return resp.text if hasattr(resp, "text") and resp.text else ""
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower():
            ss["gemini_blocked"] = True
            ss["gemini_last_error"] = "Batas harian Gemini tercapai. Fallback lokal digunakan."
            return ""
        if retries > 0:
            return gemini_generate(prompt, retries - 1)
        ss["gemini_last_error"] = msg
        return ""

def content_type(v):
    if v.get("live") == "live": return "Live"
    if v.get("duration_sec", 0) <= 60: return "Short"
    return "Regular"

def ai_summary(v):
    title, desc, ch = v["title"], v.get("description",""), v.get("channel","")
    if use_gemini() and not ss.get("gemini_blocked", False):
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
    if use_gemini() and not ss.get("gemini_blocked", False):
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
    if use_gemini() and not ss.get("gemini_blocked", False):
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
    if use_gemini() and not ss.get("gemini_blocked", False):
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
    if use_gemini() and not ss.get("gemini_blocked", False):
        if lang == "en":
            text = gemini_generate("Generate comma-separated YouTube SEO tags in ENGLISH (≤500 chars). "
                                   f"Use keywords from the title/description.\nTitle: {title}\nDescription: {desc[:1500]}")
        else:
            text = gemini_generate("Buat daftar tag SEO YouTube berbahasa INDONESIA (dipisahkan koma, ≤500 karakter). "
                                   f"Gunakan kata kunci dari judul/deskripsi.\nJudul: {title}\nDeskripsi: {desc[:1500]}")
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
        siapa = ("- Usia: 18–44\n- Gender: Campuran\n- Lokasi: Global (WIB untuk pasar ID)\n"
                 "- Status: Mahasiswa/pekerja pencari ketenangan\n- Masalah: Stres, sulit tidur, susah fokus\n- Harapan: Tenang, tidur lelap, energi positif")
        apa = ("- Kebutuhan/Minat: Musik meditasi, suara alam, 432Hz, chakra, fokus belajar\n"
               "- Jenis Konten: Track 10–60 mnt, panduan napas singkat, live relaksasi mingguan")
        bagaimana = ("- Gaya: Tenang, minim bicara, visual menenangkan\n"
                     "- Bentuk: Reguler (mix panjang), Shorts (teknik 60 detik), Live (Q&A & breathing)\n"
                     "- Durasi: 30–60 mnt (reguler) / ≤60 dtk (short)\n- Frekuensi: 3–5x/minggu + 1 live/minggu")
    else:
        siapa = ("- Usia: 18–40\n- Gender: Campuran\n- Lokasi: Global\n- Status: Pemula di niche\n"
                 "- Masalah: Minim referensi\n- Harapan: Panduan ringkas & jelas")
        apa = "- Kebutuhan/Minat: Tutorial praktis, ringkasan topik, rekomendasi alat\n- Jenis Konten: How-to, listicle, live Q&A"
        bagaimana = "- Gaya: To the point • Bentuk: Reguler/Short • Durasi: 5–15 mnt (reg) / ≤60 dtk (short) • Frekuensi: 3x/minggu"
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
        f"Sampel: **{stat['n']}** • Avg views **{format_views(stat['avg'])}** • Median **{format_views(stat['med'])}** • Avg VPH **{stat['vph']}**",
        f"Topik kunci: {tok_md}",
        f"Waktu publish efektif (WIB): {jam_md}",
        "Strategi: fokus format dominan + variasi yang cepat perform (Short/Live)."
    ]
    md = ("### 📊 Ringkasan Niche (otomatis)\n"
          f"- **Label:** {label}\n- **Distribusi Format:** {fmt_md}\n\n"
          "### 🧠 SIAPA (Target)\n" + siapa + "\n\n"
          "### 📚 APA (Minat)\n" + apa + "\n\n"
          "### 🎯 BAGAIMANA (Eksekusi)\n" + bagaimana + "\n\n"
          f"### 🕒 Rata-rata Jam Publish (WIB)\n{jam_md}\n\n"
          "### 📈 Metrik Ringkas\n"
          f"- Sampel: **{stat['n']}**\n- Rata-rata Views: **{format_views(stat['avg'])}** • Median: **{format_views(stat['med'])}**\n- VPH rata-rata: **{stat['vph']}**\n\n"
          "### 📌 Rangkuman Ketat\n" + "\n".join(f"- {b}" for b in bullets))
    return md

# ---------------- Handle submit ----------------
if submit:
    ss.keyword_input = keyword
    if not keyword.strip():
        st.info("📈 Menampilkan trending (default US)")
        videos_all = get_trending(ss.api_key, ss.max_per_order)
    else:
        st.info(f"🔎 Riset keyword: {keyword}")
        order = map_sort_option(sort_option)
        ids = yt_search_ids(ss.api_key, keyword, order, ss.max_per_order)
        videos_all = yt_videos_detail(ss.api_key, ids)

    videos_all = filter_by_video_type(videos_all, ss.get("video_type","Semua"))
    videos_all = apply_client_sort(videos_all, sort_option, ss.keyword_input)
    ss.last_results = videos_all

    # Auto IDE via Gemini (opsional) + fallback lokal
    ss.auto_ideas = None
    if videos_all and use_gemini() and not ss.get("gemini_blocked", False):
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
            ss.auto_ideas = gemini_generate(
                f"Berdasarkan judul-judul:\n{titles_text}\n\nKata kunci turunan: {derived_kw}\n"
                f"Jenis konten dominan: {fmt}\nBuatkan 5 ide video lengkap dengan SIAPA/APA/BAGAIMANA dan IDE VISUAL (1 per ide)."
            ) or ""
        except Exception:
            ss.auto_ideas = ""

    if (ss.auto_ideas is None) or (ss.auto_ideas.strip() == ""):
        if videos_all:
            sample = videos_all[:5]
            fmt_dom = "Short (≤60 detik)" if sum(1 for v in sample if v.get("duration_sec",0)<=60) > 2 else \
                      "Live Streaming" if sum(1 for v in sample if v.get("live","none")=="live") > 2 else "Video Reguler (5–30 menit)"
            kws = []
            for v in sample:
                for w in re.split(r"[^\w]+", v["title"].lower()):
                    if len(w) >= 4 and w not in STOPWORDS:
                        kws.append(w)
            kws = ", ".join(sorted(set(kws))[:10])
            ss.auto_ideas = (
                f"**Format dominan:** {fmt_dom}\n\n"
                f"**Kata kunci turunan:** {kws}\n\n"
                "### 5 Ide Video (fallback lokal)\n"
                "1) **SIAPA**: Pemula • **APA**: Panduan cepat • **BAGAIMANA**: 3 langkah praktis • **Visual**: close-up + teks 2 kata\n"
                "2) **SIAPA**: Pekerja sibuk • **APA**: Trik hemat waktu • **BAGAIMANA**: tips 1 menit • **Visual**: before/after split\n"
                "3) **SIAPA**: Konten kreator • **APA**: Optimasi judul & tag • **BAGAIMANA**: checklist • **Visual**: ikon + gradient\n"
                "4) **SIAPA**: Penonton live • **APA**: Q&A topik tren • **BAGAIMANA**: rundown segmen • **Visual**: agenda + emoji chat\n"
                "5) **SIAPA**: Pemula editing • **APA**: Efek instan • **BAGAIMANA**: step-by-step • **Visual**: infografik 1-2-3\n"
            )

# ---------------- CSS: kartu rapi & konsisten ----------------
st.markdown("""
<style>
/* Card grid: equal height */
.card {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 14px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 420px;  /* bikin rata */
}
.card .thumbwrap { position: relative; border-radius: 10px; overflow:hidden; }
.card img.thumb { width: 100%; display: block; }
.badge { position:absolute; top:8px; left:8px; color:white; padding:2px 8px; font-size:12px; border-radius:6px; font-weight:700; }
.badge-live { background:#e53935; }
.badge-short{ background:#1e88e5; }

.title-area {
  font-weight: 600;
  line-height: 1.25;
  font-size: 15px;
  min-height: 60px;     /* area judul rata */
  word-break: break-word;
  white-space: normal;  /* jangan terpotong */
}

.meta-chips { margin-top: 2px; display: flex; gap: 8px; flex-wrap: wrap; }
.chip { display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px; font-size:12px; color:white; }
.chip-views { background:#ff4b4b; }
.chip-vph { background:#4b8bff; }
.chip-time { background:#4caf50; }
.chip-dur { background:#795548; }

/* Buttons: full width & no wrap */
.stButton > button { width: 100%; white-space: nowrap; }
.linklike > button { background:none !important; border:none !important; padding:0 !important;
                     color:#1f6feb; text-decoration:none; font-weight:600; cursor:pointer; }
.linklike > button:hover { text-decoration:underline; }
</style>
""", unsafe_allow_html=True)

# ---------------- Dialog (modal) popup ----------------
HAS_DIALOG = hasattr(st, "dialog")

if HAS_DIALOG:
    @st.dialog("📺 Video Preview")
    def video_preview_dialog():
        v = ss.get("popup_video")
        if not v:
            st.write("Tidak ada video.")
            return
        vid = v["id"]
        yt_url = f"https://www.youtube.com/watch?v={vid}"
        ch_url = f"https://www.youtube.com/channel/{v.get('channelId','')}" if v.get("channelId") else None

        st.markdown(f"### {v['title']}")
        # chips header
        ch = st.columns(4)
        ch[0].markdown(f"<span class='chip chip-views'>👁 {format_views(v['views'])}</span>", unsafe_allow_html=True)
        ch[1].markdown(f"<span class='chip chip-vph'>⚡ {v['vph']}</span>", unsafe_allow_html=True)
        ch[2].markdown(f"<span class='chip chip-time'>⏱ {format_rel_time(v['publishedAt'])}</span>", unsafe_allow_html=True)
        ch[3].markdown(f"<span class='chip chip-dur'>⏳ {v.get('duration','-')}</span>", unsafe_allow_html=True)

        c1, c2 = st.columns([2,1])
        with c1:
            st.video(yt_url)
        with c2:
            st.markdown("#### Aksi")
            st.link_button("▶️ Buka di YouTube", yt_url, use_container_width=True)
            if ch_url:
                st.link_button("🌐 Kunjungi Channel", ch_url, use_container_width=True)
            st.text_input("Link Video", yt_url, key=f"copy_url_{vid}")
            st.caption(f"Channel: {v['channel']}")
            if v.get("live") == "live":
                st.success("🔴 LIVE content")
            elif v.get("duration_sec",0) <= 60:
                st.info("🟦 SHORT (≤60 detik)")
            else:
                st.caption("📼 Video reguler")

        # cache helpers
        def cache_get(task): return ss.ai_cache.get(vid, {}).get(task)
        def cache_set(task, text): ss.ai_cache.setdefault(vid, {})[task] = text
        def cache_del(task):
            if vid in ss.ai_cache and task in ss.ai_cache[vid]:
                del ss.ai_cache[vid][task]

        t1, t2, t3 = st.tabs(["ℹ️ Info", "✨ Asisten Konten AI", "📈 Analytics"])

        with t1:
            with st.expander("Deskripsi", expanded=False):
                st.write(v.get("description","Tidak ada deskripsi."))
            st.caption(f"Publish: {format_jam_utc(v['publishedAt'])} • ID: {vid}")

        with t2:
            a1, a2 = st.columns(2)

            with a1:
                ccol = st.columns([1,1])
                if ccol[0].button("🧾 Ringkas", key=f"d_summary_make_{vid}"):
                    cache_set("summary", ai_summary(v))
                if ccol[1].button("Tutup", key=f"d_summary_close_{vid}"):
                    cache_del("summary"); st.rerun()
                if cache_get("summary"): st.markdown(cache_get("summary"))

                ccol = st.columns([1,1])
                if ccol[0].button("🔑 Tag SEO", key=f"d_tags_make_{vid}"):
                    cache_set("tags", ai_seo_tags(v))
                if ccol[1].button("Tutup", key=f"d_tags_close_{vid}"):
                    cache_del("tags"); st.rerun()
                if cache_get("tags"): st.text_area("Tag SEO", cache_get("tags"), height=120, key=f"d_tags_area_{vid}")

            with a2:
                ccol = st.columns([1,1])
                if ccol[0].button("📝 Kerangka Skrip", key=f"d_script_make_{vid}"):
                    cache_set("script", ai_script_outline(v))
                if ccol[1].button("Tutup", key=f"d_script_close_{vid}"):
                    cache_del("script"); st.rerun()
                if cache_get("script"): st.markdown(cache_get("script"))

                ccol = st.columns([1,1])
                if ccol[0].button("✍️ Judul Alternatif", key=f"d_titles_make_{vid}"):
                    cache_set("alt_titles", ai_alt_titles(v))
                if ccol[1].button("Tutup", key=f"d_titles_close_{vid}"):
                    cache_del("alt_titles"); st.rerun()
                if cache_get("alt_titles"): st.markdown(cache_get("alt_titles"))

                ccol = st.columns([1,1])
                if ccol[0].button("🖼️ Ide Thumbnail", key=f"d_thumb_make_{vid}"):
                    cache_set("thumbs", ai_thumb_ideas(v))
                if ccol[1].button("Tutup", key=f"d_thumb_close_{vid}"):
                    cache_del("thumbs"); st.rerun()
                if cache_get("thumbs"): st.markdown(cache_get("thumbs"))

        with t3:
            colm = st.columns(4)
            colm[0].metric("Views", format_views(v["views"]))
            colm[1].metric("VPH", v["vph"])
            colm[2].metric("Durasi", v.get("duration","-"))
            colm[3].metric("Publish (rel)", format_rel_time(v["publishedAt"]))

        st.markdown("---")
        if st.button("❌ Tutup", key="close_dialog"):
            ss.popup_video = None
            st.rerun()

# ---------------- Render results (cards) ----------------
videos = ss.last_results
if videos:
    cols = st.columns(3)
    all_titles, rows_for_csv = [], []

    for i, v in enumerate(videos):
        with cols[i % 3]:
            st.markdown("<div class='card'>", unsafe_allow_html=True)

            # thumbnail + badge
            badge_html = ""
            if v.get("live") == "live": badge_html = '<div class="badge badge-live">LIVE</div>'
            elif v.get("duration_sec", 0) <= 60: badge_html = '<div class="badge badge-short">SHORT</div>'
            if v.get("thumbnail"):
                st.markdown(f"<div class='thumbwrap'>{badge_html}<img class='thumb' src='{v['thumbnail']}'></div>", unsafe_allow_html=True)

            # title area (clickable)
            st.markdown('<div class="title-area">', unsafe_allow_html=True)
            if st.button(v["title"], key=f"title_btn_{i}"):
                ss.popup_video = v
                if HAS_DIALOG:
                    video_preview_dialog()
            st.markdown('</div>', unsafe_allow_html=True)

            # preview button
            if st.button("🔍 Preview", key=f"preview_btn_{i}"):
                ss.popup_video = v
                if HAS_DIALOG:
                    video_preview_dialog()

            # channel
            st.caption(v["channel"])

            # chips
            st.markdown("<div class='meta-chips'>", unsafe_allow_html=True)
            st.markdown(f"<span class='chip chip-views'>👁 {format_views(v['views'])}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='chip chip-vph'>⚡ {v['vph']}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='chip chip-time'>⏱ {format_rel_time(v['publishedAt'])}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='chip chip-dur'>⏳ {v.get('duration','-')}</span>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

            # bottom time row
            st.caption(f"📅 {format_jam_utc(v['publishedAt'])}")

            st.markdown("</div>", unsafe_allow_html=True)  # end .card

        all_titles.append(v["title"])
        rows_for_csv.append({
            "Judul": v["title"], "Channel": v["channel"], "Views": v["views"], "VPH": v["vph"],
            "Tanggal (relatif)": format_rel_time(v["publishedAt"]), "Jam Publish (UTC)": format_jam_utc(v["publishedAt"]),
            "Durasi": v.get("duration","-"), "Link": f"https://www.youtube.com/watch?v={v['id']}"
        })

    # Fallback inline detail kalau versi Streamlit belum ada dialog
    if (not HAS_DIALOG) and ss.popup_video:
        v = ss.popup_video
        vid = v["id"]
        st.markdown("---")
        st.subheader("📺 Video Detail")
        st.video(f"https://www.youtube.com/watch?v={vid}")
        st.markdown(f"### {v['title']}")
        st.caption(v["channel"])
        st.write(v.get("description", "Tidak ada deskripsi."))

        def cache_get(task): return ss.ai_cache.get(vid, {}).get(task)
        def cache_set(task, text): ss.ai_cache.setdefault(vid, {})[task] = text
        def cache_del(task):
            if vid in ss.ai_cache and task in ss.ai_cache[vid]:
                del ss.ai_cache[vid][task]

        st.subheader("✨ Asisten Konten AI")
        c1, c2 = st.columns(2)
        with c1:
            b = st.columns([1,1])
            if b[0].button("🧾 Ringkas", key=f"btn_summary_make_{vid}"):
                cache_set("summary", ai_summary(v))
            if b[1].button("Tutup", key=f"btn_summary_close_{vid}"):
                cache_del("summary"); st.rerun()
            if cache_get("summary"): st.markdown(cache_get("summary"))

            b = st.columns([1,1])
            if b[0].button("🔑 Tag SEO", key=f"btn_tags_make_{vid}"):
                cache_set("tags", ai_seo_tags(v))
            if b[1].button("Tutup", key=f"btn_tags_close_{vid}"):
                cache_del("tags"); st.rerun()
            if cache_get("tags"): st.text_area("Tag SEO", cache_get("tags"), height=120, key=f"tags_area_{vid}")
        with c2:
            b = st.columns([1,1])
            if b[0].button("📝 Kerangka Skrip", key=f"btn_script_make_{vid}"):
                cache_set("script", ai_script_outline(v))
            if b[1].button("Tutup", key=f"btn_script_close_{vid}"):
                cache_del("script"); st.rerun()
            if cache_get("script"): st.markdown(cache_get("script"))

            b = st.columns([1,1])
            if b[0].button("✍️ Judul Alternatif", key=f"btn_titles_make_{vid}"):
                cache_set("alt_titles", ai_alt_titles(v))
            if b[1].button("Tutup", key=f"btn_titles_close_{vid}"):
                cache_del("alt_titles"); st.rerun()
            if cache_get("alt_titles"): st.markdown(cache_get("alt_titles"))

            b = st.columns([1,1])
            if b[0].button("🖼️ Ide Thumbnail", key=f"btn_thumb_make_{vid}"):
                cache_set("thumbs", ai_thumb_ideas(v))
            if b[1].button("Tutup", key=f"btn_thumb_close_{vid}"):
                cache_del("thumbs"); st.rerun()
            if cache_get("thumbs"): st.markdown(cache_get("thumbs"))

        if v.get("channelId"):
            st.markdown(f"[🌐 Kunjungi Channel YouTube](https://www.youtube.com/channel/{v['channelId']})")

        if st.button("❌ Tutup", key="close_popup"):
            ss.popup_video = None
            st.rerun()

    # ---- Tab Ide: ringkasan + ide ----
    with tab2:
        vids = ss.get("last_results", [])
        kw = ss.get("keyword_input", "")
        if vids:
            st.markdown(render_niche_summary(vids, kw))
        else:
            st.info("Belum ada data. Silakan cari video dulu di tab 🔍.")
        if ss.auto_ideas:
            st.markdown(ss.auto_ideas)
        else:
            st.caption("Tips: aktifkan Gemini untuk ide yang lebih variatif. Tanpa Gemini, gunakan ringkasan niche di atas sebagai acuan.")

    # ---- Rekomendasi Judul ----
    st.subheader("💡 Rekomendasi Judul (10 Judul, ≤100 Karakter)")
    rec_titles = generate_titles_from_data(videos, ss.get("sort_option", "VPH Tertinggi"))
    for idx, rt in enumerate(rec_titles, 1):
        c1, c2, c3 = st.columns([6, 1, 1])
        with c1: st.text_input(f"Judul {idx}", rt, key=f"judul_{idx}")
        with c2: st.markdown(f"<span style='font-size:12px;color:gray'>{len(rt)}/100</span>", unsafe_allow_html=True)
        with c3: st.button("📋", key=f"copy_judul_{idx}", on_click=lambda t=rt: ss.update({"copied": t}))
    if "copied" in ss:
        st.success(f"Judul tersalin: {ss['copied']}")
        ss.pop("copied")

    # ---- Rekomendasi Tag ----
    st.subheader("🏷️ Rekomendasi Tag (max 500 karakter)")
    uniq_words, seen = [], set()
    for t in all_titles:
        for w in re.split(r"[^\w]+", t.lower()):
            if len(w) >= 3 and w not in STOPWORDS and w not in seen:
                uniq_words.append(w); seen.add(w)
    tag_string = ", ".join(uniq_words)
    if len(tag_string) > 500: tag_string = tag_string[:497] + "..."
    st.text_area("Tag (gabungan hasil pencarian)", tag_string, height=100, key="tag_area_global")

    c1, c2 = st.columns([8, 1])
    with c2:
        def _copy_current_tags():
            ss["copied_tag"] = ss.get("tag_area_global", "")
        st.button("📋", key="copy_tag_btn", on_click=_copy_current_tags)
    if "copied_tag" in ss:
        st.success("✅ Tag tersalin!")
        ss.pop("copied_tag")

    # ---- Downloads ----
    st.subheader("⬇️ Download Data")
    df = pd.DataFrame(rows_for_csv)
    csv_video_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV (Video)", csv_video_bytes, "youtube_riset.csv", "text/csv", key="dl_csv")

    if ss.auto_ideas:
        ideas_txt_bytes = ss.auto_ideas.encode("utf-8")
        st.download_button("Download Ide (TXT)", ideas_txt_bytes, "auto_ideas.txt", "text/plain", key="dl_txt")
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("youtube_riset.csv", csv_video_bytes)
            zf.writestr("auto_ideas.txt", ideas_txt_bytes)
        st.download_button("Download Paket (ZIP)", zip_buffer.getvalue(), "paket_riset.zip", "application/zip", key="dl_zip")
else:
    st.info("Mulai dengan melakukan pencarian di tab 🔍, lalu klik judul atau tombol **🔍 Preview** untuk membuka popup.")
