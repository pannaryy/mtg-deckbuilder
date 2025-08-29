# deckbuilder.py
import streamlit as st
import requests
import pandas as pd
import time
import re
import unicodedata
from bs4 import BeautifulSoup

# ----------------------------
# Seite konfigurieren
# ----------------------------
st.set_page_config(page_title="MTG EDH Deckbuilder (EDHREC+Collection)", layout="wide")
st.title("🧙 MTG EDH Deckbuilder — EDHREC + Collection aware")

# ----------------------------
# Hilfsfunktionen
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
    if not s:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    # remove leading count like "2 " and strip set info "(...)" and punctuation
    s = re.sub(r'^\d+\s+', '', s)
    s = re.sub(r'\s*\(.*?\)\s*$', '', s)
    s = re.sub(r'[^a-z0-9\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# Cached Scryfall lookup (fuzzy)
@st.cache_data(show_spinner=False)
def get_card_info(name: str):
    if not name:
        return None
    url = f"https://api.scryfall.com/cards/named?fuzzy={requests.utils.quote(name)}"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None

# Robust EDHREC scraping (HTML fallback + heuristics)
@st.cache_data(show_spinner=False)
def get_edhrec_card_names(commander_name: str):
    """Return list of EDHREC card name strings (attempt HTML scraping of common selectors)."""
    if not commander_name:
        return []
    slug = _ascii_slug(commander_name)
    url_html = f"https://edhrec.com/commanders/{slug}"
    names = []
    try:
        r = requests.get(url_html, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")

        # Strategy: collect card names from multiple likely selectors and de-duplicate
        candidate_texts = []
        # common EDHREC blocks: signature cards, popular cards, etc.
        for sel in ["a.card__name", ".card-name", ".card", "a[href*='/cards/']"]:
            for a in soup.select(sel):
                txt = a.get_text(strip=True)
                if txt:
                    candidate_texts.append(txt)

        # also try images alt tags (some lists use <img alt="Card Name">)
        for img in soup.find_all("img", alt=True):
            alt = img.get("alt", "").strip()
            if alt and len(alt) < 60:
                candidate_texts.append(alt)

        # find data-card-name attributes if present
        for el in soup.find_all(attrs={"data-card-name": True}):
            candidate_texts.append(el["data-card-name"])

        # fallback: find text in lists (very heuristic)
        for ul in soup.select("ul.card-list, ul.cards"):
            for li in ul.select("li"):
                txt = li.get_text(strip=True)
                if txt:
                    candidate_texts.append(txt)

        # normalize & dedupe preserving order
        seen = set()
        out = []
        for t in candidate_texts:
            n = t.strip()
            key = _norm_name(n)
            if key and key not in seen:
                seen.add(key)
                out.append(n)
        return out
    except Exception:
        return []

def is_commander_legal(card: dict, commander_identity: set) -> bool:
    cid = set(card.get("color_identity") or [])
    return cid.issubset(commander_identity)

def detect_function(card: dict) -> str:
    if not card:
        return "Other"
    t = (card.get("type_line") or "").lower()
    o = (card.get("oracle_text") or "").lower()
    if "land" in t: return "Land"
    if "creature" in t: return "Creature"
    if "add {m" in o or "search your library for a land" in o or "ramp" in o: return "Ramp"
    if "draw a card" in o or "scry" in o or "investigate" in o: return "Card Draw"
    if "destroy target" in o or "exile target" in o or "counter target" in o or "fight target" in o: return "Removal/Interaction"
    if "you win the game" in o or "extra turn" in o or "infinite" in o: return "Wincon/Finisher"
    if "artifact" in t: return "Artifact"
    if "enchantment" in t: return "Enchantment"
    if "instant" in t: return "Instant"
    if "sorcery" in t: return "Sorcery"
    return "Other"

def get_price_eur(card: dict) -> float:
    if not card:
        return 0.0
    p = card.get("prices") or {}
    eur = p.get("eur")
    usd = p.get("usd")
    try:
        if eur: return float(eur)
        if usd: return float(usd)
    except Exception:
        pass
    return 0.0

# staples that should often be included if owned
STAPLES = [
    "Sol Ring", "Arcane Signet", "Swiftfoot Boots", "Lightning Greaves",
    "Command Tower", "Prophetic Prism", "Fellwar Stone", "Cultivate", "Kodama's Reach"
]

# ----------------------------
# UI Controls
# ----------------------------
st.markdown("**Achtung:** Lade deine Collection als CSV (erste Spalte Kartennamen) oder TXT (eine Karte pro Zeile).")
uploaded = st.file_uploader("📂 Collection (.csv oder .txt)", type=["csv","txt"])
commander_name = st.text_input("👑 Commander (voller Name empfohlen)", "")
keywords = st.text_input("🔑 Keywords (optional, komma-getrennt)", "")
avg_cmc = st.slider("⚖️ Ziel-Mana-Curve (höher = teurer)", 1.0, 7.0, 3.0, 0.1)
max_price = st.number_input("💰 Max Preis pro vorgeschlagener Karte (€)", min_value=0.0, value=5.0, step=0.5)
sort_after = st.selectbox("🔽 Sortierung nach dem Bauen", ["Keine", "Kartentyp", "Funktion"])

# ----------------------------
# Build action
# ----------------------------
if st.button("🚀 Deck bauen"):
    # validate inputs
    if not commander_name:
        st.error("Bitte gib einen Commander ein.")
        st.stop()
    if not uploaded:
        st.error("Bitte lade deine Collection hoch.")
        st.stop()

    # fetch commander from Scryfall
    with st.spinner("Lade Commander-Daten von Scryfall…"):
        commander = get_card_info(commander_name)
    if not commander:
        st.error("Commander nicht gefunden (Scryfall). Probiere den vollen Namen.")
        st.stop()

    # read collection card names robustly
    try:
        if uploaded.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded, dtype=str, keep_default_na=False)
            first_col = df.columns[0]
            raw_names = df[first_col].dropna().astype(str).tolist()
        else:
            raw_text = uploaded.read().decode("utf-8", errors="ignore")
            raw_names = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    except Exception as e:
        st.error(f"Collection konnte nicht gelesen werden: {e}")
        st.stop()

    st.write(f"🔎 Geladene Einträge: {len(raw_names)} (erste 12): {raw_names[:12]}")

    # convert raw names -> scryfall info (cached)
    pool = []
    not_found = []
    progress = st.progress(0)
    for i, raw in enumerate(raw_names):
        nm_try = raw.strip()
        info = get_card_info(nm_try)
        if not info:
            # try normalized
            nm_norm = _norm_name(nm_try)
            info = get_card_info(nm_norm)
        if info:
            pool.append(info)
        else:
            not_found.append(raw)
        progress.progress((i+1)/max(1, len(raw_names)))
        time.sleep(0.04)  # gentle pacing

    st.write(f"✅ Scryfall-Infos gefunden: {len(pool)}")
    if not_found:
        st.warning(f"⚠️ Nicht gefunden: {len(not_found)} Einträge (erste 8): {not_found[:8]}")

    st.write("🔎 Hole EDHREC-Empfehlungen (Scraper + Heuristiken)…")
    edhrec_names = get_edhrec_card_names(commander.get("name"))
    st.write(f"🧾 EDHREC lieferte {len(edhrec_names)} Namen (erste 20): {edhrec_names[:20]}")

    # Build index for collection by normalized name
    scry_idx = {_norm_name(c.get("name","")): c for c in pool}

    # Commander color identity check
    commander_identity = set(commander.get("color_identity") or [])

    # Collect owned EDHREC hits (legal)
    owned_edhrec = []
    for en in edhrec_names:
        key = _norm_name(en)
        c = scry_idx.get(key)
        if c and is_commander_legal(c, commander_identity):
            owned_edhrec.append(c)

    st.write(f"🔎 EDHREC-Treffer in deiner Collection: {len(owned_edhrec)} (erste 12): {[c.get('name') for c in owned_edhrec[:12]]}")

    # Add staples from STAPLES if owned & legal (ensure they appear)
    staple_hits = []
    for s in STAPLES:
        key = _norm_name(s)
        c = scry_idx.get(key)
        if c and is_commander_legal(c, commander_identity) and c not in staple_hits:
            staple_hits.append(c)

    if staple_hits:
        st.write(f"🔩 Staple-Treffer (automatisch hinzugefügt): {[c.get('name') for c in staple_hits]}")

    # Fillers from collection sorted by edhrec_rank then cmc (prefer better cards)
    fillers = [c for k,c in scry_idx.items() if c not in owned_edhrec and is_commander_legal(c, commander_identity)]
    fillers.sort(key=lambda x: (x.get("edhrec_rank") or 999999, x.get("cmc") or 99))

    # apply avg_cmc bias to fillers (rotate)
    bias = max(0.0, min((avg_cmc-1)/6.0, 1.0))
    if fillers:
        shift = int(len(fillers) * bias * 0.45)
        fillers = fillers[shift:] + fillers[:shift]

    # Build deck order: commander, staples, owned_edhrec, fillers
    deck = [commander] + staple_hits
    for c in owned_edhrec:
        if c not in deck:
            deck.append(c)
    for c in fillers:
        if len(deck) >= 100:
            break
        if c not in deck:
            deck.append(c)
    deck = deck[:100]

    st.success(f"✅ Deck gebaut: {len(deck)} Karten (EDHREC-Treffer: {len(owned_edhrec)}).")

    # Build DataFrame for display
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

    # Suggestions: EDHREC names you don't own (apply price filter and legality)
    missing_names = [n for n in edhrec_names if _norm_name(n) not in scry_idx]
    st.write(f"🔍 EDHREC-Karten, die dir fehlen: {len(missing_names)} (erste 20): {missing_names[:20]}")

    suggested = []
    for nm in missing_names[:150]:  # cap
        info = get_card_info(nm)
        if not info:
            continue
        if not is_commander_legal(info, commander_identity):
            continue
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
