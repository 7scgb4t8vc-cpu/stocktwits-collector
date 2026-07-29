# StockTwits Sentiment & Price Correlation Dashboard

**What this project does:** It watches StockTwits (a stock-focused social
media site) and tracks how often people are talking about certain stocks,
what they're saying (bullish/bearish), and compares that to the stock's
actual price movement — to help study whether retail social media chatter
predicts or follows price moves.

**Live app:** https://stocktwits-collector-production.up.railway.app

---

## The two main pieces

Think of this project as two separate programs that share the same database:

1. **The collectors** (root folder) — small Python scripts that grab data
   from StockTwits and FinViz (a stock data website) and save it to the
   database. These run automatically on a schedule using GitHub Actions.

2. **The dashboard** (`dashboard/` folder) — a website (built with Flask)
   that reads from the same database and shows charts, stock screeners,
   and social media feeds. This runs continuously on Railway (a hosting
   service), and *also* collects its own live data every 60 seconds in
   the background while it's running.

Both pieces talk to the same MongoDB database, so data collected by
either one shows up everywhere.

---

## Folder guide
stocktwits-collector/
├── .github/workflows/ → Tells GitHub when to run the collector scripts automatically
├── dashboard/ → The actual website code (deployed on Railway)
│ ├── app.py → The website's brain — all pages and API routes live here
│ ├── db.py → Handles all database reads/writes for the website
│ ├── static/ → CSS and JavaScript files for the website
│ └── templates/ → The actual HTML pages (one file per page)
├── stocktwits_collector.py → Grabs new StockTwits messages + prices, runs every 5 min
├── finviz_refresh.py → Refreshes the full list of ~10,000 stocks, every 30 min
├── nlp_processor.py → Reads new messages and labels them bullish/bearish/neutral
├── db.py → Same job as dashboard/db.py, but for these root scripts
└── README.md

**Why is there two `db.py` files?** Because the collector scripts and the
website are two separate programs that get deployed to two separate
places (GitHub Actions vs. Railway), each one needs its own copy of the
code that talks to the database.

---

## How to run it yourself

### Step 1 — Get a database
Sign up for a free MongoDB Atlas account and create a cluster. Copy the
connection string it gives you — you'll need it below. In MongoDB's
network settings, allow connections from anywhere (`0.0.0.0/0`), since
this will be running on a server, not just your own laptop.

### Step 2 — Get a FinViz Elite account
This project pulls stock data from FinViz Elite (a paid stock screener
site). You'll need an active subscription and its export token.

### Step 3 — Set your environment variable
The only thing every script needs to run is your database connection
string:
```bash
export MONGO_URI="your-mongodb-connection-string-here"
```

### Step 4 — Add your FinViz token
Rather than typing the FinViz token into a config file, this project
stores it in the database so it can be updated anytime without
redeploying anything. Once the website is running, just visit:
https://<wherever-you-deployed-it>/admin/token
and paste your token in.

### Step 5 — Run the website
```bash
cd dashboard
pip install -r requirements.txt
python app.py
```
Open your browser to `http://localhost:8080` and you should see the
dashboard running. It will start collecting live data automatically.

### Step 6 — Run the collector scripts (optional, for testing)
```bash
pip install curl-cffi requests pytz "pymongo[srv]" certifi
python stocktwits_collector.py
python finviz_refresh.py
```

The sentiment-labeling script needs a couple more packages, since it
downloads a small AI model the first time it runs:
```bash
pip install transformers torch pymongo
python nlp_processor.py
```

---

## For whoever's grading this

The easiest way to see this working is to just visit the live link above
— it's already running continuously and collecting data. If you want to
confirm the code itself runs independently on a different machine, Steps
3–5 above are the fastest way: it only takes a MongoDB connection string
and a couple of `pip install` commands.
