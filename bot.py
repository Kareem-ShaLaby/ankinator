#!/usr/bin/env python3
"""
كشف نتائج الامتحانات - Telegram Results Bot
Faculty of Medicine, Menoufia University — CNS2
"""

import os, json, time, logging, requests, pdfplumber, re, sqlite3
from io import BytesIO
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = "8674572158:AAGdPzN8vC8pqeY_QcfSeq9Ee2n0uRsbvKY"
DB_PATH   = Path(__file__).parent / "results.db"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── Telegram helpers ─────────────────────────────────────────────────────────
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def tg(method, **kw):
    try:
        r = requests.post(f"{BASE}/{method}", json=kw, timeout=30)
        return r.json()
    except Exception as e:
        log.error(f"tg({method}): {e}")
        return {}

def send(chat_id, text, **kw):
    return tg("sendMessage", chat_id=chat_id, text=text,
               parse_mode="HTML", **kw)

def get_updates(offset=0):
    r = tg("getUpdates", offset=offset, timeout=25,
            allowed_updates=["message"])
    return r.get("result", [])

def get_file_url(file_id):
    r = tg("getFile", file_id=file_id)
    fp = r.get("result", {}).get("file_path", "")
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}" if fp else None

# ─── Database ─────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS datasets (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL,
            year     TEXT,
            subject  TEXT,
            uploaded TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS students (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id  INTEGER NOT NULL,
            seq_no      TEXT,
            national_id TEXT,
            name        TEXT,
            histo       REAL,
            patho       REAL,
            pharma      REAL,
            parasito    REAL,
            physio      REAL,
            written     REAL,
            practical   REAL,
            year_work   REAL,
            total       REAL,
            FOREIGN KEY (dataset_id) REFERENCES datasets(id)
        );
        CREATE INDEX IF NOT EXISTS idx_seq  ON students(seq_no);
        CREATE INDEX IF NOT EXISTS idx_nat  ON students(national_id);
        CREATE INDEX IF NOT EXISTS idx_ds   ON students(dataset_id);
    """)
    con.commit(); con.close()

def save_dataset(name, year, subject, rows):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("INSERT INTO datasets (name,year,subject) VALUES (?,?,?)",
                (name, year, subject))
    ds_id = cur.lastrowid
    cur.executemany(
        """INSERT INTO students
           (dataset_id,seq_no,national_id,name,
            histo,patho,pharma,parasito,physio,
            written,practical,year_work,total)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(ds_id, r["seq"], r["nat"], r["name"],
          r["histo"], r["patho"], r["pharma"], r["parasito"], r["physio"],
          r["written"], r["practical"], r["year_work"], r["total"])
         for r in rows],
    )
    con.commit(); con.close()
    return len(rows)

def list_datasets():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT id,name,year,subject,uploaded FROM datasets ORDER BY id DESC LIMIT 20"
    ).fetchall()
    con.close()
    return rows

