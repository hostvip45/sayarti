# ---- DB adapter ----
try:
    import psycopg  # PostgreSQL
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None

def get_conn():
    """ارجع اتصال قاعدة البيانات حسب المتوفر: Postgres أو SQLite."""
    db_url = os.environ.get("DATABASE_URL")
    if db_url:                           # تشغيل عبر PostgreSQL (Neon)
        conn = psycopg.connect(db_url, row_factory=dict_row)
        return conn
    else:                                # الوضع القديم (SQLite)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

from flask import current_app, flash, Flask, g, redirect, render_template, request, Response, send_file, session, url_for
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
import csv
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import arabic_reshaper
from bidi.algorithm import get_display
import requests

# ---------- Config ----------
DB_PATH = os.path.join(os.path.dirname(__file__), "sayarti.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-key")
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY


# --- context processor: provides has_endpoint() and current_user.is_authenticated ---
@app.context_processor
def utility_processor():
    def has_endpoint(name: str) -> bool:
        try:
            return name in current_app.view_functions
        except Exception:
            return False
    class CurrentUser:
        @property
        def is_authenticated(self):
            try:
                return bool(g.user)
            except Exception:
                return False
    return dict(has_endpoint=has_endpoint, current_user=CurrentUser())
# --- end context processor ---


# ---------- DB Helpers ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    with open(os.path.join(os.path.dirname(__file__), "schema.sql"), mode="r", encoding="utf-8") as f:
        db.executescript(f.read())
    db.commit()

def ensure_admin():
    db = get_db()
    cur = db.execute("SELECT id FROM users WHERE email = ?", ("admin@sayarti.local",))
    row = cur.fetchone()
    if row is None:
        db.execute(
            "INSERT INTO users (name, email, password_hash, role, is_approved, is_active, created_at) VALUES (?,?,?,?,?,?,?)",
            ("المشرف", "admin@sayarti.local", generate_password_hash("admin123"), "admin", 1, 1, datetime.now().isoformat())
        )
        db.commit()

