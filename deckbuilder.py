# deckbuilder.py
import streamlit as st
import requests
import pandas as pd
import time
import re
import unicodedata
from bs4 import BeautifulSoup

# ----------------------------
# Config / UI
# ----------------------------
st.set_page_config(page_title="MTG EDH Deckbuilder (EDHREC + Collection)", layout="wide")
st.title("🧙 MTG EDH Deckbuilder — EDHREC + Collection aware")

# Debug-Schalter in UI (standardmäßig aus)
DEBUG = st.checkbox("🔍 Debug-Ausgaben anzeigen", value=False)

st.markdown("**Hinweis:** Lade deine Collection als CSV (erste Spalte = Kartennamen) oder als TXT (eine Karte pro Zeile).")

uploaded = st.file_uploader("📂 Collection (.csv oder .txt)", type=["csv", "txt"])
commander_name = st.text_input("👑 Commander (voller Name empfohlen)", "")
keywords = st.text_input("🔑 Keywords (optional, komma-getrennt)", "")
avg_cmc = st.slider("⚖️ Ziel-Mana-Curve (höher = teurer)", 1.0, 7.0, 3.0, 0.1)
max_price = st.number_input("💰 Max Preis pro vorgeschlagener Karte (€) (0 = keine Preisfilterung)", min_value=0.0, value=5.0, step=0.5)
sort_after = st.selectbox("🔽 Sortierung nach dem Bauen", ["Keine", "Kartentyp", "Funktion"])

# ----------------------------
# Helpers: Normalisierung & Klassifikation
# ----------------------------
def _ascii_slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s

def _norm_name(s: str) -> str:
    """Normalize a card name for matching: lower, remove accents, strip counts and set-parens."""
    if not s:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r'^\d+\s+', '', s)               # leading counts like "2 "
    s = re.sub(r'\s*\(.*?\)\s*$', '', s)       # trailing "(set)" blocks
    s = re.sub(r'[\u2018\u2019`\'"]', '', s)    # fancy quotes
    s = re.sub(r'[^a-z0-9\s-]', ' ', s)        # remove punctuation
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def detect_function(card: dict) -> str:
    if not card:
        return "Other"
    t = (card.get("type_line") or "").lower()
    o = (card.get("oracle_text") or "").lower()
    if "land" in t: return "Land"
    if "creature" in t: return "Creature"
    if "add {m" in o or "search your library for a land" in o or "ramp" in o: return "Ramp"
    if "draw a card" in o or "scry" in o or "investigate" in o: return "Card Draw"
    if "destroy target" in o or "exile target" in o or "counter target" in o: return "Removal/Interaction"
    if "you win the game" in o or "extra turn" in o or "infinite" in o: return "Wincon/Finisher"
    if "enchant" in t: return "Enchantment"
    if "artifact" in t: return "Artifact"
    if "instant" in t: return "Instant"
    if "sorcery" in t: return "Sorcery"
    return "Other"

def get_price_eur(card: dict) -> float:
    p = (card or {}).get("prices") or {}
    eur = p.get("eur")
    usd = p.get("usd")
    try:
        if eur: return float(eur)
        if usd: return float(usd)
    except Exception:
        pass
    return 0.0

# ----------------------------
# Staples (häufig gewünschte Auto-Adds)
# ----------------------------
STAPLES = [
    "sol ring", "arcane signet", "f swiftfoot boots", "swiftfoot boots", "lightning greaves",
    "command tower", "fellwar stone", "prophetic prism", "arcane signet", "kodama's reach",
    "cultivate", "farseek", "explosive vegetation", "sol ring"  # duplicates ok, normalized later
]

# ----------------------------
# API calls with caching
# ----------------------------
@st.cache_data(show_spinner=False)
def get_card_info(name: str):
    if not name:
        return None
    url = f"https://api.scryfall.com/cards/named?fuzzy={requests.utils.quote(name)}"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "MTG-Deckbuilder/1.0"})
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None

