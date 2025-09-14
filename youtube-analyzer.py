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
from streamlit.components.v1 import html as st_html

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
if "ai_cache" not in st.session_state: st.session_state.ai_cache = {}
if "keyword_input" not in st.session_state: st.session_state.keyword_input = ""

# ---------------- Query params helpers ----------------
def get_qp():
    try:
        qp = getattr(st, "query_params")
        if isinstance(qp, dict):
            return {k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}
        return {}
    except Exception:
        try:
            qp = st.experimental_get_query_params()
            return {k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}
        except Exception:
            return {}

def set_qp(**kwargs):
    clean = {k: v for k, v in kwargs.items() if v is not None}
    try:
        st.query_params.clear()
        for k, v in clean.items():
            st.query_params[k] = v
    except Exception:
        try:
            st.experimental_set_query_params(**clean)
        except Exception:
            pass

def clear_open_param():
    qp = get_qp()
    if "open" in qp:
        qp.pop("open", None)
        set_qp(**qp)

# ---------------- Sidebar ----------------
if st.session_state.get("gemini_blocked"):
    st.info("ℹ️ Fitur Gemini dibatasi (quota tercapai). App pakai fallback lokal.")

with st.sidebar:
    st.header("⚙️ Pengaturan")
    api_key = st.text_input("YouTube Data API Key", st.session_state.api_key, type="password", key="yt_api_key")
    gemini_api = st.text_input("Gemini API Key (Opsional)", st.session_state.gemini_api, type="password", key="gemini_api_key")
    gemini_model = st.selectbox("Gemini Model", ["gemini-1.5-flash-8b", "gemini-1.5-flash", "gemini-1.5-pro"], index=0, key="gemini_model")
    st.caption("Belum punya Gemini API Key? 👉 [Buat di sini](https://aistudio.google.com/app/apikey)")
    max_per_order = st.slider("Jumlah video per kategori/varian", 5, 30, 15, 1, key="max_per_order")
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
        keyword = st.text_input("Kata Kunci (kosongkan untuk Trending)", placeholder="flute tibet / seruling tibetan / healing flute", key="keyword_form_input")
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
        if ZoneInfo: return dt.astimezone(ZoneInfo("Asia/Jakarta")).hour
        return (dt.hour + 7) % 24
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

# ---------------- Multilingual synonyms (niche musik/meditasi/healing) ----------------
INSTR_SYN = {
    "en":["flute"], "id":["seruling"], "es":["flauta"], "pt":["flauta"], "fr":["flûte"], "de":["flöte"],
    "it":["flauto"], "ru":["флейта"], "tr":["flüt"], "ar":["فلوت","ناي"], "hi":["बांसुरी"],
    "ja":["フルート"], "ko":["플루트","플룻"], "zh":["长笛","笛子"], "vi":["sáo"], "th":["ขลุ่ย"]
}
REGION_SYN = {
    "en":["tibet","tibetan"], "id":["tibet","tibetan","tibetian"], "es":["tíbet","tibetano"],
    "pt":["tibete","tibetano"], "fr":["tibet","tibétain"], "de":["tibet","tibetisch"], "it":["tibet","tibetano"],
    "ru":["тибет","тибетский"], "tr":["tibet","tibetli"], "ar":["التبت"], "hi":["तिब्बत"],
    "ja":["チベット"], "ko":["티베트"], "zh":["西藏"], "vi":["tây tạng"], "th":["ทิเบต"]
}
THEME_SYN = {
    "en":["healing","meditation","relax","sleep"], "id":["penyembuhan","meditasi","santai","tidur"],
    "es":["sanación","meditación","relajante","dormir"], "pt":["cura","meditação","relaxante","dormir"],
    "fr":["guérison","méditation","relaxant","sommeil"], "de":["heilung","meditation","entspannung","schlaf"],
    "it":["guarigione","meditazione","rilassante","sonno"], "ru":["исцеление","медитация","релакс","сон"],
    "tr":["şifa","meditasyon","rahatlatıcı","uyku"], "ar":["شفاء","تأمل","استرخاء","نوم"],
    "hi":["उपचार","ध्यान","आराम","नींद"], "ja":["ヒーリング","瞑想","リラックス","睡眠"],
    "ko":["치유","명상","릴랙스","수면"], "zh":["治愈","冥想","放松","睡眠"], "vi":["chữa lành","thiền","thư giãn","ngủ"],
    "th":["รักษา","ทำสมาธิ","ผ่อนคลาย","นอน"]
}
# reverse index untuk deteksi cepat
def _rev_index(syndict):
    r = {}
    for lang, terms in syndict.items():
        for t in terms:
            r[t.lower()] = (lang, t)
    return r