def lookup_student(query):
    q = query.strip()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT s.*, d.name AS dataset_name, d.year, d.subject
           FROM students s JOIN datasets d ON s.dataset_id=d.id
           WHERE s.seq_no=? OR s.national_id=?
           ORDER BY d.id DESC""",
        (q, q)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

# ─── PDF Parser ───────────────────────────────────────────────────────────────
def safe_float(v):
    try: return float(str(v).strip())
    except: return None

def fix_arabic(s):
    """pdfplumber reverses RTL text — fix it."""
    if not s: return s
    words = s.split()
    return ' '.join(w[::-1] for w in reversed(words))

def parse_pdf(data: bytes):
    """
    Parse CNS2 result sheet PDF.
    Column order (L→R as pdfplumber reads it, mirroring the RTL layout):
      [0] م  [1] رقم الجلوس  [2] اسم الطالب  [3] الرقم القومي
      [4..8] sub-scores: parasito(3), pharma(1.5), patho(1.5), physio(1.5), histo(1.5)
      — then empty cols for written/practical header —
      [11] year_work total   (last meaningful score, also = total for this sheet)
    """
    rows = []
    year = ""
    subject = "CNS2"

    with pdfplumber.open(BytesIO(data)) as pdf:
        # Extract year from text
        raw = pdf.pages[0].extract_text() or ""
        m = re.search(r'(\d{4}/\d{4})', raw)
        if m: year = m.group(1)

        for page in pdf.pages:
            table = page.extract_table()
            if not table: continue

            for raw_row in table:
                if not raw_row: continue
                cells = [str(c).strip() if c else "" for c in raw_row]

                # Last cell = row number (م)
                row_num_str = cells[-1]
                if not row_num_str.isdigit(): continue
                row_num = int(row_num_str)
                if not (1 <= row_num <= 2000): continue

                # Fixed positions from right (last 4 cols)
                seq    = cells[-2] if len(cells) >= 2 else ""
                name   = cells[-3] if len(cells) >= 3 else ""
                nat_id = cells[-4] if len(cells) >= 4 else ""

                # Validate seq
                if not re.match(r'^\d{7,8}$', seq): seq = ""

                # Fix reversed Arabic name
                name = fix_arabic(name)

                # Score cols (from left, after the 4 header cols on the right):
                # cells[2] = year_work total (9 or less)
                # cells[3] = parasito (3 or less)
                # cells[4] = pharma   (1.5 or less)
                # cells[5] = patho    (1.5 or less)
                # cells[6] = physio   (1.5 or less)
                # cells[7] = histo    (1.5 or less)
                # pdfplumber reads RTL sheet L→R, so subject columns are:
                # cells[2]=year_work total  cells[3]=physio  cells[4]=parasito
                # cells[5]=pharma  cells[6]=patho  cells[7]=histo
                year_work = safe_float(cells[2]) if len(cells) > 2 else None
                physio    = safe_float(cells[3]) if len(cells) > 3 else None
                parasito  = safe_float(cells[4]) if len(cells) > 4 else None
                pharma    = safe_float(cells[5]) if len(cells) > 5 else None
                patho     = safe_float(cells[6]) if len(cells) > 6 else None
                histo     = safe_float(cells[7]) if len(cells) > 7 else None

                # Skip rows with no scores
                if year_work is None: continue

                rows.append({
                    "seq":       seq,
                    "nat":       nat_id,
                    "name":      name,
                    "histo":     histo,
                    "patho":     patho,
                    "pharma":    pharma,
                    "parasito":  parasito,
                    "physio":    physio,
                    "written":   None,
                    "practical": None,
                    "year_work": year_work,
                    "total":     year_work,
                })

    # Deduplicate by seq or nat_id
    seen, unique = set(), []
    for r in rows:
        k = r["seq"] or r["nat"] or r["name"]
        if k and k not in seen:
            seen.add(k); unique.append(r)

    return unique, year, subject

# ─── Result formatter ─────────────────────────────────────────────────────────
MAX_OUT = {
    "histo": 1.5, "patho": 1.5, "pharma": 1.5,
    "parasito": 3.0, "physio": 1.5,
    "written": 27.0, "practical": 18.0,
    "year_work": 9.0,
}
LABELS = {
    "histo":     "هستولوجي",
    "physio":    "فسيولوجي",
    "patho":     "باثولوجي",
    "pharma":    "فارماكولوجي",
    "parasito":  "طفيليات",
    "written":   "الكتابي (End Module)",
    "practical": "العملي (End Module)",
    "year_work": "أعمال السنة",
}

def score_line(label, val, out_of):
    if val is None: return f"  • {label}: —"
    lost = out_of - val
    bar = "🟢" if lost == 0 else ("🟡" if lost <= 0.5 else "🔴")
    return f"  {bar} {label}: <b>{val:.2f}</b> / {out_of:.1f}  (− {lost:.2f})"

def build_result(s: dict) -> str:
    total     = s.get("total")
    year_work = s.get("year_work")
    written   = s.get("written")
    practical = s.get("practical")

    # Compute percentage based on what's available
    # If only year work sheet: show out of 9
    has_finals = written is not None or practical is not None
    if has_finals:
        max_score = 90
        grand_total = (year_work or 0) + (written or 0) + (practical or 0)
    else:
        max_score = 9
        grand_total = year_work or 0

    pct = grand_total / max_score * 100 if max_score else 0
    lost_total = max_score - grand_total

    lines = [
        f"🎓 <b>{s['name']}</b>",
        f"🪑 رقم الجلوس: <code>{s['seq_no']}</code>",
        f"🆔 الرقم القومي: <code>{s['national_id']}</code>",
        f"📋 {s.get('subject','CNS2')}  |  {s.get('year','')}",
        f"📂 {s.get('dataset_name','')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📝 <b>أعمال السنة التطبيقية</b>",
        score_line(LABELS["histo"],    s.get("histo"),    MAX_OUT["histo"]),
        score_line(LABELS["physio"],   s.get("physio"),   MAX_OUT["physio"]),
        score_line(LABELS["patho"],    s.get("patho"),    MAX_OUT["patho"]),
        score_line(LABELS["pharma"],   s.get("pharma"),   MAX_OUT["pharma"]),
        score_line(LABELS["parasito"], s.get("parasito"), MAX_OUT["parasito"]),
        score_line("إجمالي أعمال السنة", year_work, 9.0),
    ]

    if has_finals:
        lines += [
            "",
            "📚 <b>الامتحان النهائي</b>",
            score_line(LABELS["written"],   written,   MAX_OUT["written"]),
            score_line(LABELS["practical"], practical, MAX_OUT["practical"]),
        ]

    pass_fail = "✅ ناجح" if pct >= 60 else "❌ راسب"
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏆 <b>المجموع: {grand_total:.2f} / {max_score}</b>",
        f"📉 النقاط المفقودة: {lost_total:.2f}",
        f"📊 النسبة: {pct:.1f}%",
        f"🎯 الحالة: {pass_fail}",
    ]
    return "\n".join(lines)

# ─── State machine ────────────────────────────────────────────────────────────
user_state: dict[int, dict] = {}

def get_st(uid): return user_state.get(uid, {})
def set_st(uid, **kw): user_state[uid] = {**user_state.get(uid, {}), **kw}
def clr_st(uid): user_state.pop(uid, None)

ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = set(int(x) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit())

def is_admin(uid): return not ADMIN_IDS or uid in ADMIN_IDS

# ─── Message handler ──────────────────────────────────────────────────────────
def handle(msg: dict):
    chat_id = msg["chat"]["id"]
    uid     = msg["from"]["id"]
    text    = msg.get("text", "").strip()
    doc     = msg.get("document")
    st      = get_st(uid)

    if text == "/start":
        clr_st(uid)
        set_st(uid, mode="query")
        send(chat_id,
             "🎓 <b>بوت نتائج أعمال السنة — CNS2</b>\n"
             "كلية الطب | جامعة المنوفية\n\n"
             "أرسل <b>رقم الجلوس</b> أو <b>الرقم القومي</b>:\n"
             "<code>12240001</code>  أو  <code>30601011700001</code>")
        return

    if text == "/upload":
        if not is_admin(uid):
            send(chat_id, "⛔ هذا الأمر للمسؤولين فقط.")
            return
        set_st(uid, mode="await_name")
        send(chat_id,
             "📂 <b>رفع كشف نتائج</b>\n\n"
             "أرسل اسماً وصفياً للكشف:\n"
             "مثال: <code>أعمال سنة CNS2 - 2025/2026</code>")
        return

    if text == "/datasets":
        ds = list_datasets()
        if not ds:
            send(chat_id, "لا توجد كشوف بعد. أرسل /upload لرفع ملف.")
            return
        lines = ["📋 <b>الكشوف المتاحة:</b>\n"]
        for d in ds:
            lines.append(f"🔹 <b>{d[1]}</b>  |  {d[2]}  |  {d[3]}\n   📅 {d[4]}")
        send(chat_id, "\n".join(lines))
        return

    if text == "/help":
        send(chat_id,
             "📖 <b>المساعدة</b>\n\n"
             "• أرسل رقم جلوسك أو رقمك القومي\n"
             "• /start — بداية\n"
             "• /datasets — الكشوف المتاحة\n"
             "• /upload — رفع كشف PDF (مسؤولون)\n"
             "• /help — هذه الرسالة\n\n"
             "🟢 = بدون خسارة  🟡 = خسارة بسيطة  🔴 = خسارة")
        return

    # Awaiting dataset name
    if st.get("mode") == "await_name":
        set_st(uid, mode="await_file", pending_name=text)
        send(chat_id, f"✅ الاسم: <b>{text}</b>\n\nالآن أرسل ملف PDF.")
        return

    # Awaiting PDF file
    if st.get("mode") == "await_file":
        if not doc:
            send(chat_id, "⚠️ أرسل ملف PDF من فضلك.")
            return
        mime = doc.get("mime_type", "")
        fname = doc.get("file_name", "")
        if "pdf" not in mime.lower() and not fname.lower().endswith(".pdf"):
            send(chat_id, "⚠️ يجب أن يكون الملف بصيغة PDF.")
            return

        send(chat_id, "⏳ جاري تحليل الملف…")
        url = get_file_url(doc["file_id"])
        if not url:
            send(chat_id, "❌ تعذر تحميل الملف."); clr_st(uid); return

        pdf_bytes = requests.get(url, timeout=60).content
        try:
            rows, year, subject = parse_pdf(pdf_bytes)
        except Exception as e:
            log.exception("PDF parse error")
            send(chat_id, f"❌ خطأ في تحليل الملف:\n{e}"); clr_st(uid); return

        if not rows:
            send(chat_id, "⚠️ لم يُعثر على بيانات. تأكد من صيغة الملف.")
            clr_st(uid); return

        ds_name = st.get("pending_name", fname)
        save_dataset(ds_name, year, subject, rows)
        send(chat_id,
             f"✅ <b>تم الحفظ!</b>\n\n"
             f"📋 {ds_name}\n"
             f"🗓 {year}\n"
             f"👨‍🎓 عدد الطلاب: <b>{len(rows)}</b>")
        clr_st(uid); return

    # Default: student lookup
    if text.startswith("/"): 
        send(chat_id, "أمر غير معروف. أرسل /help."); return

    query = re.sub(r'\s+', '', text)
    if not re.match(r'^[\dA-Za-z]{6,}$', query):
        send(chat_id,
             "🔍 أرسل رقم الجلوس أو الرقم القومي للاستعلام.\n"
             "مثال: <code>12240001</code>")
        return

    results = lookup_student(query)
    if not results:
        send(chat_id,
             f"❌ لم يُعثر على نتيجة للرقم: <code>{query}</code>\n"
             "تأكد من الرقم وأعد المحاولة.")
        return

    for s in results[:3]:
        send(chat_id, build_result(s))

# ─── Main loop ────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        print("❌  BOT_TOKEN غير محدد.")
        print("    export BOT_TOKEN='your_token_here'")
        return

    init_db()
    log.info("✅ Bot started. Polling…")
    offset = 0
    while True:
        try:
            for upd in get_updates(offset):
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if msg:
                    try: handle(msg)
                    except Exception: log.exception("handle error")
        except KeyboardInterrupt:
            log.info("Stopped."); break
        except Exception as e:
            log.error(f"Poll error: {e}"); time.sleep(5)

if __name__ == "__main__":
    main()