@st.cache_data(show_spinner=False)
def get_edhrec_names_html(commander_name: str):
    """Scrape EDHREC commander page for many possible card name selectors."""
    if not commander_name:
        return []
    slug = _ascii_slug(commander_name)
    url = f"https://edhrec.com/commanders/{slug}"
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")

        candidates = []
        # collect from several selectors known on EDHREC pages
        selectors = ["a.card__name", "a.card", ".card-name", "a[href*='/card/']", ".card-list a", ".card-list li a"]
        for sel in selectors:
            for el in soup.select(sel):
                txt = el.get_text(strip=True)
                if txt:
                    candidates.append(txt)

        # try images alt
        for img in soup.find_all("img", alt=True):
            alt = img.get("alt", "").strip()
            if alt and len(alt) < 80:
                candidates.append(alt)

        # data attributes
        for el in soup.find_all(attrs={"data-card-name": True}):
            candidates.append(el["data-card-name"])

        # also scan textual blocks for lines that look like "CardName X% of N decks"
        texts = soup.get_text(separator="\n").splitlines()
        for line in texts:
            line = line.strip()
            if len(line) > 4 and re.search(r"\d+% of \d+ decks", line):
                # extract potential card name at start
                m = re.match(r"^([A-Za-z0-9'‘’\-\.\s:]+?)\s+\d+% of \d+ decks", line)
                if m:
                    candidates.append(m.group(1).strip())

        # normalize & dedupe preserving order
        seen = set(); out = []
        for t in candidates:
            n = t.strip()
            key = _norm_name(n)
            if key and key not in seen:
                seen.add(key)
                out.append(n)
        return out
    except Exception:
        return []

