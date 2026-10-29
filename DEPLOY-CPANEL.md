# cPanel-এ লাইভ করা — MT5 ছাড়া, API ডেটা দিয়ে

cPanel/Passenger WSGI চালায়, WebSocket চালায় না — আর লিনাক্সে MetaTrader 5 টার্মিনালও হয় না।
তাই কোডে **hosted mode** যোগ করা হয়েছে: টার্মিনালের বদলে দাম আসে পাবলিক মার্কেট API থেকে, আর
ব্রাউজার WebSocket-এর বদলে HTTP-তে একই snapshot পোল করে। **কোনো MT5 লগইন লাগবে না।**

| কাজ করে ✅ | কাজ করে না ❌ |
|---|---|
| লাইভ চার্ট (ক্যান্ডেল + টিকার + টেপ) | Exness অ্যাকাউন্ট লগইন |
| Market Pressure Watch ডায়াল (01/02) | অটো-ট্রেডিং বট (MT5 ছাড়া অর্ডার হয় না) |
| ১২ venue-র Order Flow Terminal | MT5 থেকে ট্রেড হিস্ট্রি |
| নিউজ ইভেন্ট, ক্যালেন্ডার | — |

---

## ধাপ ০ — কোন ফাইলগুলো আপলোড করবেন

```
passenger_wsgi.py      ← নতুন: cPanel/Passenger entry point
requirements.txt
backend/               ← পুরোটা
web/                   ← পুরোটা
config/                ← ঐচ্ছিক (পুরোনো সেটিংস ধরে রাখতে চাইলে)
```

**আপলোড করবেন না:** `data/` (আসল trades.db), `tests/`, `docs/`, `ea/`, `tools/`, `Dockerfile`,
`docker-compose.yml`, `Caddyfile`, `start.bat`, `.git`।

> গঠনটা ভাঙা যাবে না: `passenger_wsgi.py`, `backend/` আর `web/` একই ফোল্ডারে থাকতে হবে।

---

## ধাপ ১ — cPanel-এ Python Application বানানো

1. cPanel → **Software → Setup Python App** → **CREATE APPLICATION**
2. **Python version**: `3.11` (৩.৯ বা তার উপরে যেকোনোটা)
3. **Application root**: `softradex`  → ফোল্ডারটা হবে `/home/USER/softradex`
4. **Application URL**: আপনার ডোমেইন বা `dashboard.yourdomain.com`
5. **Application startup file**: `passenger_wsgi.py`
6. **Application Entry point**: `application`
7. **CREATE**

এরপর **File Manager** দিয়ে ধাপ ০-এর ফাইলগুলো ঠিক `/home/USER/softradex`-এ তুলুন।

> `Setup Python App` অপশনটা না দেখলে হোস্টিং সাপোর্টকে বলুন "Python application / Passenger
> enabled" চালু করতে — অনেক shared প্ল্যানে এটা ডিফল্টে বন্ধ থাকে।

---

## ধাপ ২ — প্যাকেজ ইনস্টল

cPanel যে কমান্ডটা দেখায় (উপরে ডান দিকে "Enter to the virtual environment"), সেটাই ব্যবহার করুন:

```bash
source /home/USER/virtualenv/softradex/3.11/bin/activate
cd /home/USER/softradex
pip install --upgrade pip
pip install -r requirements.txt
```

`MetaTrader5` প্যাকেজটা নিজে থেকেই স্কিপ হবে (`sys_platform == "win32"`), তাই cPanel-এ কোনো
error দেবে না — ওটা Windows-only।

---

## ধাপ ৩ — চালু করা

1. Setup Python App পেজে **Environment variables**-এ (ঐচ্ছিক, কোড নিজেও ধরে নেয়):
   `SOFTRADEX_MODE` = `public`
2. **RESTART** চাপুন (ফাইল বদলালে প্রতিবার এটাই করতে হবে)
3. কিছুক্ষণ পর খুলুন: `https://yourdomain.com/api/health`

```json
{"ok": true, "connected": true, "source": "public", "hosted": true}
```

সাইট খুললে নিচের স্ট্যাটাসে **`http: live (polling)`** দেখাবে — এটাই ঠিক আছে, WebSocket নেই
বলে HTTP-তে ডেটা আসছে।

---

## ডেটা কোথা থেকে আসছে