REV_INSTR = _rev_index(INSTR_SYN)
REV_REGION = _rev_index(REGION_SYN)
REV_THEME = _rev_index(THEME_SYN)
LANG_PRIORITY = ["en","id","es","pt","fr","de","ru","ar","hi","ja","ko","zh","tr","vi","th"]

def expand_keyword_variants(user_q: str, max_variants: int = 10):
    """
    Kembalikan list varian (query, lang) agar tetap satu niche walau beda bahasa.
    Logika: deteksi instrumen/region/tema dari input (di bahasa apapun), lalu
    buat frase standar di banyak bahasa (maks 10).
    """
    qnorm = user_q.strip()
    if not qnorm:
        return []
    toks = re.findall(r"\w+|\p{L}+", qnorm, flags=re.UNICODE) if hasattr(re, "UNICODE") else re.findall(r"\w+", qnorm)

    instr_langs, region_langs, theme_langs = set(), set(), set()
    text_lower = qnorm.lower()
    # deteksi semua kemunculan dari kamus
    for w in re.split(r"[^\w\u00C0-\u024F\u0370-\u03FF\u0400-\u04FF\u0590-\u06FF\u3040-\u30FF\u4E00-\u9FFF]+", text_lower):
        if w in REV_INSTR: instr_langs.add(REV_INSTR[w][0])
        if w in REV_REGION: region_langs.add(REV_REGION[w][0])
        if w in REV_THEME: theme_langs.add(REV_THEME[w][0])

    # base languages: yang terdeteksi + prioritas default (en,id)
    langs = []
    for lang in LANG_PRIORITY:
        if (lang in instr_langs) or (lang in region_langs) or (lang in theme_langs) or (lang in ["en","id"]):
            langs.append(lang)
    # unik & batasi
    seen=set(); ordered=[]
    for l in langs:
        if l not in seen:
            ordered.append(l); seen.add(l)
    langs = ordered[:max_variants]  # caps

    variants = []
    variants.append((qnorm, None))  # varian asli (tanpa hint language)
    for lang in langs:
        parts = []
        if instr_langs:
            parts.append(INSTR_SYN[lang][0])
        if region_langs:
            parts.append(REGION_SYN[lang][0])
        if theme_langs:
            parts.append(THEME_SYN[lang][0])
        # kalau tidak terdeteksi apapun dari kamus, skip varian bahasa itu
        if not parts:
            continue
        phrase = " ".join(parts)
        variants.append((phrase, lang))
    # unik berdasarkan query string
    uq, out = set(), []
    for q, l in variants:
        if q.lower() not in uq:
            out.append((q, l)); uq.add(q.lower())
    return out[:max_variants]

# ---------------- API ----------------
def yt_search_ids(api_key, query, order, max_results, video_type_label="Semua", lang: str | None = None, region: str | None = None):
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": order,
        "maxResults": max_results,
        "key": api_key
    }
    if video_type_label == "Short":
        params["videoDuration"] = "short"   # pre-filter
    elif video_type_label == "Live":
        params["eventType"] = "live"
    if lang: params["relevanceLanguage"] = lang
    if region: params["regionCode"] = region
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

# ---------------- Relevance helpers ----------------
def _tokenize(txt: str):
    return [w for w in re.split(r"[^\w]+", (txt or "").lower()) if len(w) >= 3 and w not in STOPWORDS]

