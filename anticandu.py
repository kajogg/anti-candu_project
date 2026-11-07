import getpass, hashlib, os, random, re, secrets, shutil, sqlite3, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, timedelta, date
from statistics import mean, pstdev

if sys.version_info < (3, 8): sys.exit("Butuh Python 3.8 atau lebih baru.")
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

DB = os.environ.get("SADARLAYAR_DB", os.path.expanduser("~/.sadarlayar.db"))
CATS = ["edukasi", "produktif", "sosial", "hiburan", "game", "lainnya"]
APPS = {"edukasi": ["Ruangguru", "Duolingo", "Khan Academy", "Quipper"],
        "hiburan": ["YouTube", "Netflix", "TikTok", "Spotify"], "sosial": ["WhatsApp", "Instagram", "Discord"],
        "game": ["Roblox", "Mobile Legends", "Minecraft", "Free Fire"],
        "produktif": ["Google Docs", "Canva", "Calculator"]}
ACTIVITIES = {"membaca buku": 0.5, "olahraga": 0.5, "membantu pekerjaan rumah": 0.5,
              "belajar / PR": 0.3, "kegiatan kreatif": 0.4, "meditasi": 0.4}
BONUS_CAP, BREAK_AFTER, TOKEN_GAP = 30, 45, 300
NOTICE = ("TRANSPARANSI: aplikasi ini hanya mencatat NAMA APLIKASI AKTIF + KATEGORI + waktu.\n"
          "Tidak merekam layar, kamera, mikrofon, judul jendela, pesan, atau lokasi. Anak dapat melihat\n"
          "semua data ini lewat menu Transparansi di mode anak.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS child(id INTEGER PRIMARY KEY, name TEXT UNIQUE, age INT, daily_limit INT,
  self_limit INT, bed_start TEXT, bed_end TEXT);
CREATE TABLE IF NOT EXISTS session(id INTEGER PRIMARY KEY, child_id INT, app TEXT, cat TEXT, start REAL, end REAL);
CREATE INDEX IF NOT EXISTS ix_s ON session(child_id, start);
CREATE TABLE IF NOT EXISTS rule(pattern TEXT PRIMARY KEY, cat TEXT);
CREATE TABLE IF NOT EXISTS quiet(id INTEGER PRIMARY KEY, child_id INT, label TEXT, start TEXT, end TEXT, days TEXT);
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, child_id INT, name TEXT, minutes INT, ts REAL);
CREATE TABLE IF NOT EXISTS bonus(id INTEGER PRIMARY KEY, child_id INT, minutes INT, reason TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, actor TEXT, action TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""

def connect():
    db = sqlite3.connect(DB); db.row_factory = sqlite3.Row; db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM rule").fetchone():
        db.executemany("INSERT INTO rule VALUES(?,?)",
                       [(re.escape(a.lower().replace(" ", "")), c) for c, l in APPS.items() for a in l])
        db.commit()
    return db

def hm(s): h, m = map(int, s.split(":")); return h * 60 + m
def day_range(d): a = datetime.combine(d, datetime.min.time()).timestamp(); return a, a + 86400
def fmt_min(m): m = int(round(m)); return f"{m // 60}j{m % 60:02d}m" if m >= 60 else f"{m}m"
def clock(t): return datetime.fromtimestamp(t).strftime("%H:%M") if t else "-"
def overlap(s, e, a, b): return max(0.0, min(e, b) - max(s, a)) / 60
def audit(db, actor, action): db.execute("INSERT INTO audit(ts,actor,action) VALUES(?,?,?)", (time.time(), actor, action)); db.commit()

def classify(db, app):
    key = app.lower().replace(" ", "")
    for r in db.execute("SELECT * FROM rule"):
        if re.search(r["pattern"], key): return r["cat"]
    return "lainnya"

def set_pin(db, pin):
    salt = secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 100_000).hex()
    db.execute("REPLACE INTO meta VALUES('pin',?)", (f"{salt}${h}",)); db.commit()