# ---------- Light DB Migrations ----------
def _apply_light_migrations():
    """
    يضيف أعمدة استرجاع كلمة المرور إذا لم تكن موجودة:
    - users.reset_token TEXT
    - users.reset_expires TEXT
    """
    db = get_db()
    cols = [r["name"] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    need_commit = False
    if "reset_token" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN reset_token TEXT")
        need_commit = True
    if "reset_expires" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN reset_expires TEXT")
        need_commit = True
    if need_commit:
        db.commit()
        print("[DB] Light migration: added reset_token, reset_expires to users")

# تشغيل الهجرة مرة واحدة فقط (متوافق مع Flask 3.x)
_migrated_once = False
@app.before_request
def _run_light_migrations_once():
    global _migrated_once
    if not _migrated_once:
        _apply_light_migrations()
        _migrated_once = True

@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        db = get_db()
        g.user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

# ---------- Auth ----------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        if not name or not email or not password:
            flash("يرجى تعبئة جميع الحقول.", "error")
            return render_template("register.html")
        db = get_db()
        exists = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if exists:
            flash("هذا البريد مسجل مسبقاً.", "error")
            return render_template("register.html")
        db.execute(
            "INSERT INTO users (name, email, password_hash, role, is_approved, is_active, created_at) VALUES (?,?,?,?,?,?,?)",
            (name, email, generate_password_hash(password), "user", 0, 1, datetime.now().isoformat())
        )
        db.commit()
        flash("تم التسجيل بنجاح. انتظر موافقة المشرف.", "info")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            if user["is_approved"]==0 and user["role"]!="admin":
                flash("حسابك قيد الموافقة. يرجى الانتظار.", "warning")
                return render_template("login.html")
            if user["is_active"]==0:
                flash("تم إيقاف حسابك. تواصل مع المشرف.", "error")
                return render_template("login.html")
            session.clear()
            session["user_id"] = user["id"]
            db.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().isoformat(), user['id']))
            db.commit()
            flash("مرحباً بك!", "success")
            return redirect(url_for("home"))
        flash("بيانات الدخول غير صحيحة.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("تم تسجيل الخروج.", "info")
    return redirect(url_for("login"))

def login_required(view):
    from functools import wraps
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    from functools import wraps
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        if g.user["role"] != "admin":
            flash("هذه الصفحة للمشرف فقط.", "error")
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped

# ---------- Home (Dashboard) ----------
@app.route("/")
@login_required
def home():
    db = get_db()
    if g.user["role"]=="admin":
        cars_cnt = db.execute("SELECT COUNT(*) c FROM cars").fetchone()["c"]
        maint_cnt = db.execute("SELECT COUNT(*) c FROM maintenance").fetchone()["c"]
        upcoming_rows = db.execute("""
            SELECT m.*, c.car_type, c.model FROM maintenance m
            JOIN cars c ON c.id=m.car_id
            WHERE m.next_maintenance_date IS NOT NULL
              AND date(m.next_maintenance_date) <= date('now','+30 day')
            ORDER BY m.next_maintenance_date ASC LIMIT 10
        """).fetchall()
    else:
        cars_cnt = db.execute("SELECT COUNT(*) c FROM cars WHERE owner_id=?", (g.user["id"],)).fetchone()["c"]
        maint_cnt = db.execute("""
            SELECT COUNT(*) c FROM maintenance m
            JOIN cars c ON c.id=m.car_id
            WHERE c.owner_id=?
        """, (g.user["id"],)).fetchone()["c"]
        upcoming_rows = db.execute("""
            SELECT m.*, c.car_type, c.model FROM maintenance m
            JOIN cars c ON c.id=m.car_id
            WHERE c.owner_id=? AND m.next_maintenance_date IS NOT NULL
              AND date(m.next_maintenance_date) <= date('now','+30 day')
            ORDER BY m.next_maintenance_date ASC LIMIT 10
        """, (g.user["id"],)).fetchall()
    stats = {"cars": cars_cnt, "maint": maint_cnt, "upcoming": len(upcoming_rows)}
    return render_template("home.html", stats=stats, upcoming_rows=upcoming_rows)

# ---------- Admin: users ----------
@app.route("/admin/users", methods=["GET","POST"])
@admin_required
def admin_users():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        uid = request.form.get("user_id")
        if not uid:
            return redirect(url_for("admin_users"))
        if action == "approve":
            db.execute("UPDATE users SET is_approved=1 WHERE id=?", (uid,))
        elif action == "reject":
            db.execute("DELETE FROM users WHERE id=?", (uid,))
        elif action == "promote":
            db.execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
        elif action == "demote":
            admins = db.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin' AND is_approved=1 AND is_active=1").fetchone()["c"]
            if int(uid) != int(g.user["id"]) or admins > 1:
                db.execute("UPDATE users SET role='user' WHERE id=?", (uid,))
        elif action == "delete":
            if int(uid) != int(g.user["id"]):
                db.execute("DELETE FROM users WHERE id=?", (uid,))
        elif action == "suspend":
            if int(uid) != int(g.user["id"]):
                db.execute("UPDATE users SET is_active=0 WHERE id=?", (uid,))
        elif action == "activate":
            db.execute("UPDATE users SET is_active=1 WHERE id=?", (uid,))
        elif action == "resetpwd":
            import secrets
            new_pwd = secrets.token_hex(3)
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_pwd), uid))
            db.commit()
            flash(f"تم تعيين كلمة مرور مؤقتة: <b>{new_pwd}</b>", "info")
            return redirect(url_for("admin_users"))
        db.commit()
        return redirect(url_for("admin_users"))
    pending = db.execute("SELECT * FROM users WHERE is_approved=0").fetchall()
    approved = db.execute("SELECT * FROM users WHERE is_approved=1").fetchall()
    return render_template("admin_users.html", pending=pending, approved=approved)

# ---------- Cars ----------
@app.route("/cars/add", methods=["GET","POST"])
@login_required
def add_car():
    db = get_db()
    if request.method == "POST":
        car_type = request.form.get("car_type","").strip()
        model = request.form.get("model","").strip()
        if not car_type or not model:
            flash("يرجى تعبئة جميع الحقول.", "error")
            return render_template("add_car.html")
        db.execute("INSERT INTO cars (car_type, model, owner_id) VALUES (?,?,?)",(car_type, model, g.user["id"]))
        db.commit()
        flash("تمت إضافة السيارة.", "success")
        return redirect(url_for("home"))
    return render_template("add_car.html")

# ---------- Maintenance Types ----------
@app.route("/maintenance_types/add", methods=["GET","POST"])
@login_required
def add_maintenance_type():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name","").strip()
        if not name:
            flash("يرجى إدخال اسم نوع الصيانة.", "error")
            return render_template("add_maintenance_type.html")
        row = db.execute("SELECT id FROM maintenance_types WHERE name=?", (name,)).fetchone()
        if row:
            flash("النوع موجود مسبقاً.", "warning")
            return render_template("add_maintenance_type.html")
        db.execute("INSERT INTO maintenance_types (name) VALUES (?)", (name,))
        db.commit()
        flash("تمت إضافة نوع الصيانة.", "success")
        return redirect(url_for("home"))
    return render_template("add_maintenance_type.html")