# ----------------------------
# Build action
# ----------------------------
if st.button("🚀 Deck bauen"):
    # validate
    if not commander_name:
        st.error("Bitte gib einen Commander ein.")
        st.stop()
    if not uploaded:
        st.error("Bitte lade deine Collection hoch (.csv oder .txt).")
        st.stop()

    # load commander
    with st.spinner("Lade Commander-Daten von Scryfall …"):
        commander = get_card_info(commander_name)
    if not commander:
        st.error("Commander nicht gefunden (Scryfall). Versuche den vollständigen Namen.")
        st.stop()

    # read collection
    try:
        if uploaded.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded, dtype=str, keep_default_na=False)
            first_col = df.columns[0]
            raw_names = df[first_col].dropna().astype(str).tolist()
        else:
            text = uploaded.read().decode("utf-8", errors="ignore")
            raw_names = [ln.strip() for ln in text.splitlines() if ln.strip()]
    except Exception as e:
        st.error(f"Collection konnte nicht gelesen werden: {e}")
        st.stop()

    if DEBUG: st.write(f"🔎 Geladene Einträge: {len(raw_names)} (erste 12): {raw_names[:12]}")

    # get scryfall info for collection entries (cached + gentle pacing)
    pool = []
    not_found = []
    progress = st.progress(0)
    for i, raw in enumerate(raw_names):
        if not raw: continue
        # try as-is then normalized
        info = get_card_info(raw)
        if not info:
            info = get_card_info(_norm_name(raw))
        if info:
            pool.append(info)
        else:
            not_found.append(raw)
        progress.progress((i+1)/max(1, len(raw_names)))
        time.sleep(0.03)
    if DEBUG:
        st.write(f"✅ Scryfall-Infos gefunden: {len(pool)}; nicht gefunden: {len(not_found)} (erste 8): {not_found[:8]}")

    # EDHREC names (scrape)
    edhrec_names = get_edhrec_names_html(commander.get("name"))
    if DEBUG:
        st.write(f"🧾 EDHREC lieferte {len(edhrec_names)} Namen (erste 20): {edhrec_names[:20]}")

    # Build normalized index of collection: norm_name -> card obj
    scry_idx = {_norm_name(c.get("name","")): c for c in pool}

    commander_identity = set(commander.get("color_identity") or [])

    # Owned EDHREC hits (legal)
    owned_edhrec = []
    for en in edhrec_names:
        key = _norm_name(en)
        c = scry_idx.get(key)
        if c and set(c.get("color_identity") or []).issubset(commander_identity):
            owned_edhrec.append(c)

    # add staples if owned & legal
    staple_hits = []
    for s in STAPLES:
        key = _norm_name(s)
        c = scry_idx.get(key)
        if c and set(c.get("color_identity") or []).issubset(commander_identity) and c not in staple_hits:
            staple_hits.append(c)

    # fillers from collection (legal, not already included)
    fillers = [c for k,c in scry_idx.items() if c not in owned_edhrec and c not in staple_hits and set(c.get("color_identity") or []).issubset(commander_identity)]
    fillers.sort(key=lambda x: (x.get("edhrec_rank") or 999999, x.get("cmc") or 99))

    # apply avg_cmc bias by rotating
    bias = max(0.0, min((avg_cmc - 1)/6.0, 1.0))
    if fillers:
        shift = int(len(fillers) * bias * 0.45)
        fillers = fillers[shift:] + fillers[:shift]

    # Combine deck: commander, staples, owned edhrec, fillers until 100
    deck = [commander] + staple_hits
    for c in owned_edhrec:
        if c not in deck:
            deck.append(c)
    for c in fillers:
        if len(deck) >= 100: break
        if c not in deck:
            deck.append(c)
    deck = deck[:100]

    st.success(f"✅ Deck gebaut: {len(deck)} Karten (EDHREC-Treffer in Collection: {len(owned_edhrec)}).")

    # Build display dataframe
    rows = []
    for c in deck:
        rows.append({
            "Name": c.get("name"),
            "Mana Value": c.get("cmc"),
            "Type": c.get("type_line"),
            "Function": detect_function(c),
            "Color Identity": "".join(c.get("color_identity") or [])
        })
    df_deck = pd.DataFrame(rows)

    if sort_after == "Kartentyp":
        df_deck = df_deck.sort_values(["Type","Mana Value","Name"], kind="stable")
    elif sort_after == "Funktion":
        df_deck = df_deck.sort_values(["Function","Mana Value","Name"], kind="stable")

    st.dataframe(df_deck, use_container_width=True)

    # Suggestions: EDHREC names not in collection -> price-filtered & legality
    missing_names = [n for n in edhrec_names if _norm_name(n) not in scry_idx]
    if DEBUG:
        st.write(f"🔍 EDHREC-Karten, die du nicht besitzt: {len(missing_names)} (erste 20): {missing_names[:20]}")

    suggested = []
    for nm in missing_names[:150]:
        info = get_card_info(nm)
        if not info: continue
        if not set(info.get("color_identity") or []).issubset(commander_identity): continue
        price = get_price_eur(info)
        if max_price <= 0 or price <= max_price:
            suggested.append({
                "Name": info.get("name"),
                "Price (EUR/USD)": price,
                "Type": info.get("type_line"),
                "Mana Value": info.get("cmc"),
                "Function": detect_function(info)
            })
        time.sleep(0.03)
    if suggested:
        df_sugg = pd.DataFrame(suggested).sort_values(["Price (EUR/USD)","Mana Value","Name"], kind="stable")
        st.subheader("💡 Vorschläge (EDHREC-Karten, die du nicht besitzt, Preisfilter angewendet)")
        st.dataframe(df_sugg, use_container_width=True)
    else:
        st.info("Keine Vorschläge innerhalb des Preisrahmens gefunden oder EDHREC lieferte keine Kartenvorschläge.")

    # Export
    st.download_button("📤 Deck als CSV exportieren", data=df_deck.to_csv(index=False), file_name="deck.csv", mime="text/csv")

