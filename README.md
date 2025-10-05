# سيارتي برو v8.5 (نسخة مبسطة + تحسينات)

## الجديد في v8.5
- لوحة تحكم (Dashboard) بإحصائيات سريعة.
- صفحة **تعديل كلمة المرور** للمستخدم.
- **تصدير PDF** للتقارير مع تجميع حسب **السيارة** أو حسب **الشهر** + إجمالي التكاليف.

## التشغيل محليًا
```bash
python -m venv .venv
. .venv/bin/activate  # على ويندوز: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
- افتح: http://127.0.0.1:5000
- حساب المشرف الافتراضي: `admin@sayarti.local` / `admin123`

> ملاحظة: غيّر قيمة `SECRET_KEY` داخل `app.py` قبل النشر.


## استعادة كلمة المرور (نسيت كلمة المرور)
- من شاشة الدخول اضغط **"نسيت كلمة المرور؟"** وأدخل بريدك.
- سيظهر لك رابط استعادة (في وضع التطوير) صالح لمدة ساعة — افتحه لتعيين كلمة جديدة.

## أوامر الطوارئ (للمشرف)
```bash
# إنشاء/تحديث مشرف
flask --app app.py create-admin

# إعادة تعيين كلمة مرور لأي مستخدم
flask --app app.py reset-password

# إنشاء رابط استعادة وطباعة المسار (صالح لساعة)
flask --app app.py gen-reset-link
```


---


# دعم العربية في PDF + تحويل العملة تلقائيًا

**مهم:** ضع ملف خط عربي يدعم العربية في:
`static/fonts/Amiri-Regular.ttf`
يفضَّل خط [Amiri](https://github.com/alif-type/amiri) — استخدم الملف `Amiri-Regular.ttf`.

ثم ثبّت المتطلبات:
```bash
pip install -r requirements.txt
```

## العملة
- الواجهة وPDF يعرضان التكاليف حسب اختيار الحقل `currency` (SAR أو USD).
- السعر يجلبه تلقائيًا من exchangerate.host مع بديل احتياطي.


---


# النسخة الموحد — دمج جاهز
- تم تعديل `app.py` بإضافة context_processor يوفّر:
  - `has_endpoint(name)`
  - `current_user.is_authenticated` مبسّط عبر `g.user`
- تم إنشاء/استبدال `templates/base.html` بروابط شرطية + Footer ثابت بأسفل الصفحة.
- أضفنا `static/style.css` خفيف.

## التشغيل (ويندوز)
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## ملاحظات
- `sayarti.db` و `schema.sql` مرفقة كما رفعتها.
- لو عندك قوالب أخرى في مشروعك الأصلي، انسخها إلى مجلد `templates/` هنا.


---

# سيارتي برو — ترقية التقارير وإزالة السجلات

- تمت إزالة صفحة السجلات والاكتفاء بالتقارير.
- صفحة التقارير الآن تدعم:
  - فلاتر: التاريخ من/إلى، السيارة، نوع الصيانة، مركز الخدمة.
  - التجميع: حسب السيارة أو الشهر أو نوع الصيانة، أو عرض تفصيلي (بديل السجلات).
  - تصدير PDF وCSV من خلال زرَّين أعلى الصفحة.

## التشغيل
```bash
python -m venv .venv
. .venv/bin/activate  # على ويندوز: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