# ---------- Maintenance: Add ----------
@app.route("/maintenance/add", methods=["GET","POST"])
@login_required
def add_maintenance():
    db = get_db()
    if g.user["role"] == "admin":
        cars = db.execute("SELECT * FROM cars ORDER BY id DESC").fetchall()
    else:
        cars = db.execute("SELECT * FROM cars WHERE owner_id=? ORDER BY id DESC", (g.user["id"],)).fetchall()
    mtypes = db.execute("SELECT * FROM maintenance_types ORDER BY name").fetchall()

    if request.method == "POST":
        maintenance_date = request.form.get("maintenance_date") or datetime.now().strftime("%Y-%m-%d")
        car_id = request.form.get("car_id")
        maintenance_type = request.form.get("maintenance_type")
        mileage = request.form.get("mileage") or None
        cost = request.form.get("cost") or None
        service_center = request.form.get("service_center","").strip()
        notes = request.form.get("notes","").strip()
        next_maintenance_date = request.form.get("next_maintenance_date") or None

        if not car_id or not maintenance_type:
            flash("يرجى اختيار السيارة ونوع الصيانة.", "error")
            return render_template("add_maintenance.html", cars=cars, mtypes=mtypes)

        db.execute("""
            INSERT INTO maintenance
            (maintenance_date, car_id, maintenance_type, mileage, cost, service_center, notes, next_maintenance_date, created_by)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (maintenance_date, car_id, maintenance_type, mileage, cost, service_center, notes, next_maintenance_date, g.user["id"]))
        db.commit()
        flash("تم تسجيل الصيانة.", "success")
        return redirect(url_for("reports"))
    return render_template("add_maintenance.html", cars=cars, mtypes=mtypes)

# ---------- Change Password ----------
@app.route("/account/password", methods=["GET","POST"])
@login_required
def change_password():
    if request.method == "POST":
        cur = request.form.get("current","")
        n1 = request.form.get("new1","")
        n2 = request.form.get("new2","")
        if not cur or not n1 or not n2:
            flash("يرجى تعبئة جميع الحقول.", "error")
            return render_template("change_password.html")
        if not check_password_hash(g.user["password_hash"], cur):
            flash("الكلمة الحالية غير صحيحة.", "error")
            return render_template("change_password.html")
        if n1 != n2:
            flash("تأكيد كلمة المرور غير مطابق.", "error")
            return render_template("change_password.html")
        db = get_db()
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(n1), g.user["id"]))
        db.commit()
        flash("تم تغيير كلمة المرور بنجاح.", "success")
        return redirect(url_for("home"))
    return render_template("change_password.html")

# ========== PDF Arabic Helpers (robust with Windows fonts) ==========
def _register_arabic_font():
    """
    يسجّل خطًا عربيًا تلقائيًا.
    الترتيب:
      1) خطوط داخل المشروع (static/fonts)
      2) خطوط نظام ويندوز (Traditional Arabic/Tahoma/Arial/Arial Unicode/…)
      3) مسارات شائعة في لينكس/ماك (احتياط)
    يرجّع اسم الخط المسجّل أو 'Helvetica' كبديل.
    """
    candidates = []

    # 1) Project fonts
    base = os.path.join(os.path.dirname(__file__), "static", "fonts")
    candidates += [
        ("Amiri", os.path.join(base, "Amiri-Regular.ttf")),
        ("NotoNaskhArabic", os.path.join(base, "NotoNaskhArabic-Regular.ttf")),
        ("DejaVuSans", os.path.join(base, "DejaVuSans.ttf")),
    ]

    # 2) Windows fonts
    win_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    candidates += [
        ("TraditionalArabic", os.path.join(win_fonts, "trado.ttf")),
        ("Tahoma",            os.path.join(win_fonts, "tahoma.ttf")),
        ("Arial",             os.path.join(win_fonts, "arial.ttf")),
        ("ArialUnicodeMS",    os.path.join(win_fonts, "arialuni.ttf")),
        ("SegoeUI",           os.path.join(win_fonts, "segoeui.ttf")),
        ("TimesNewRoman",     os.path.join(win_fonts, "times.ttf")),
    ]

    # 3) Linux/mac fallback (common locations)
    linux_dirs = ["/usr/share/fonts/truetype", "/usr/local/share/fonts", os.path.expanduser("~/.fonts")]
    linux_files = [
        ("NotoNaskhArabic", "NotoNaskhArabic-Regular.ttf"),
        ("DejaVuSans",      "DejaVuSans.ttf"),
        ("Amiri",           "Amiri-Regular.ttf"),
    ]
    for d in linux_dirs:
        for fam, fn in linux_files:
            candidates.append((fam, os.path.join(d, fn)))

    mac_dirs = ["/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    mac_files = [
        ("GeezaPro", "GeezaPro.ttf"),
        ("ArialUnicodeMS", "Arial Unicode.ttf"),
        ("NotoNaskhArabic", "NotoNaskhArabic-Regular.ttf"),
        ("Amiri", "Amiri-Regular.ttf"),
    ]
    for d in mac_dirs:
        for fam, fn in mac_files:
            candidates.append((fam, os.path.join(d, fn)))

    for family, path in candidates:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 30 * 1024:  # >=30KB to avoid placeholders
                pdfmetrics.registerFont(TTFont(family, path))
                print(f"[PDF] Arabic font loaded: {family} -> {path}")
                return family
            else:
                print(f"[PDF] Font missing or too small: {path}")
        except Exception as e:
            print(f"[PDF] Failed to load {path}: {e}")
    print("[PDF] WARNING: using Helvetica fallback (no Arabic shaping).")
    return "Helvetica"

PDF_AR_FONT = _register_arabic_font()

def ar_txt(s):
    """تهيئة نص عربي (reshape + bidi)؛ يرجع نصًا قابلاً للرسم من اليمين لليسار."""
    if s is None:
        return ""
    try:
        reshaped = arabic_reshaper.reshape(str(s))
        return get_display(reshaped)
    except Exception:
        return str(s)

# ---------- Reports (Enhanced) ----------
@app.context_processor
def inject_dt():
    return dict(now=datetime.now, timedelta=timedelta)

def _apply_quick_filter():
    """Set date range based on qf parameter: today|this_week|this_month|last_30d."""
    qf = (request.args.get("qf") or "").strip()
    today = datetime.now().date()
    dfrom = request.args.get("from")
    dto = request.args.get("to")
    if dfrom or dto or not qf:
        return dfrom, dto, qf
    if qf == "today":
        dfrom = dto = today.isoformat()
    elif qf == "this_week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        dfrom, dto = start.isoformat(), end.isoformat()
    elif qf == "this_month":
        start = today.replace(day=1)
        if start.month == 12:
            next_start = start.replace(year=start.year+1, month=1)
        else:
            next_start = start.replace(month=start.month+1)
        end = next_start - timedelta(days=1)
        dfrom, dto = start.isoformat(), end.isoformat()
    elif qf == "last_30d":
        dfrom = (today - timedelta(days=30)).isoformat()
        dto = today.isoformat()
    return dfrom, dto, qf

def _get_fx_rate(base: str, target: str) -> float:
    base = (base or "SAR").upper()
    target = (target or "SAR").upper()
    if base == target:
        return 1.0
    try:
        url = f"https://api.exchangerate.host/convert?from={base}&to={target}"
        r = requests.get(url, timeout=4)
        j = r.json()
        if j and j.get("result"):
            return float(j["result"])
    except Exception:
        pass
    table = {("SAR","USD"): 0.2667, ("USD","SAR"): 3.75}
    return table.get((base, target), 1.0)

def _format_currency(value, currency):
    if value is None or value == "":
        return ""
    try:
        num = float(value)
    except Exception:
        return str(value)
    symbol = "﷼" if currency == "SAR" else "$"
    return f"{symbol}{num:,.2f}"

def _reports_base_filters(owner_id):
    cond = ["1=1"]
    params = []
    if g.user["role"] != "admin":
        cond.append("c.owner_id=?")
        params.append(owner_id)
    else:
        owner_q = request.args.get("owner_id")
        if owner_q:
            cond.append("c.owner_id=?")
            params.append(owner_q)

    dfrom, dto, _ = _apply_quick_filter()
    car_id = request.args.get("car_id") or None
    mtype = request.args.get("type") or None
    service_center = (request.args.get("sc") or "").strip() or None

    if dfrom:
        cond.append("date(m.maintenance_date) >= date(?)")
        params.append(dfrom)
    if dto:
        cond.append("date(m.maintenance_date) <= date(?)")
        params.append(dto)
    if car_id:
        cond.append("m.car_id = ?")
        params.append(car_id)
    if mtype:
        cond.append("m.maintenance_type = ?")
        params.append(mtype)
    if service_center:
        cond.append("m.service_center LIKE ?")
        params.append(f"%{service_center}%")

    return cond, params

def _reports_query_enhanced(user_id, group):
    db = get_db()
    cond, params = _reports_base_filters(user_id)
    where = " AND ".join(cond)

    if group == "month":
        grp = "substr(m.maintenance_date,1,7)"
        select_grp_label = "الشهر"
    elif group == "type":
        grp = "m.maintenance_type"
        select_grp_label = "نوع الصيانة"
    elif group == "car":
        grp = "c.car_type || ' - ' || c.model"
        select_grp_label = "السيارة"
    else:
        grp = None

    if grp:
        sql = f"""
            SELECT {grp} AS grp, COUNT(*) AS cnt, COALESCE(SUM(m.cost),0) AS total, MAX(m.maintenance_date) AS last_date
            FROM maintenance m
            JOIN cars c ON c.id=m.car_id
            WHERE {where}
            GROUP BY {grp}
            ORDER BY date(last_date) DESC, total DESC
        """
        rows = db.execute(sql, tuple(params)).fetchall()
        grand = sum([float(r['total']) for r in rows if r['total'] is not None])
        count = sum([int(r['cnt']) for r in rows])
        return {"mode": "grouped", "label": select_grp_label, "rows": rows, "total_cost": grand, "count": count}
    else:
        sql = f"""
            SELECT m.*, c.car_type, c.model, u.name as created_by_name
            FROM maintenance m
            JOIN cars c ON c.id = m.car_id
            LEFT JOIN users u ON u.id = m.created_by
            WHERE {where}
            ORDER BY date(m.maintenance_date) DESC, m.id DESC
        """
        rows = db.execute(sql, tuple(params)).fetchall()
        total_cost = sum([float(r['cost']) for r in rows if r['cost'] is not None])
        return {"mode": "detailed", "rows": rows, "total_cost": total_cost, "count": len(rows)}

def _reports_common_context():
    db = get_db()
    if g.user["role"] == "admin":
        cars = db.execute("""
            SELECT c.id, c.car_type || ' - ' || c.model AS label
            FROM cars c ORDER BY c.id DESC
        """).fetchall()
    else:
        cars = db.execute("""
            SELECT c.id, c.car_type || ' - ' || c.model AS label
            FROM cars c WHERE c.owner_id=? ORDER BY c.id DESC
        """, (g.user["id"],)).fetchall()
    mtypes = db.execute("SELECT name FROM maintenance_types ORDER BY name").fetchall()
    scs = db.execute("SELECT DISTINCT service_center FROM maintenance WHERE service_center IS NOT NULL AND service_center<>'' ORDER BY service_center").fetchall()
    return cars, mtypes, scs

@app.route("/reports")
@login_required
def reports():
    group = request.args.get("group","car")  # car | month | type | none
    data = _reports_query_enhanced(g.user["id"], group)
    cars, mtypes, scs = _reports_common_context()
    currency = (request.args.get('currency') or 'SAR').upper()
    fx_rate = _get_fx_rate('SAR', currency)
    return render_template(
        "reports.html",
        group=group,
        data=data,
        cars=cars, mtypes=mtypes, scs=scs,
        q_from=request.args.get("from") or "",
        q_to=request.args.get("to") or "",
        q_car=request.args.get("car_id") or "",
        q_type=request.args.get("type") or "",
        q_sc=request.args.get("sc") or "",
        currency=currency,
        fx_rate=fx_rate,
        qf=(request.args.get('qf') or ''),
    )

def _pdf_grouped(c, rows, label, currency, fx_rate):
    width, height = A4
    y = height - 30*mm
    c.setFont(PDF_AR_FONT, 14); c.drawRightString(190*mm, y, ar_txt("تقرير الصيانة (تجميعي)")); y -= 8*mm
    c.setFont(PDF_AR_FONT, 10); c.drawRightString(190*mm, y, ar_txt(f"تجميع حسب: {label}")); y -= 6*mm
    y -= 2*mm
    c.setFont(PDF_AR_FONT, 11)
    c.drawRightString(190*mm, y, ar_txt("المجموعة")); c.drawRightString(125*mm, y, ar_txt("عدد")); c.drawRightString(75*mm, y, ar_txt("الإجمالي"))
    y -= 6*mm; c.line(20*mm, y, 190*mm, y); y -= 4*mm; c.setFont("Helvetica", 10)
    total_all = 0.0
    for r in rows:
        if y < 30*mm:
            c.showPage(); y = height - 20*mm
            c.setFont(PDF_AR_FONT, 11)
            c.drawRightString(190*mm, y, ar_txt("المجموعة")); c.drawRightString(125*mm, y, ar_txt("عدد")); c.drawRightString(75*mm, y, ar_txt("الإجمالي"))
            y -= 10*mm; c.setFont("Helvetica", 10)
        c.setFont(PDF_AR_FONT, 10); c.drawRightString(190*mm, y, ar_txt(str(r["grp"])));
        c.setFont("Helvetica", 10); c.drawRightString(125*mm, y, str(r["cnt"]))
        amt = (float(r["total"]) if r["total"] is not None else 0.0) * fx_rate
        c.drawRightString(75*mm, y, f"{amt:.2f}")
        total_all += float(r["total"]); y -= 6*mm
    y -= 6*mm; c.setFont(PDF_AR_FONT, 12); c.drawRightString(190*mm, y, ar_txt(f"الإجمالي الكلي: {total_all*fx_rate:.2f}"))

def _pdf_detailed(c, rows, currency, fx_rate):
    width, height = A4
    y = height - 30*mm
    c.setFont(PDF_AR_FONT, 14); c.drawRightString(190*mm, y, ar_txt("تقرير الصيانة (تفصيلي)")); y -= 8*mm
    c.setFont(PDF_AR_FONT, 9)
    headers = ["التاريخ","السيارة","النوع","العداد","التكلفة","المركز","ملاحظات"]
    xs = [20, 45, 90, 120, 140, 165, 20]
    c.drawRightString(190*mm, y, ar_txt(headers[0])); c.drawRightString(160*mm, y, ar_txt(headers[1]))
    c.drawRightString(130*mm, y, ar_txt(headers[2])); c.drawRightString(110*mm, y, ar_txt(headers[3]))
    c.drawRightString(90*mm, y, ar_txt(headers[4])); c.drawRightString(70*mm, y, ar_txt(headers[5])); y -= 6*mm
    c.line(20*mm, y, 190*mm, y); y -= 4*mm; c.setFont("Helvetica", 9)

    for r in rows:
        if y < 30*mm:
            c.showPage(); y = height - 20*mm
            c.setFont("Helvetica-Bold", 9)
            c.drawString(xs[0]*mm, y, headers[0]); c.drawString(xs[1]*mm, y, headers[1])
            c.drawString(xs[2]*mm, y, headers[2]); c.drawString(xs[3]*mm, y, headers[3])
            c.drawString(xs[4]*mm, y, headers[4]); c.drawString(xs[5]*mm, y, headers[5]); y -= 10*mm
            c.setFont("Helvetica", 9)
        car_label = f"{r['car_type']} - {r['model']}"
        amt_sar = float(r["cost"]) if r["cost"] is not None else 0.0
        c.setFont("Helvetica", 9); c.drawRightString(190*mm, y, str(r["maintenance_date"]))
        c.setFont(PDF_AR_FONT, 9); c.drawRightString(160*mm, y, ar_txt(car_label))
        c.drawRightString(130*mm, y, ar_txt(str(r["maintenance_type"])))
        c.setFont("Helvetica", 9); c.drawRightString(110*mm, y, "" if r["mileage"] is None else str(r["mileage"]))
        c.drawRightString(90*mm, y, "" if r["cost"] is None else f"{amt_sar*fx_rate:.2f}")
        c.setFont(PDF_AR_FONT, 9); c.drawRightString(70*mm, y, ar_txt((r["service_center"] or "")[:18])); y -= 5*mm
        if r["notes"]:
            c.drawRightString(190*mm, y, ar_txt(f"- {r['notes'][:90]}")); y -= 4*mm

@app.route("/reports/export")
@login_required
def reports_export():
    fmt = request.args.get("fmt", "pdf")  # pdf | csv
    group = request.args.get("group", "car")
    data = _reports_query_enhanced(g.user["id"], group)

    # تعريف العملة وسعر الصرف مرة وحدة
    currency = (request.args.get("currency") or "SAR").upper()
    fx_rate = _get_fx_rate("SAR", currency)

    if fmt == "csv":
        def generate():
            if data["mode"] == "grouped":
                yield "group,count,total\n"
                for r in data["rows"]:
                    total = r["total"] if r["total"] is not None else 0
                    yield f"{r['grp']},{r['cnt']},{total*fx_rate:.2f}\n"
            else:
                yield "date,car,type,mileage,cost,service_center,notes\n"
                for r in data["rows"]:
                    car = f"{r['car_type']} - {r['model']}"
                    mileage = "" if r["mileage"] is None else r["mileage"]
                    cost = "" if r["cost"] is None else f"{float(r['cost'])*fx_rate:.2f}"
                    sc = (r["service_center"] or "").replace(',', ' ')
                    notes = (r["notes"] or "").replace('\n',' ').replace(',', ' ')
                    yield f"{r['maintenance_date']},{car},{r['maintenance_type']},{mileage},{cost},{sc},{notes}\n"
        return Response(generate(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=reports.csv"})
    else:
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        if data["mode"] == "grouped":
            _pdf_grouped(
                c,
                data["rows"],
                {"car": "السيارة", "month": "الشهر", "type": "نوع الصيانة"}.get(group, "المجموعة"),
                currency,
                fx_rate,
            )
        else:
            _pdf_detailed(c, data["rows"], currency, fx_rate)
        c.showPage()
        c.save()
        buf.seek(0)
        fname = f"report_{group}.pdf"
        return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=fname)

# ---------- Extra: Font check utilities ----------
@app.route("/__font_info")
def __font_info():
    base = os.path.join(os.path.dirname(__file__), "static","fonts")
    p1 = os.path.join(base, "Amiri-Regular.ttf")
    p2 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "trado.ttf")
    p3 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "tahoma.ttf")
    p4 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")
    p5 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialuni.ttf")
    lines = [
        f"PDF_AR_FONT={PDF_AR_FONT}",
        f"Amiri (project) exists={os.path.exists(p1)} size={(os.path.getsize(p1) if os.path.exists(p1) else 0)}",
        f"Windows trado.ttf exists={os.path.exists(p2)}",
        f"Windows tahoma.ttf exists={os.path.exists(p3)}",
        f"Windows arial.ttf exists={os.path.exists(p4)}",
        f"Windows arialuni.ttf exists={os.path.exists(p5)}",
    ]
    return Response("\n".join(lines), mimetype="text/plain")

@app.route("/__font_check")
def __font_check():
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont(PDF_AR_FONT, 16)
    c.drawRightString(190*mm, 270*mm, ar_txt("اختبار الخط العربي — سيارة، صيانة، تقرير"))
    c.showPage(); c.save(); buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=False, download_name="font_check.pdf")

# ---------- Forgot / Reset Password (single, consolidated) ----------
import secrets

@app.route("/forgot", methods=["GET","POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not email:
            flash("يرجى إدخال البريد الإلكتروني.", "error")
            return render_template("forgot.html")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            flash("إن كان البريد موجودًا سنرسل تعليمات الاستعادة.", "info")
            return redirect(url_for("login"))
        token = secrets.token_urlsafe(24)
        expires = (datetime.now() + timedelta(hours=1)).isoformat()
        db.execute("UPDATE users SET reset_token=?, reset_expires=? WHERE id=?", (token, expires, user["id"]))
        db.commit()
        reset_url = url_for("reset_password", token=token, _external=True)
        return render_template("reset_sent.html", reset_url=reset_url)
    return render_template("forgot.html")

@app.route("/reset/<token>", methods=["GET","POST"])
def reset_password(token):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE reset_token=?", (token,)).fetchone()
    if not user:
        flash("رابط غير صالح.", "error")
        return redirect(url_for("login"))
    exp = user["reset_expires"]
    try:
        if not exp or datetime.fromisoformat(exp) < datetime.now():
            flash("انتهت صلاحية الرابط. اطلب رابطاً جديداً.", "error")
            return redirect(url_for("forgot_password"))
    except Exception:
        flash("انتهت صلاحية الرابط. اطلب رابطاً جديداً.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        n1 = request.form.get("new1","")
        n2 = request.form.get("new2","")
        if not n1 or not n2:
            flash("يرجى تعبئة جميع الحقول.", "error")
            return render_template("reset.html")
        if n1 != n2:
            flash("تأكيد كلمة المرور غير مطابق.", "error")
            return render_template("reset.html")
        db.execute("UPDATE users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?",
                   (generate_password_hash(n1), user["id"]))
        db.commit()
        flash("تم تعيين كلمة المرور. تفضل بتسجيل الدخول.", "success")
        return redirect(url_for("login"))
    return render_template("reset.html")

# ---------- Manage: Cars + Maintenance Types ----------
@app.route("/manage", methods=["GET", "POST"])
@login_required
def manage():
    db = get_db()

    if request.method == "POST":
        act = request.form.get("action")

        # --- Cars ---
        if act == "car_add":
            car_type = (request.form.get("car_type") or "").strip()
            model = (request.form.get("model") or "").strip()
            if car_type and model:
                owner_id = g.user["id"]
                if g.user["role"] == "admin":
                    try:
                        owner_id = int(request.form.get("owner_id") or g.user["id"])
                    except Exception:
                        owner_id = g.user["id"]
                db.execute("INSERT INTO cars (car_type, model, owner_id) VALUES (?,?,?)", (car_type, model, owner_id))
                db.commit()
                flash("تمت إضافة السيارة.", "success")

        elif act == "car_edit":
            try:
                cid = int(request.form.get("car_id"))
            except Exception:
                cid = None
            car_type = (request.form.get("car_type") or "").strip()
            model = (request.form.get("model") or "").strip()
            if cid:
                if g.user["role"] == "admin":
                    db.execute("UPDATE cars SET car_type=?, model=? WHERE id=?", (car_type, model, cid))
                else:
                    db.execute("UPDATE cars SET car_type=?, model=? WHERE id=? AND owner_id=?", (car_type, model, cid, g.user["id"]))
                db.commit()
                flash("تم تحديث بيانات السيارة.", "success")

        elif act == "car_delete":
            try:
                cid = int(request.form.get("car_id"))
            except Exception:
                cid = None
            if cid:
                if g.user["role"] == "admin":
                    db.execute("DELETE FROM cars WHERE id=?", (cid,))
                else:
                    db.execute("DELETE FROM cars WHERE id=? AND owner_id=?", (cid, g.user["id"]))
                db.commit()
                flash("تم حذف السيارة.", "info")

        # --- Maintenance Types ---
        elif act == "mt_add":
            name = (request.form.get("name") or "").strip()
            if name:
                exists = db.execute("SELECT 1 FROM maintenance_types WHERE name=?", (name,)).fetchone()
                if exists:
                    flash("النوع موجود مسبقاً.", "warning")
                else:
                    db.execute("INSERT INTO maintenance_types (name) VALUES (?)", (name,))
                    db.commit()
                    flash("تمت إضافة نوع الصيانة.", "success")

        elif act == "mt_edit":
            try:
                mid = int(request.form.get("mt_id"))
            except Exception:
                mid = None
            name = (request.form.get("name") or "").strip()
            if mid and name:
                db.execute("UPDATE maintenance_types SET name=? WHERE id=?", (name, mid))
                db.commit()
                flash("تم تحديث اسم النوع.", "success")

        elif act == "mt_delete":
            try:
                mid = int(request.form.get("mt_id"))
            except Exception:
                mid = None
            if mid:
                db.execute("DELETE FROM maintenance_types WHERE id=?", (mid,))
                db.commit()
                flash("تم حذف النوع.", "info")

        return redirect(url_for("manage"))

    if g.user["role"] == "admin":
        cars = db.execute("""
            SELECT c.*, u.name as owner_name
            FROM cars c LEFT JOIN users u ON u.id=c.owner_id
            ORDER BY c.id DESC
        """).fetchall()
    else:
        cars = db.execute("SELECT * FROM cars WHERE owner_id=? ORDER BY id DESC", (g.user["id"],)).fetchall()

    mtypes = db.execute("SELECT * FROM maintenance_types ORDER BY name").fetchall()
    return render_template("manage.html", cars=cars, mtypes=mtypes, is_admin=(g.user["role"]=="admin"))

# ---------- CLI Init ----------
@app.cli.command("init-db")
def cli_init_db():
    with app.app_context():
        init_db()
        ensure_admin()
        _apply_light_migrations()
    print("DB initialized, default admin: admin@sayarti.local / admin123")

if __name__ == "__main__":
    # شغّل دائمًا مع الإقلاع: إنشاء قاعدة جديدة عند عدم وجودها + الهجرة الخفيفة
    with app.app_context():
        if not os.path.exists(DB_PATH):
            init_db()
            ensure_admin()
        _apply_light_migrations()
    app.run(debug=True, host="0.0.0.0", port=5000)