def relevance_score(title: str, desc: str, keyword: str) -> int:
    if not keyword: return 0
    q = set(_tokenize(keyword))
    if not q: return 0
    doc = _tokenize((title or "") + " " + (desc or ""))
    overlap = sum(1 for w in doc if w in q)
    return overlap + (5 if q.issubset(set(doc)) else 0)

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

# ---------------- Judul Generator ----------------
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

# ---------------- Gemini helpers & tasks ----------------
def use_gemini():
    return bool(st.session_state.gemini_api)

def gemini_generate(prompt: str, retries: int = 1) -> str:
    if not use_gemini() or st.session_state.get("gemini_blocked", False): return ""
    try:
        import google.generativeai as genai
        genai.configure(api_key=st.session_state.gemini_api)
        model_name = st.session_state.get("gemini_model", "gemini-1.5-flash-8b")
        resp = genai.GenerativeModel(model_name).generate_content(prompt)
        return resp.text if getattr(resp, "text", "") else ""
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower():
            st.session_state["gemini_blocked"] = True
            st.session_state["gemini_last_error"] = "Batas harian Gemini tercapai. Fallback lokal."
            return ""
        if retries > 0: return gemini_generate(prompt, retries - 1)
        st.session_state["gemini_last_error"] = msg
        return ""

def content_type(v):
    if v.get("live") == "live": return "Live"
    if v.get("duration_sec", 0) <= 60: return "Short"
    return "Regular"

def ai_summary(v):
    title, desc, ch = v["title"], v.get("description",""), v.get("channel","")
    res = ""
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        res = gemini_generate(f"Ringkas video YouTube berikut menjadi 5 bullet berbahasa Indonesia.\nJudul: {title}\nChannel: {ch}\nDeskripsi:\n{desc[:3000]}")
    if res: return res
    sentences = re.split(r'(?<=[.!?])\s+', desc)[:5] or [title]
    return "**Ringkasan (fallback lokal)**\n" + "\n".join(f"- {s}" for s in sentences)

def ai_alt_titles(v):
    ct, lang = content_type(v), detect_lang(v["title"])
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        res = gemini_generate(
            (f"Write 10 alternative YouTube titles (≤100 chars) in ENGLISH for '{v['title']}'. " if lang=="en"
             else f"Buat 10 judul alternatif (≤100 karakter) dalam BAHASA INDONESIA untuk '{v['title']}'. ")
            + f"Mix styles, keep topic. Content format: {ct}. Numbered list."
        )
        if res: return res
    base = v["title"]
    if lang == "en":
        variants = [trim_to_100(base), trim_to_100(f"{base} | Full Guide"), trim_to_100(f"{base} (Tips & Tricks)"),
                    trim_to_100(f"{base}: Step-by-Step"), trim_to_100(f"Master {base} in Minutes"),
                    trim_to_100(f"{base} for Beginners"), trim_to_100(f"{base} Explained!"),
                    trim_to_100(f"Top 5 {base} Hacks"), trim_to_100(f"{base} [2025 Update]"),
                    trim_to_100(f"Why {base}? The Truth")]
    else:
        variants = [trim_to_100(base), trim_to_100(f"{base} | Panduan Lengkap"), trim_to_100(f"{base} (Tips & Trik)"),
                    trim_to_100(f"{base}: Langkah demi Langkah"), trim_to_100(f"Kuasi {base} dalam Hitungan Menit"),
                    trim_to_100(f"{base} untuk Pemula"), trim_to_100(f"{base} Tuntas!"),
                    trim_to_100(f"5 Trik {base} Teratas"), trim_to_100(f"{base} [2025]"),
                    trim_to_100(f"Kenapa {base}? Ini Alasannya")]
    return "\n".join(f"{i+1}. {t}" for i, t in enumerate(variants[:10]))

