# ismp3 ♪

محوّل شخصي خفيف يحوّل **أي رابط أو ملف صوت/فيديو** إلى **MP3 بأقل حجم ممكن**.
واجهة عربية (RTL) تعمل من المتصفح، وأوامر CLI، وخادم محلي واحد بلا قاعدة بيانات.
مصمّم ليعمل على **Termux (أندرويد)** و**Replit** دون تعقيد.

- الافتراضي: **24 kbps · مونو · 24 kHz · CBR** — حوالي **10.8 MB لكل ساعة**.
- المحرّكان: [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) للتنزيل و[`ffmpeg`](https://ffmpeg.org/) للتحويل.
- لا React، لا FastAPI، لا PostgreSQL، لا Docker، لا خدمات وسيطة.

> **الرابط:** <https://github.com/ilyas1435/ismp3>
>
> [![CI](https://github.com/ilyas1435/ismp3/actions/workflows/ci.yml/badge.svg)](https://github.com/ilyas1435/ismp3/actions/workflows/ci.yml)

---

## المحتويات

- [المزايا](#المزايا)
- [التثبيت على Termux](#التثبيت-على-termux)
- [التشغيل على Replit](#التشغيل-على-replit)
- [الواجهة العربية](#الواجهة-العربية)
- [سطر الأوامر](#سطر-الأوامر)
- [الجودة والحجم](#الجودة-والحجم)
- [كوكيز YouTube](#كوكيز-youtube)
- [الرفع إلى Archive.org](#الرفع-إلى-archiveorg)
- [الإعدادات والملفات](#الإعدادات-والملفات)
- [واجهة API](#واجهة-api)
- [بنية المشروع](#بنية-المشروع)
- [حدود معروفة](#حدود-معروفة)
- [الترخيص](#الترخيص)

---

## المزايا

| الميزة | التفاصيل |
|---|---|
| من أي مصدر | YouTube، SoundCloud، Bandcamp، Archive.org، روابط مباشرة، ملفات محلية، مجلدات، ملفات ZIP/TAR/7z |
| قوائم التشغيل | تنزيل القائمة كاملة أو اختيار مقاطع (`1,3,5-9`) |
| أقل حجم | 8–320 kbps، CBR/ABR/VBR، مونو/ستيريو، تردد تلقائي أو يدوي |
| بيانات وصفية | العنوان، الفنان، الألبوم، السنة، رقم المقطع، رابط المصدر |
| غلاف اختياري | استخراج غلاف مصغّر وتضمينه في MP3 |
| منع التكرار | سجل JSON بمعرّفات المصادر، مع خيار `force` للتجاهل |
| مهام متزامنة | 1–8 عمّال، إلغاء فوري، إعادة محاولة، إعادة تسمية، حذف |
| تنظيم تلقائي | مسطّح، فنان، فنان/ألبوم، أو مجلد قائمة تشغيل |
| ZIP | جمع نتائج المهمة في أرشيف واحد |
| Archive.org | رفع النتائج كعنصر عام (اختياري) |
| واجهة عربية | RTL، وضع داكن/فاتح، تحديث فوري عبر SSE، تنزيل واستماع |
| أمان بسيط | يعمل محلياً افتراضياً، ورمز دخول تلقائي عند فتحه للشبكة |

---

## التثبيت على Termux

المتطلبات: Termux من F-Droid (وليس نسخة Play القديمة)، ومساحة كافية.

```bash
pkg update -y
pkg install -y python ffmpeg deno git

# منح الوصول إلى /sdcard (نافذة إذن تظهر مرة واحدة)
termux-setup-storage

git clone https://github.com/ilyas1435/ismp3.git
cd ismp3

# لا تضع الـ venv داخل /sdcard (لا يدعم symlinks)
python -m venv ~/.venvs/ismp3
source ~/.venvs/ismp3/bin/activate
python -m pip install -U pip
python -m pip install -e '.[ia]'

ismp3 doctor
```

أو استخدم السكربت الجاهز:

```bash
bash scripts/setup-termux.sh
```

للتشغيل:

```bash
source ~/.venvs/ismp3/bin/activate
ismp3 serve
```

ثم افتح الرابط الذي يطبعه (عادةً `http://127.0.0.1:8787/`).
على أندرويد يمكن أيضاً فتح `http://localhost:8787/` من المتصفح.

### مشاركة رابط من أي تطبيق

انسخ `scripts/termux-url-opener` إلى `~/.termux/termux-url-opener` ثم:

```bash
mkdir -p ~/.termux
cp scripts/termux-url-opener ~/.termux/termux-url-opener
chmod +x ~/.termux/termux-url-opener
```

بعدها عند اختيار «مشاركة» من تطبيق YouTube أو المتصفح واختيار Termux، يُضاف الرابط مباشرة.

### تشغيل دائم في الخلفية

```bash
termux-wake-lock
nohup ismp3 serve --no-browser > ~/.ismp3.log 2>&1 &
```

أندرويد قد يوقف التطبيق عند نفاد البطارية؛ استخدم `termux-wake-lock` واسمح بالعمل في الخلفية.

---

## التشغيل على Replit

1. افتح <https://replit.com> → **Create Repl** → **Import from GitHub**.
2. الصق `https://github.com/ilyas1435/ismp3` واختر **Import**.
3. Replit يقرأ `.replit` و`replit.nix` تلقائياً ويثبّت **ffmpeg** و**Deno** و Python.
4. من **Shell** نفّذ مرة واحدة:

   ```bash
   bash scripts/setup-replit.sh
   ```

5. اضغط **Run**. سيبدأ الخادم على `0.0.0.0:$PORT` ويطبع في الكونسول:
   - رابط المعاينة،
   - **رمز دخول** تلقائي (لأن الخادم مفتوح للشبكة) — افتح الرابط مع `?token=…`.

> Replit المجاني قد ينام عند عدم الاستخدام. عند إعادة التشغيل تبقى المهام في `data/` إن كان الملف persist، وإلا فاستخدم مجلداً دائماً من إعدادات Replit.
>
> YouTube قد يطلب تسجيل دخول من عناوين Replit. الحل العملي: أضف `cookies.txt` من متصفحك (انظر [كوكيز YouTube](#كوكيز-youtube)). لا يعيد ismp3 بناء Piped/Invidious — هذا مقصود.

---

## الواجهة العربية

```bash
ismp3 serve
```

- **روابط:** سطر لكل رابط/مسار.
- **رفع:** اسحب ملفات أو اخترها من الجهاز؛ تُرفع إلى الخادم ثم تُضاف للمهمة.
- **من الجهاز:** تصفّح مجلدات الهاتف/الخادم واختر ملفاً أو مجلداً.
- **خيارات متقدمة:** البتريت، النوع، التردد، القنوات، الترتيب، السرعة، عميل YouTube، القائمة، الغلاف، ZIP، تجاهل السجل.
- **المهام:** تقدم فوري، إلغاء، إعادة، إعادة تسمية، تنزيل، ZIP، رفع إلى Archive.org.
- **المكتبة:** كل ملفات MP3 في مجلد المخرجات مع استماع وتنزيل.
- **الإعدادات:** مجلد المخرجات، الافتراضيات، الكوكيز، مفاتيح Archive.org، رمز الدخول.

### رمز الدخول

- محلياً: بلا رمز افتراضياً.
- عند الربط على `0.0.0.0` (Replit/شبكة) يُولّد رمزاً ويطبعه في الطرفية.
- يمكن تثبيت رمز من الواجهة أو `config.toml` أو `--token`.
- الرمز يُحفظ في `localStorage` بعد فتح الرابط مرة واحدة.

---

## سطر الأوامر

```bash
ismp3 <رابط>                       # اختصار لـ add
ismp3 add <رابط أو ملف> [خيارات]
ismp3 add - < urls.txt             # قراءة من stdin
ismp3 add --pick 1,3,5-9 "https://www.youtube.com/playlist?list=…"
ismp3 add --bundle -o ~/Music "قائمة تشغيل"
ismp3 probe <رابط أو ملف>          # معاينة وتقدير الحجم
ismp3 list                          # المهام
ismp3 retry <id>
ismp3 rename <id> "اسم جديد"
ismp3 rm <id>... | --finished | --all
ismp3 cookies status|set|paste|rm
ismp3 upload-ia <id> [--identifier …]
ismp3 config [--set bitrate=32]
ismp3 doctor
ismp3 update
ismp3 serve [--host 0.0.0.0] [--port 8787] [--token …] [--no-browser]
```

### أهم خيارات `add`

| الخيار | المعنى |
|---|---|
| `-b, --bitrate` | 8–320 kbps (افتراضي 24) |
| `--preset tiny\|voice\|music\|hq` | إعداد جاهز |
| `--mode cbr\|abr\|vbr` | نوع البتريت |
| `--sample-rate` | `0` تلقائي، أو 8000…48000 |
| `--channels 1\|2` | مونو/ستيريو |
| `--speed` | 0.5–4.0 |
| `--cover / --no-cover` | تضمين غلاف |
| `--layout flat\|artist\|album\|playlist` | تنظيم المجلدات |
| `--pick` | اختيار مقاطع القائمة |
| `--bundle` | جمع النتائج في ZIP |
| `--force` | تجاهل سجل التكرار |
| `--single` | تجاهل قائمة التشغيل |
| `-o, --output` | مجلد المخرجات |
| `--dry-run` | معاينة بلا تنزيل |
| `--json` | مخرجات للبرمجة |

أمثلة:

```bash
# أصغر ملف ممكن لخطبة أو درس
ismp3 add --preset tiny "https://www.youtube.com/watch?v=…"

# موسيقى بجودة معقولة
ismp3 add --preset music -o /sdcard/Music "https://soundcloud.com/…"

# ألبوم كامل مع غلاف وZIP
ismp3 add --bundle --cover --layout album "https://archive.org/details/…"

# تحويل مجلد محلي كامل
ismp3 add /sdcard/Download/lectures
```

---

## الجودة والحجم

MP3 عند **24 kbps** صالح فقط ضمن MPEG-2 (ترددات 16/22.05/**24** kHz) أو MPEG-2.5 (8/11.025/12 kHz). لا يسمح المعيار بـ 24 kbps عند 44.1 kHz، لذلك يختار ismp3 التردد تلقائياً ويتحقق من التركيبة قبل التشغيل.

| البتريت | التردد التلقائي | ≈ الحجم/ساعة | الاستخدام |
|---:|---:|---:|---|
| 8k | 8 kHz | 3.5 MB | كلام واضح جداً، أصغر ما يمكن |
| 16k | 16 kHz | 7.0 MB | صوت/بودكاست |
| **24k** | **24 kHz** | **10.8 MB** | **الافتراضي** — كلام ومحاضرات |
| 32k | 44.1 kHz | 14.4 MB | صوت + موسيقى خفيفة |
| 64k | 44.1 kHz | 28.8 MB | موسيقى مقبولة |
| 128k | 44.1 kHz | 57.6 MB | موسيقى جيدة |

- المونو يقلّل الحجم للنصف تقريباً مقارنة بالستيريو.
- 24 kbps مخصّص للكلام؛ للموسيقى استخدم 64k أو 128k.
- تُكتب الوسوم (ID3v2.3 + ID3v1) دائماً. الغلاف اختياري لأنه يضيف عشرات الكيلوبايتات.

---

## كوكيز YouTube

عندما يظهر `Sign in to confirm you're not a bot`:

1. من متصفح على حاسوبك، ثبّت إضافة لتصدير الكوكيز بصيغة **Netscape `cookies.txt`**.
2. سجّل الدخول إلى YouTube ثم صدّر كوكيز `youtube.com`.
3. أضفها بأحد الطرق:

   ```bash
   ismp3 cookies set ~/Downloads/cookies.txt
   ismp3 cookies status
   ```

   أو من الواجهة: **الإعدادات → كوكيز YouTube → الصق المحتوى → حفظ**.

- الملف يُخزّن بصلاحية `0600` في `~/.local/share/ismp3/cookies.txt` افتراضياً.
- لا ترفع `cookies.txt` إلى GitHub (مستثنى في `.gitignore`).
- الكوكيز تنتهي صلاحيتها؛ أعد التصدير عند الحاجة.

---

## الرفع إلى Archive.org

1. أنشئ حساباً على Archive.org.
2. احصل على مفاتيح S3 من <https://archive.org/account/s3.php>.
3. احفظها:

   ```bash
   ismp3 upload-ia <job-id> --access "ACCESS" --secret "SECRET"
   # أو مرة واحدة:
   export IA_ACCESS_KEY=…
   export IA_SECRET_KEY=…
   # أو من الواجهة: الإعدادات → Archive.org
   ```

4. ارفع نتائج مهمة:

   ```bash
   ismp3 upload-ia <job-id> --identifier my-item-id
   ```

   ومن الواجهة: زر **🏛 رفع للأرشيف** بجانب المهمة المكتملة.

- العنصر يُنشر كـ `opensource_audio` و`mediatype=audio`.
- تأكد من حقوق نشر المحتوى قبل رفعه علناً.
- المكتبة المطلوبة: `pip install 'ismp3[ia]'` (مثبّتة مع سكربت Termux).

---

## الإعدادات والملفات

| المسار | المحتوى |
|---|---|
| `~/.config/ismp3/config.toml` | الإعدادات (يمكن تجاوزها بـ `ISMP3_CONFIG`) |
| `~/.local/share/ismp3/jobs.json` | المهام المحفوظة |
| `~/.local/share/ismp3/seen.json` | سجل منع التكرار |
| `~/.local/share/ismp3/cookies.txt` | كوكيز YouTube |
| `~/.local/share/ismp3/ia.json` | مفاتيح Archive.org (0600) |
| `$TMPDIR/ismp3/work/` | ملفات العمل المؤقتة |
| `/sdcard/Music/ismp3` | المخرجات الافتراضية على Termux |
| `./data/downloads` | المخرجات الافتراضية على Replit |

الافتراضيات المهمة في `config.toml`:

```toml
bitrate = 24
mode = "cbr"
sample_rate = 0
channels = 1
cover = false
layout = "flat"
jobs = 2
playlist = true
client = "auto"
```

غيّرها من الواجهة أو:

```bash
ismp3 config --set bitrate=32
ismp3 config --set layout=artist
```

---

## واجهة API

كل المسارات تحت `/api` وتعيد JSON. أضف `?token=…` أو `Authorization: Bearer …` إذا كان الرمز مفعّلاً.

| الطريقة | المسار | الوظيفة |
|---|---|---|
| GET | `/api/health` | فحص عام بلا رمز |
| GET | `/api/events` | SSE لتحديثات المهام |
| GET | `/api/jobs?search=&limit=` | قائمة المهام |
| POST | `/api/jobs` | إضافة مصادر |
| GET | `/api/jobs/<id>` | مهمة واحدة |
| POST | `/api/jobs/<id>/cancel` | إلغاء |
| POST | `/api/jobs/<id>/retry` | إعادة |
| POST | `/api/jobs/<id>/rename` | إعادة تسمية `{title}` |
| DELETE | `/api/jobs/<id>` | حذف المهمة وملفاتها |
| GET | `/api/jobs/<id>/download?index=` | تنزيل مخرج |
| GET | `/api/jobs/<id>/zip` | ZIP للمخرجات |
| POST | `/api/probe` | معاينة `{source}` |
| POST | `/api/upload?name=` | رفع ملف خام |
| GET | `/api/browse?path=` | تصفح المجلدات |
| GET | `/api/files` | ملفات مجلد المخرجات |
| GET/POST | `/api/settings` | قراءة/تحديث الإعدادات |
| GET/POST/DELETE | `/api/cookies` | إدارة الكوكيز |
| GET/POST/DELETE | `/api/ia` | مفاتيح Archive.org |
| GET | `/api/stats`, `/api/disk` | إحصاءات ومساحة |

مثال:

```bash
curl -s http://127.0.0.1:8787/api/health | jq
curl -s -X POST http://127.0.0.1:8787/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"sources":["https://youtu.be/…"],"settings":{"bitrate":24}}' | jq
```

---

## بنية المشروع

```text
src/ismp3/
  config.py       إعدادات TOML ومسارات المنصّات
  util.py         أدوات عامة، CancelToken، ZIP، مسارات آمنة
  naming.py       أسماء ملفات عربية آمنة وتنظيم المجلدات
  probe.py        ffprobe
  transcode.py    ffmpeg → MP3 والتحقق من التركيبة
  fetch.py        yt-dlp والتنزيل والتقارير العربية
  library.py      سجل منع التكرار JSON
  jobs.py         طابور المهام، القوائم، الأرشيف، الرفع
  archive_org.py  رفع Internet Archive
  cli.py          سطر الأوامر
  web.py          خادم HTTP + JSON API (stdlib فقط)
  static/         الواجهة العربية (HTML/CSS/JS)
scripts/          إعداد Termux/Replit ومشاركة الروابط
tests/            pytest
```

---

## حدود معروفة

- **استخدام شخصي**: لا حسابات متعددة ولا صلاحيات؛ الرمز حماية بسيطة فقط.
- **YouTube**: قد يفرض تحققاً أو قيوداً حسب الشبكة/الدولة. الكوكيز هي الحل المتوقع. لا توجد خدمات Piped/Invidious.
- **24 kbps**: ممتاز للكلام، ضعيف للموسيقى. ارفع البتريت للموسيقى.
- **أندرويد**: قد يوقف Termux في الخلفية؛ استخدم `termux-wake-lock`. بعض روابط `/sdcard` تحتاج إذن التخزين.
- **Replit**: قد ينام الخادم المجاني. ملفات `data/` تبقى فقط إذا كان التخزين دائماً.
- **Archive.org**: يتطلب حساباً ومفاتيح، والمحتوى مسؤوليتك القانونية.
- **لا تحويل مباشر إلى صيغ أخرى**: MP3 فقط بحسب طلب المشروع.

---

## التطوير والاختبار

```bash
python -m venv ~/.venvs/ismp3-dev
source ~/.venvs/ismp3-dev/bin/activate
pip install -e '.[dev,ia]'
pytest
ruff check src tests
```

اختبار سريع للتحويل:

```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=2" -ac 1 -ar 44100 /tmp/test.wav
ismp3 add /tmp/test.wav -o /tmp/ismp3-out
ls -lh /tmp/ismp3-out
```

---

## الترخيص

MIT — انظر [LICENSE](LICENSE).

> استخدم الأداة للمحتوى الذي تملك حقوقه أو المسموح بتنزيله. أنت مسؤول عن الالتزام بشروط الخدمات والقوانين المحلية.