| অংশ | সোর্স | রিফ্রেশ |
|---|---|---|
| চার্ট ক্যান্ডেল | Binance (crypto) + Yahoo (গোল্ড/ফরেক্স/ইন্ডেক্স/স্টক) REST | ৫ সেকেন্ড |
| দাম / 24h | `/api/chart/ticker` | ৫ সেকেন্ড |
| Watch ডায়াল, অ্যাকাউন্ট প্যানেল | `/api/state` (একই snapshot) | ২.৫ সেকেন্ড |
| ১২ venue order flow | ব্রাউজার সরাসরি Binance/Coinbase/Kraken/OKX/Bybit-এ | রিয়েল-টাইম |
| নিউজ | `/api/bot5` | ১ মিনিট |

সব সংখ্যা আসল মার্কেট ডেটা — কোনো সিমুলেশন নেই। মেটাল/ফরেক্সে Yahoo, ক্রিপ্টোতে Binance
ব্যবহার হয়, আর যে ইনস্ট্রুমেন্টে পাবলিক সোর্স নেই সেটা চার্টে "no data" বলবে (বানানো দাম দেখাবে না)।

---

## সমস্যা হলে

| লক্ষণ | কারণ / সমাধান |
|---|---|
| 500 Internal Server Error | Setup Python App-এ "Passenger log file" পাথ দেখুন → শেষ error লাইন |
| `ModuleNotFoundError: a2wsgi` | ভেনভ অ্যাক্টিভেট করে `pip install -r requirements.txt`, তারপর RESTART |
| `ModuleNotFoundError: backend` | `passenger_wsgi.py` আর `backend/` একই ফোল্ডারে নেই |
| পেজ খোলে, সব খালি | `/api/health` চেক করুন; `hosted: true` না এলে env var / restart দেখুন |
| প্রথম রিকোয়েস্টে ১০–২০ সেকেন্ড দেরি | Passenger idle থাকলে অ্যাপ আনলোড করে — এটা স্বাভাবিক, ব্রাউজার খোলা থাকলে পোলিং তাকে জাগিয়ে রাখে |
| চার্টে "no candles" | ইন্টারনেট/CDN ব্লক, বা ওই সিম্বলে পাবলিক সোর্স নেই — অন্য সিম্বল ট্রাই করুন |
| `sqlite3.OperationalError: unable to open database` | `data/` ফোল্ডার নেই বা রাইট পারমিশন নেই → বানিয়ে `chmod 755 data` |

## আপডেট নেওয়া

নতুন ফাইল আপলোড → **RESTART**। কোনো বিল্ড স্টেপ নেই (Python + static JS)।

---

## ধাপ ৪ — গতি (cPanel-এর জন্য সোজা উপায়)

cPanel-এ dashboard স্লো হওয়ার আসল কারণ ছিল `/api/state` রিকোয়েস্ট: ব্রাউজার প্রতি ২.৫ সেকেন্ডে
১ MB (৪৬০টা নিউজ রো) টানত, অর্থাৎ সেকেন্ডে ~০.৪ MB ≈ ৩.৪ Mbps — shared হোস্টে এতেই সব জমে যায়।
এখন তিনটা লেভেলে কাটা হয়েছে:

| আগে | এখন |
|---|---|
| প্রতি poll 1,043,625 bytes | প্রতি poll **~7 KB** (gzip) — প্রায় ১৫০× কম |
| সব ফাইল uncompressed | gzip: `app.js` 390 KB → 97 KB, `index.html` 120 KB → 20 KB |
| প্রতি ভিউটে সব ফাইল নতুন করে | স্ট্যাটিক ফাইল ৫ মিনিট ক্যাশ (`?v=` ভার্সন বদলালেই নতুন ফাইল আসবে) |

**কীভাবে কাজ করে:** নিউজ ক্যালেন্ডার আলাদা পাঠানো হয় — ব্রাউজার `?have=<sig>` দিয়ে বলে "আমার কাছে
এই লিস্ট আছে", তখন সার্ভার সেই মেগাবাইট ফাইলটা বাদ দেয়। লিস্ট বদলালে (১৫ মিনিটে একবার) তবেই আসে।
ক্লায়েন্ট সাইডে তিন জায়গা ম্যানেজ করে: `web/app.js`, `web/chart-news-events.js`, `web/chart-overlay.js` —
তিনটাই "নিউজ আমার কাছে নেই" মানে আগে ফুল লিস্ট নেবে, তারপর শুধু `have` পাঠাবে।

**আপলোডের আগে লোকালে যাচাই করুন** (cPanel-এর হুবহু WSGI পাথ):

```bash
python serve_cpanel_local.py     # http://127.0.0.1:8799
```

uvicorn দিয়ে চালালে WebSocket চলে, তাই cPanel-এর সমস্যা দেখা যায় না — এই ফাইলটাই ঠিক দেখায়।