def ai_script_outline(v):
    ct = content_type(v)
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        res = gemini_generate(f"Buat kerangka skrip YouTube berbahasa Indonesia untuk '{v['title']}'. Format: {ct}. Sertakan HOOK, Intro, 3–6 poin utama, CTA. Untuk Live tambahkan agenda & interaksi chat.")
        if res: return res
    return "HOOK → Intro → 3 Bagian → Rekap → CTA" if ct=="Regular" else \
           "HOOK (0–3s) → Inti cepat (3–50s, 3 poin) → CTA (50–60s)" if ct=="Short" else \
           "Opening • Agenda • Interaksi Chat • Checkpoint • Closing"

def ai_thumb_ideas(v):
    title = v["title"]
    kw = ", ".join(sorted({w for w in re.split(r"[^\w]+", (title + ' ' + v.get('description','')).lower()) if len(w)>=4 and w not in STOPWORDS})[:8])
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        res = gemini_generate(f"Buat 5 ide thumbnail berbahasa Indonesia untuk '{title}'. 1 baris/ide: konsep + gaya + komposisi + teks ≤3 kata. Sertakan 1 prompt (Midjourney-style). Kata kunci: {kw}.")
        if res: return res
    return "\n".join([
        "Close-up objek + teks 2 kata\nPrompt: ultra-detailed close-up, dramatic lighting, high contrast",
        "Before/After split screen\nPrompt: split-screen comparison, cinematic 16:9, big arrow",
        "Wajah ekspresif menunjuk objek\nPrompt: person pointing, shallow depth, crisp label",
        "Ikon minimalis + gradient\nPrompt: flat icon center, vivid gradient, clean type",
        "Diagram 3 langkah\nPrompt: infographic 1-2-3, bright, bold numbers"
    ])

def ai_seo_tags(v):
    title, desc, lang = v["title"], v.get("description",""), detect_lang(v["title"])
    words = [w for w in re.split(r"[^\w]+", (title+" "+desc).lower()) if len(w)>=3 and w not in STOPWORDS]
    fallback = ", ".join(list(dict.fromkeys(words))[:40])[:500]
    if use_gemini() and not st.session_state.get("gemini_blocked", False):
        text = gemini_generate(
            ("Generate comma-separated YouTube SEO tags in ENGLISH (≤500 chars). " if lang=="en"
             else "Buat daftar tag SEO YouTube berbahasa INDONESIA (dipisahkan koma, ≤500 karakter). ")
            + f"Use/Gunakan kata kunci dari judul & deskripsi.\nTitle/Judul: {title}\nDescription/Deskripsi: {desc[:1500]}"
        )
        return text if text else fallback
    return fallback

# ---------------- Niche summary (Tab Ide) ----------------
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
    return "Meditasi / Healing Music 432Hz" if (tokens & med_keys) else "Niche berdasarkan kata kunci"

def publish_hour_stats(videos):
    hours=[]
    for v in videos:
        h = asia_jakarta_hour(v.get("publishedAt",""))
        if h is not None: hours.append(h)
    if not hours: return {"avg": None, "top": []}
    avg_h = round(mean(hours))
    top = Counter(hours).most_common(3)
    return {"avg": avg_h, "top": top}

def views_stats(videos):
    vs=[int(v.get("views",0)) for v in videos if isinstance(v.get("views",0), int)]
    vph=[float(v.get("vph",0.0)) for v in videos]
    return {"avg": int(mean(vs)) if vs else 0, "med": int(median(vs)) if vs else 0, "vph": round(mean(vph),2) if vph else 0.0, "n": len(videos)}

def window_hour(h): return f"{h:02d}:00–{(h+1)%24:02d}:59"