def check_pin(db, pin):
    row = db.execute("SELECT v FROM meta WHERE k='pin'").fetchone()
    if not row: return True
    salt, h = row["v"].split("$")
    return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 100_000).hex(), h)

def add_session(db, cid, app, s, e):
    db.execute("INSERT INTO session(child_id,app,cat,start,end) VALUES(?,?,?,?,?)", (cid, app, classify(db, app), s, e))

def sessions(db, cid, d):
    a, b = day_range(d)
    return db.execute("SELECT app,cat,MAX(start,?) s,MIN(end,?) e FROM session WHERE child_id=? AND end>? AND start<? ORDER BY start",
                      (a, b, cid, a, b)).fetchall()

def bonus_today(db, cid, d):
    a, b = day_range(d)
    return min(BONUS_CAP, db.execute("SELECT COALESCE(SUM(minutes),0) FROM bonus WHERE child_id=? AND ts>=? AND ts<?", (cid, a, b)).fetchone()[0])

def eff_limit(db, ch, d):
    base = min(ch["daily_limit"], ch["self_limit"] or 10**6)
    return base + bonus_today(db, ch["id"], d)

def add_activity(db, ch, name, minutes, ts=None):
    ts = ts or time.time(); d = date.fromtimestamp(ts)
    rate = ACTIVITIES.get(name, 0.3)
    room = BONUS_CAP - bonus_today(db, ch["id"], d)
    gain = max(0, min(room, round(minutes * rate)))
    db.execute("INSERT INTO activity(child_id,name,minutes,ts) VALUES(?,?,?,?)", (ch["id"], name, minutes, ts))
    if gain: db.execute("INSERT INTO bonus(child_id,minutes,reason,ts) VALUES(?,?,?,?)", (ch["id"], gain, name, ts))
    audit(db, ch["name"], f"catat aktivitas '{name}' {minutes}m -> bonus {gain}m"); return gain

def quiet_hits(db, ch, d):
    a, _ = day_range(d); out = []
    S = sessions(db, ch["id"], d)
    for q in db.execute("SELECT * FROM quiet WHERE child_id=?", (ch["id"],)):
        if str(d.weekday()) not in q["days"].split(","): continue
        m = sum(overlap(r["s"], r["e"], a + hm(q["start"]) * 60, a + hm(q["end"]) * 60) for r in S)
        if m >= 1: out.append((q["label"], m))
    return out

def stats(db, ch, d):
    a, _ = day_range(d)
    st = dict(total=0.0, cat=defaultdict(float), hourly=[0.0] * 24, first=None, last=None, night=0.0, longest=0.0)
    bs, be = hm(ch["bed_start"]), hm(ch["bed_end"])
    wins = [(a - 86400 + bs * 60, a + be * 60), (a + bs * 60, a + 86400 + be * 60)] if bs > be else [(a + bs * 60, a + be * 60)]
    blocks = []
    for r in sessions(db, ch["id"], d):
        s, e = r["s"], r["e"]; m = (e - s) / 60
        st["total"] += m; st["cat"][r["cat"]] += m
        st["first"] = s if st["first"] is None else min(st["first"], s); st["last"] = e if st["last"] is None else max(st["last"], e)
        st["night"] += sum(overlap(s, e, x, y) for x, y in wins)
        t = s
        while t < e:
            dt = datetime.fromtimestamp(t); nxt = min(e, (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp())
            st["hourly"][dt.hour] += (nxt - t) / 60; t = nxt
        if blocks and s - blocks[-1][1] < TOKEN_GAP: blocks[-1][1] = max(blocks[-1][1], e)
        else: blocks.append([s, e])
    st["longest"] = max(((e - s) / 60 for s, e in blocks), default=0.0)
    return st

def insights(db, ch, today):
    out = []; st = stats(db, ch, today); lim = eff_limit(db, ch, today); pct = st["total"] / lim * 100 if lim else 0
    if pct >= 100: out.append(("bad", f"Batas hari ini terlampaui: {fmt_min(st['total'])} dari {fmt_min(lim)}."))
    elif pct >= 80: out.append(("warn", f"Sudah {pct:.0f}% dari batas hari ini ({fmt_min(st['total'])}/{fmt_min(lim)})."))
    hist = [stats(db, ch, today - timedelta(days=i)) for i in range(1, 15)]
    tot = [h["total"] for h in hist]
    if pstdev(tot) > 0 and (z := (st["total"] - mean(tot)) / pstdev(tot)) >= 2:
        out.append(("warn", f"Anomali: pemakaian hari ini z={z:.1f} di atas kebiasaan 14 hari (rata-rata {fmt_min(mean(tot))})."))
    if mean(tot[3:10]) > 0 and (r := mean(tot[:3]) / mean(tot[3:10])) > 1.25:
        out.append(("warn", f"Tren naik: 3 hari terakhir {100 * (r - 1):.0f}% lebih tinggi dari 7 hari sebelumnya."))
    nights = sum(1 for h in hist[:7] if h["night"] > 0)
    if st["night"] > 0 or nights >= 2:
        out.append(("bad", f"Aktivitas di jam tidur ({ch['bed_start']}-{ch['bed_end']}): {nights}/7 hari terakhir"
                            + (f", hari ini {fmt_min(st['night'])}." if st["night"] else ".")))
    early = [h["first"] for h in hist[:7] if h["first"] and datetime.fromtimestamp(h["first"]).hour < 6]
    if early: out.append(("warn", f"Pemakaian sebelum jam 06:00 terdeteksi {len(early)}x minggu ini."))
    if st["longest"] >= BREAK_AFTER * 2: out.append(("warn", f"Blok pemakaian tanpa jeda 5 menit: {fmt_min(st['longest'])}. Disarankan istirahat mata tiap 45 menit."))
    fun, edu = st["cat"]["hiburan"] + st["cat"]["game"], st["cat"]["edukasi"] + st["cat"]["produktif"]
    if st["total"] > 30 and fun > 0 and edu / fun < 0.25: out.append(("info", f"Komposisi: edukasi/produktif hanya {edu / (fun + edu) * 100:.0f}% dari waktu layar."))
    for label, m in quiet_hits(db, ch, today): out.append(("warn", f"Memakai gawai saat '{label}' selama {fmt_min(m)}."))
    if pct >= 80 or (fun and edu / fun < 0.25):
        done = {r[0] for r in db.execute("SELECT name FROM activity WHERE child_id=? AND ts>?", (ch["id"], time.time() - 7 * 86400))}
        for name in [n for n in ACTIVITIES if n not in done][:2] or list(ACTIVITIES)[:2]:
            out.append(("ok", f"Saran: {name} 30 menit -> +{round(30 * ACTIVITIES[name])} menit bonus (maks {BONUS_CAP}/hari)."))
    return out or [("ok", "Pola pemakaian sehat. Pertahankan!")]

def bar(v, m, w=20): f = 0 if m <= 0 else min(w, round(w * v / m)); return "█" * f + "░" * (w - f)
def spark(vals): mx = max(vals) or 1; return "".join(" ▁▂▃▄▅▆▇█"[min(8, int(v / mx * 8 + (0.999 if v > 0 else 0)))] for v in vals)
ICON = {"ok": "✔", "info": "ℹ", "warn": "▲", "bad": "✖"}

def p_today(db, ch, d):
    st = stats(db, ch, d); lim = eff_limit(db, ch, d); p = st["total"] / lim * 100 if lim else 0
    L = [f"{ch['name']} ({ch['age']} th) - {d:%a %d %b %Y}",
         f" Total {bar(st['total'], lim, 30)} {fmt_min(st['total'])}/{fmt_min(lim)} ({p:.0f}%)", ""]
    for c in CATS:
        if st["cat"][c] > 0: L.append(f" {c:<10}{bar(st['cat'][c], st['total'], 24)} {fmt_min(st['cat'][c])}")
    L += ["", f" Pertama pakai {clock(st['first'])}   Terakhir {clock(st['last'])}   Blok tanpa jeda terpanjang {fmt_min(st['longest'])}",
          f" Aktivitas jam tidur ({ch['bed_start']}-{ch['bed_end']}): {fmt_min(st['night'])}",
          "", " Per jam  " + spark(st["hourly"]), "          0     6     12    18    "]
    for label, m in quiet_hits(db, ch, d): L.append(f" Melanggar blok '{label}': {fmt_min(m)}")
    return L

def p_week(db, ch, d):
    L = [f"7 hari terakhir - {ch['name']}"]; tot = []; edu = fun = 0
    for i in range(6, -1, -1):
        x = d - timedelta(days=i); st = stats(db, ch, x); lim = eff_limit(db, ch, x); tot.append(st["total"])
        edu += st["cat"]["edukasi"] + st["cat"]["produktif"]; fun += st["cat"]["hiburan"] + st["cat"]["game"]
        L.append(f" {x:%a %d/%m} {bar(st['total'], lim, 20)} {fmt_min(st['total']):>7}  malam {fmt_min(st['night']):>6}  mulai {clock(st['first'])}")
    L += ["", f" Rata-rata {fmt_min(mean(tot))}/hari   Edukasi+produktif vs hiburan+game: {edu / max(1, edu + fun) * 100:.0f}% : {fun / max(1, edu + fun) * 100:.0f}%"]
    return L

def p_insights(db, ch, d): return [f"Wawasan - {ch['name']}"] + [f" {ICON[k]} {t}" for k, t in insights(db, ch, d)]

def p_summary(db, kids, d):
    L = [f"Ringkasan keluarga - {d:%A %d %B %Y}", ""]
    for ch in kids:
        st = stats(db, ch, d); lim = eff_limit(db, ch, d)
        top = max(st["cat"].items(), key=lambda x: x[1], default=("-", 0))[0]
        warns = sum(1 for k, _ in insights(db, ch, d) if k in ("warn", "bad"))
        L += [f" {ch['name']:<10}{bar(st['total'], lim, 24)} {fmt_min(st['total'])}/{fmt_min(lim)}  dominan: {top}",
              f"            malam {fmt_min(st['night'])} | pertama {clock(st['first'])} | {warns} peringatan"]
    return L

def p_activity(db, ch, d):
    L = [f"Aktivitas offline - {ch['name']}", f" Bonus hari ini: {bonus_today(db, ch['id'], d)}/{BONUS_CAP} menit", "",
         " Pilihan (nomor) -> tarif bonus:"] + [f"   {i}. {k:<26}x{v}" for i, (k, v) in enumerate(ACTIVITIES.items(), 1)] + [""]
    for r in db.execute("SELECT * FROM activity WHERE child_id=? ORDER BY ts DESC LIMIT 12", (ch["id"],)):
        L.append(f" {datetime.fromtimestamp(r['ts']):%d/%m %H:%M}  {r['name']:<26}{r['minutes']}m")
    return L

def p_rules(db, ch, d):
    L = [f"Aturan - {ch['name']}", f" Batas harian orang tua : {fmt_min(ch['daily_limit'])}",
         f" Batas pilihan anak     : {fmt_min(ch['self_limit']) if ch['self_limit'] else '-'}",
         f" Jam tidur              : {ch['bed_start']}-{ch['bed_end']}", "", " Blok tenang:"]
    for q in db.execute("SELECT * FROM quiet WHERE child_id=?", (ch["id"],)): L.append(f"   {q['label']:<18}{q['start']}-{q['end']}  hari:{q['days']}")
    return L

def p_transparency(db, ch, d):
    L = NOTICE.split("\n") + ["", "Log audit terakhir:"]
    for r in db.execute("SELECT * FROM audit ORDER BY ts DESC LIMIT 15"):
        L.append(f" {datetime.fromtimestamp(r['ts']):%d/%m %H:%M} [{r['actor']}] {r['action']}")
    return L

def active_app():
    try:
        if os.name == "nt":
            import ctypes; from ctypes import wintypes
            u = ctypes.windll.user32; pid = wintypes.DWORD(); u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(pid))
            out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid.value}", "/FO", "CSV", "/NH"], text=True, timeout=3)
            return out.split(",")[0].strip('"').replace(".exe", "") or None
        if sys.platform == "darwin":
            return subprocess.check_output(["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'], text=True, timeout=3).strip() or None
        if shutil.which("xdotool"):
            pid = subprocess.check_output(["xdotool", "getactivewindow", "getwindowpid"], text=True, timeout=3).strip()
            return open(f"/proc/{pid}/comm").read().strip() or None
    except Exception: pass
    return None

def monitor(db, ch, interval=5, probe=active_app):
    print(NOTICE + f"\n\nMemantau {ch['name']} tiap {interval}s. Ctrl+C untuk berhenti.\n")
    cur = start = cont = None; fired = set(); idle_since = None
    def flush(now):
        if cur and now - start >= 3: add_session(db, ch["id"], cur, start, now); db.commit()
    try:
        while True:
            now = time.time(); app = probe()
            if app != cur: flush(now); cur, start = app, now
            if app:
                idle_since = None; cont = cont or now
            else:
                idle_since = idle_since or now
                if now - idle_since >= TOKEN_GAP: cont = None
            d = date.today(); st = stats(db, ch, d); run = (now - start) / 60 if cur else 0
            lim = eff_limit(db, ch, d); used = st["total"] + run
            for th, msg in ((80, "80% batas tercapai"), (100, "BATAS HARIAN TERCAPAI - saatnya istirahat/aktivitas lain")):
                if used / lim * 100 >= th and (d, th) not in fired: fired.add((d, th)); print(f"\a[{datetime.now():%H:%M}] {ch['name']}: {msg}")
            if cont and (now - cont) / 60 >= BREAK_AFTER and ("brk", int(cont)) not in fired:
                fired.add(("brk", int(cont))); print(f"\a[{datetime.now():%H:%M}] Istirahat 5 menit & lihat jauh 20 detik (aturan 20-20-20).")
            time.sleep(interval)
    except KeyboardInterrupt: flush(time.time()); print("\nBerhenti. Data tersimpan.")

def seed(db, pin="1234"):
    for t in ("child", "session", "quiet", "activity", "bonus", "audit"): db.execute(f"DELETE FROM {t}")
    kids = [("Aisyah", 10, 120, "20:30", "05:30", dict(edukasi=.35, hiburan=.35, sosial=.1, game=.15, produktif=.05)),
            ("Rafi", 14, 180, "22:00", "05:30", dict(game=.4, hiburan=.25, sosial=.2, edukasi=.1, produktif=.05))]
    random.seed(7); now = time.time()
    for i, (n, age, lim, bs, be, w) in enumerate(kids, 1):
        db.execute("INSERT INTO child VALUES(?,?,?,?,NULL,?,?)", (i, n, age, lim, bs, be))
        for lbl, s, e, days in (("Sekolah", "07:00", "13:00", "0,1,2,3,4"), ("Maghrib", "18:00", "18:30", "0,1,2,3,4,5,6"), ("Jam belajar", "19:00", "20:00", "0,1,2,3,4,6")):
            db.execute("INSERT INTO quiet(child_id,label,start,end,days) VALUES(?,?,?,?,?)", (i, lbl, s, e, days))
        for back in range(21, 0, -1):
            d = date.today() - timedelta(days=back); a, _ = day_range(d); rem = lim * random.uniform(.6, 1.2) * (1.15 if d.weekday() >= 5 else 1)
            t = a + (9 if d.weekday() >= 5 else 15) * 3600 + random.randint(0, 3600)
            while rem > 5 and t < a + 21.5 * 3600:
                cat = random.choices(list(w), list(w.values()))[0]; m = min(rem, random.randint(15, 70))
                add_session(db, i, random.choice(APPS[cat]), t, t + m * 60); t += (m + random.randint(2, 40)) * 60; rem -= m
            if n == "Rafi" and back <= 6:
                add_session(db, i, "Mobile Legends", a + 23 * 3600 + 600, a + 23 * 3600 + 600 + random.randint(25, 60) * 60)
            if n == "Aisyah" and back in (2, 5, 9): add_activity(db, db.execute("SELECT * FROM child WHERE id=?", (i,)).fetchone(), "membaca buku", 40, a + 17 * 3600)
        for off, (app, m) in zip((200, 120, 60), (("Roblox", 45), ("YouTube", 30), ("Ruangguru", 40))): add_session(db, i, app, now - off * 60, now - (off - m) * 60 if off > m else now - 60)
    set_pin(db, pin); audit(db, "sistem", "data demo dibuat"); db.commit()
    print(f"Data demo siap (PIN orang tua: {pin}).")

def ask(msg): return input(msg).strip()

def ask_int(msg):
    try: return int(ask(msg))
    except ValueError: return None

def show(lines): print("\n" + "\n".join(lines) + "\n")

def ask_day():
    n = ask_int("Berapa hari lalu? (0 = hari ini): ")
    return date.today() - timedelta(days=max(0, n or 0))

def all_kids(db): return db.execute("SELECT * FROM child ORDER BY id").fetchall()
def get_child_by_id(db, cid): return db.execute("SELECT * FROM child WHERE id=?", (cid,)).fetchone()

def pick_child(db):
    kids = all_kids(db)
    if not kids: return None
    for i, k in enumerate(kids, 1): print(f"  {i}. {k['name']}")
    n = ask_int("Pilih anak (nomor): ")
    return kids[n - 1] if n and 1 <= n <= len(kids) else None

def menu_log_activity(db, ch):
    for i, (k, v) in enumerate(ACTIVITIES.items(), 1): print(f"  {i}. {k:<26}x{v}")
    n = ask_int("Nomor aktivitas: "); mn = ask_int("Durasi (menit): ")
    if not n or not mn or mn <= 0 or not 1 <= n <= len(ACTIVITIES): print("Input tidak valid."); return
    print(f"+{add_activity(db, ch, list(ACTIVITIES)[n - 1], mn)} menit bonus")

def menu_set_limit(db, ch):
    v = ask_int("Batas harian baru (menit): ")
    if not v or v <= 0: print("Input tidak valid."); return
    db.execute("UPDATE child SET daily_limit=?, self_limit=NULL WHERE id=?", (v, ch["id"]))
    audit(db, "orang tua", f"ubah batas {ch['name']} -> {v}m"); print("Batas tersimpan.")

def menu_set_bed(db, ch):
    s = ask("Jam tidur (HH:MM-HH:MM): ")
    if not re.fullmatch(r"\d\d:\d\d-\d\d:\d\d", s): print("Format salah."); return
    a, b = s.split("-"); db.execute("UPDATE child SET bed_start=?, bed_end=? WHERE id=?", (a, b, ch["id"]))
    audit(db, "orang tua", f"ubah jam tidur {ch['name']} {s}"); print("Jam tidur tersimpan.")

def menu_set_self_limit(db, ch):
    v = ask_int(f"Batas harian pilihanmu (menit, maks {ch['daily_limit']}): ")
    if v is not None and 15 <= v <= ch["daily_limit"]:
        db.execute("UPDATE child SET self_limit=? WHERE id=?", (v, ch["id"]))
        audit(db, ch["name"], f"atur batas sendiri {v}m"); print("Batas tersimpan.")
    else: print("Batas harus antara 15 menit dan batas orang tua.")

def menu_add_child(db):
    name = ask("Nama anak: "); age = ask_int("Umur: "); lim = ask_int("Batas harian (menit, default 120): ") or 120
    if not name or not age: print("Input tidak valid."); return
    try: db.execute("INSERT INTO child(name,age,daily_limit,bed_start,bed_end) VALUES(?,?,?,?,?)", (name, age, lim, "21:00", "05:30")); db.commit()
    except sqlite3.IntegrityError: print("Nama sudah ada."); return
    if not db.execute("SELECT 1 FROM meta WHERE k='pin'").fetchone(): set_pin(db, getpass.getpass("Buat PIN orang tua: "))
    audit(db, "orang tua", f"tambah anak {name}"); print(f"{name} ditambahkan.")

def menu_add_rule(db):
    pattern = ask("Pola nama aplikasi: ").lower()
    for i, c in enumerate(CATS, 1): print(f"  {i}. {c}")
    n = ask_int("Nomor kategori: ")
    if not pattern or not n or not 1 <= n <= len(CATS): print("Input tidak valid."); return
    db.execute("REPLACE INTO rule VALUES(?,?)", (pattern, CATS[n - 1])); audit(db, "orang tua", f"aturan {pattern}->{CATS[n - 1]}"); db.commit()
    print("Aturan tersimpan.")

PARENT_MENU = ["Ringkasan keluarga", "Laporan harian", "Laporan 7 hari", "Wawasan", "Aktivitas offline", "Aturan",
               "Catat aktivitas", "Ubah batas harian", "Ubah jam tidur", "Tambah anak", "Tambah aturan kategori",
               "Mulai monitor", "Ganti anak"]
CHILD_MENU = ["Hari ini", "7 hari", "Wawasan", "Aktivitas offline", "Catat aktivitas", "Atur batas sendiri", "Transparansi"]

def print_menu(title, items):
    print(f"\n=== {title} ===")
    for i, t in enumerate(items, 1): print(f"  {i}. {t}")
    print("  0. Kembali")

def parent_menu(db):
    ch = pick_child(db)
    if not ch: print("Pilihan tidak valid."); return
    cid = ch["id"]
    while True:
        ch = get_child_by_id(db, cid)
        print_menu(f"MODE ORANG TUA | {ch['name']}", PARENT_MENU)
        c = ask("Pilih: ")
        if c == "0": return
        elif c == "1": show(p_summary(db, all_kids(db), date.today()))
        elif c == "2": show(p_today(db, ch, ask_day()))
        elif c == "3": show(p_week(db, ch, date.today()))
        elif c == "4": show(p_insights(db, ch, date.today()))
        elif c == "5": show(p_activity(db, ch, date.today()))
        elif c == "6": show(p_rules(db, ch, date.today()))
        elif c == "7": menu_log_activity(db, ch)
        elif c == "8": menu_set_limit(db, ch)
        elif c == "9": menu_set_bed(db, ch)
        elif c == "10": menu_add_child(db)
        elif c == "11": menu_add_rule(db)
        elif c == "12": monitor(db, ch)
        elif c == "13":
            new = pick_child(db)
            if new: cid = new["id"]
            else: print("Pilihan tidak valid.")
        else: print("Pilihan tidak valid.")

def child_menu(db, ch):
    cid = ch["id"]
    while True:
        ch = get_child_by_id(db, cid)
        print_menu(f"MODE ANAK | {ch['name']}", CHILD_MENU)
        c = ask("Pilih: ")
        if c == "0": return
        elif c == "1": show(p_today(db, ch, ask_day()))
        elif c == "2": show(p_week(db, ch, date.today()))
        elif c == "3": show(p_insights(db, ch, date.today()))
        elif c == "4": show(p_activity(db, ch, date.today()))
        elif c == "5": menu_log_activity(db, ch)
        elif c == "6": menu_set_self_limit(db, ch)
        elif c == "7": show(p_transparency(db, ch, date.today()))
        else: print("Pilihan tidak valid.")

def main():
    db = connect()
    while True:
        print("\n=== SADARLAYAR ===\n  1. Masuk sebagai orang tua\n  2. Masuk sebagai anak\n  3. Buat data demo\n  0. Keluar")
        c = ask("Pilih: ")
        if c == "0": return
        elif c == "1":
            if not all_kids(db): print("Belum ada anak. Tambahkan dulu."); menu_add_child(db); continue
            if check_pin(db, getpass.getpass("PIN orang tua: ")): audit(db, "orang tua", "masuk mode orang tua"); parent_menu(db)
            else: print("PIN salah.")
        elif c == "2":
            ch = pick_child(db)
            if ch: child_menu(db, ch)
            else: print("Belum ada data anak atau pilihan tidak valid.")
        elif c == "3": seed(db)
        else: print("Pilihan tidak valid.")

if __name__ == "__main__":
    main()