def render_niche_summary(videos, keyword: str) -> str:
    vids = relevant_videos(videos, keyword)
    s,l,r = format_share(vids)
    tokens = set(core_tokens(vids, topn=12))
    label = format_label_from_tokens(tokens)
    hrs = publish_hour_stats(vids)
    stat = views_stats(vids)
    if hrs["top"]:
        top_list = ", ".join(f"{h:02d} (n={c})" for h,c in hrs["top"])
        saran = ", ".join(window_hour(h) for h,_ in hrs["top"][:2])
        jam_md = f"**Rata-rata:** {hrs['avg']:02d}:00 WIB • **Puncak:** {top_list}\n**Saran upload:** {saran}"
    else:
        jam_md = "Data jam publish tidak cukup."
    fmt_md = f"Short: {s} • Live: {l} • Reguler: {r} (total {len(vids)})"
    tok_md = ", ".join(sorted(list(tokens))[:12])
    bullets = [
        f"Niche: **{label}** • Format dominan → {('Reguler' if r>=max(s,l) else 'Short' if s>=max(l,r) else 'Live')}",
        f"Sampel: **{stat['n']}** video • Rata-rata views **{format_views(stat['avg'])}** • Median **{format_views(stat['med'])}** • VPH rata-rata **{stat['vph']}**",
        f"Topik kunci: {tok_md}",
        f"Waktu publish efektif (WIB): {jam_md}",
        "Strategi: konsisten format dominan + variasi (Short/Live) yang cepat perform."
    ]
    return ("### 📊 Ringkasan Niche (otomatis)\n"
            f"- **Label:** {label}\n- **Distribusi Format:** {fmt_md}\n\n"
            "### 🕒 Rata-rata Jam Publish (WIB)\n" + jam_md + "\n\n"
            "### 📈 Metrik Ringkas\n"
            f"- Sampel: **{stat['n']}** • Rata-rata Views: **{format_views(stat['avg'])}** • Median: **{format_views(stat['med'])}** • VPH: **{stat['vph']}**\n\n"
            "### 📌 Rangkuman Ketat\n" + "\n".join(f"- {b}" for b in bullets))

# ---------------- Handle submit ----------------
def search_multilang_union(api_key, user_keyword, order, max_per_query, video_type_label):
    """Cari banyak varian bahasa & gabungkan ID unik."""
    variants = expand_keyword_variants(user_keyword, max_variants=10)
    if not variants:
        return []
    all_ids = []
    seen = set()
    # region sampling untuk jangkau beberapa market besar
    REGIONS = ["US","ID","IN","JP","KR","DE","FR","ES","BR","RU","TR","SA","EG","VN","MX"]
    r_idx = 0
    for (q, lang) in variants:
        region = REGIONS[r_idx % len(REGIONS)]
        r_idx += 1
        ids = yt_search_ids(api_key, q, order, max_per_query, video_type_label, lang=lang, region=region)
        for vid in ids:
            if vid not in seen:
                all_ids.append(vid); seen.add(vid)
    return all_ids[:120]  # batas aman

if submit:
    st.session_state.keyword_input = keyword
    if not keyword.strip():
        st.info("📈 Menampilkan trending (default US)")
        videos_all = get_trending(st.session_state.api_key, st.session_state.get("max_per_order", 15))
    else:
        st.info(f"🔎 Riset keyword (lintas bahasa): {keyword}")
        order = map_sort_option(sort_option)
        # >>>> Multilingual search di sini
        ids = search_multilang_union(
            st.session_state.api_key, keyword, order,
            st.session_state.get("max_per_order", 15),
            st.session_state.get("video_type","Semua")
        )
        # fetch detail batched (maks 50 id per panggilan)
        videos_all = []
        for i in range(0, len(ids), 50):
            videos_all.extend(yt_videos_detail(st.session_state.api_key, ids[i:i+50]))

    # Post-filter agar benar-benar bersih
    videos_all = filter_by_video_type(videos_all, st.session_state.get("video_type","Semua"))
    videos_all = apply_client_sort(videos_all, sort_option, st.session_state.keyword_input)
    st.session_state.last_results = videos_all

    # Auto-ideas (ringkas; sama seperti sebelumnya, tidak diubah)
    st.session_state.auto_ideas = None

# ---------------- CSS ----------------
st.markdown("""
<style>
.yt-title a, .yt-title button { color:#e6e6e6; font-weight:700; font-size:16px; line-height:1.3; text-decoration:none; display:block; margin-top:8px; background:none; border:none; padding:0; text-align:left; cursor:pointer; }
.yt-title a:hover, .yt-title button:hover { color:#ffffff; text-decoration:underline; }
.yt-channel { color:#9aa0a6; font-size:13px; margin:6px 0 2px 0; }
.yt-meta { color:#9aa0a6; font-size:12px; margin-top:2px; }
.yt-dot { display:inline-block; width:4px; height:4px; background:#9aa0a6; border-radius:50%; margin:0 6px; vertical-align:middle; }
.chip { display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px; margin-right:6px; margin-top:6px; color:white; }
.chip-vph { background:#4b8bff; } /* VPH biru */
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

        st.markdown(f"### {v['title']}")
        st.markdown(f"👁 **{format_views(v['views'])}** &nbsp;&nbsp; ⚡ **{v['vph']}** &nbsp;&nbsp; ⏱ {format_rel_time(v['publishedAt'])} &nbsp;&nbsp; ⏳ {v.get('duration','-')}", unsafe_allow_html=True)

        c1, c2 = st.columns([2,1])
        with c1:
            st.video(yt_url)
        with c2:
            st.link_button("▶️ Buka di YouTube", yt_url, use_container_width=True)
            if ch_url: st.link_button("🌐 Kunjungi Channel", ch_url, use_container_width=True)
            st.text_input("Link Video", yt_url, key=f"copy_url_{vid}")
            st.caption(f"Channel: {v['channel']}")

        t1, t2, t3 = st.tabs(["ℹ️ Info", "✨ Asisten Konten AI", "📈 Analytics"])
        with t1:
            with st.expander("Deskripsi", expanded=False):
                st.write(v.get("description","Tidak ada deskripsi."))
            st.caption(f"Publish: {format_jam_utc(v['publishedAt'])} • ID: {vid}")

        def cache_get(task): return st.session_state.ai_cache.get(vid, {}).get(task)
        def cache_set(task, text): st.session_state.ai_cache.setdefault(vid, {})[task] = text

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
            colm = st.columns(4)
            colm[0].metric("Views", format_views(v["views"]))
            colm[1].metric("VPH", v["vph"])
            colm[2].metric("Durasi", v.get("duration","-"))
            colm[3].metric("Publish (rel)", format_rel_time(v["publishedAt"]))

        st.markdown("---")
        if st.button("❌ Tutup", key="close_dialog"):
            st.session_state.popup_video = None
            clear_open_param()
            st.rerun()

# ---------------- Render results ----------------
videos_to_show = st.session_state.last_results

open_param = get_qp().get("open")
if open_param and not st.session_state.get("popup_video"):
    for _v in st.session_state.last_results:
        if _v.get("id") == open_param:
            st.session_state.popup_video = _v
            if HAS_DIALOG: video_preview_dialog()
            break

def render_card_iframe(v):
    """Thumbnail/card pakai iframe HTML + badge LIVE/SHORT kiri atas."""
    vid = v["id"]
    thumb = v.get("thumbnail","")
    duration = v.get("duration","-")
    pill = ""
    if v.get("live") == "live":
        pill = '<span class="pill live">LIVE</span>'
    elif v.get("duration_sec",0) <= 60:
        pill = '<span class="pill short">SHORT</span>'
    html = f"""
<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  body {{ margin:0; background:transparent; }}
  .card {{ position:relative; display:block; width:100%; border-radius:12px; overflow:hidden; background:#111418; }}
  .thumb {{ width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }}
  .dur {{ position:absolute; right:8px; bottom:8px; background:rgba(0,0,0,.85); color:#fff; font-size:12px; padding:2px 6px; border-radius:6px; }}
  .pill {{ position:absolute; left:8px; top:8px; font-weight:700; font-size:12px; padding:2px 8px; border-radius:999px; color:#fff; }}
  .pill.live {{ background:#e53935; }}   /* LIVE merah */
  .pill.short {{ background:#1e88e5; }}  /* SHORT biru */
  a {{ text-decoration:none; }}
</style></head>
<body>
  <a href="?open={vid}" target="_top" class="card" title="Preview">
    {pill}
    <img class="thumb" src="{thumb}">
    <span class="dur">{duration}</span>
  </a>
</body></html>
"""
    st_html(html, height=210, scrolling=False)

if videos_to_show:
    cols = st.columns(3)
    all_titles, rows_for_csv = [], []
    for i, v in enumerate(videos_to_show):
        with cols[i % 3]:
            render_card_iframe(v)

            safe_title = html_lib.escape(v["title"])
            if st.button(safe_title, key=f"title_btn_{v['id']}"):
                st.session_state.popup_video = v
                set_qp(open=v["id"])
                if HAS_DIALOG: video_preview_dialog()

            st.markdown(f"<div class='yt-channel'>{html_lib.escape(v['channel'])}</div>", unsafe_allow_html=True)

            meta1 = f"{format_views(v['views'])} x ditonton <span class='yt-dot'></span> {format_rel_time(v['publishedAt'])}"
            st.markdown(f"<div class='yt-meta'>{meta1}</div>", unsafe_allow_html=True)

            st.markdown(f"<span class='chip chip-vph'>⚡ {v['vph']} VPH</span> <span class='yt-meta'>🕒 {format_jam_utc(v['publishedAt'])}</span>", unsafe_allow_html=True)

        all_titles.append(v["title"])
        rows_for_csv.append({
            "Judul": v["title"], "Channel": v["channel"], "Views": v["views"], "VPH": v["vph"],
            "Tanggal (relatif)": format_rel_time(v["publishedAt"]), "Jam Publish (UTC)": format_jam_utc(v["publishedAt"]),
            "Durasi": v.get("duration","-"), "Link": f"https://www.youtube.com/watch?v={v['id']}"
        })

    # -------- Fallback inline detail (tanpa st.dialog) --------
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
            if st.button("🧾 Ringkas Video Ini", key=f"btn_summary_{vid}"): cache_set("summary", ai_summary(v))
            if cache_get("summary"): st.markdown(cache_get("summary"))
            if st.button("🔑 Buat Tag SEO", key=f"btn_tags_{vid}"): cache_set("tags", ai_seo_tags(v))
            if cache_get("tags"): st.text_area("Tag SEO", cache_get("tags"), height=120, key=f"tags_area_{vid}")
        with c2:
            if st.button("📝 Buat Kerangka Skrip", key=f"btn_script_{vid}"): cache_set("script", ai_script_outline(v))
            if cache_get("script"): st.markdown(cache_get("script"))
            if st.button("✍️ Buat Judul Alternatif", key=f"btn_titles_{vid}"): cache_set("alt_titles", ai_alt_titles(v))
            if cache_get("alt_titles"): st.markdown(cache_get("alt_titles"))
            if st.button("🖼️ Ide Thumbnail", key=f"btn_thumb_{vid}"): cache_set("thumbs", ai_thumb_ideas(v))
            if cache_get("thumbs"): st.markdown(cache_get("thumbs"))
        if v.get("channelId"): st.markdown(f"[🌐 Kunjungi Channel YouTube](https://www.youtube.com/channel/{v['channelId']})")
        if st.button("❌ Tutup", key="close_popup"):
            st.session_state.popup_video = None
            clear_open_param()
            st.rerun()

    # -------- Tab Ide --------
    with tab2:
        vids = st.session_state.get("last_results", [])
        kw = st.session_state.get("keyword_input", "")
        if vids: st.markdown(render_niche_summary(vids, kw))
        else: st.info("Belum ada data. Silakan cari video dulu di tab 🔍.")
        if st.session_state.auto_ideas: st.markdown(st.session_state.auto_ideas)

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
    uniq_words, seen = [], set()
    for t in all_titles:
        for w in re.split(r"[^\w]+", t.lower()):
            if len(w) >= 3 and w not in STOPWORDS and w not in seen:
                uniq_words.append(w); seen.add(w)
    tag_string = ", ".join(uniq_words)
    if len(tag_string) > 500: tag_string = tag_string[:497] + "..."
    st.text_area("Tag (gabungan hasil pencarian)", tag_string, height=100, key="tag_area_global")

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
    st.info("Mulai dengan melakukan pencarian di tab 🔍, lalu klik **kartu** atau **judul** untuk membuka popup.")
