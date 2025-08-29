# deckbuilder.py (ersetzt vorherige Version)
import streamlit as st
import requests
import pandas as pd
import time
import re
import unicodedata
from bs4 import BeautifulSoup

st.set_page_config(page_title="MTG EDH Deckbuilder (EDHREC + Collection)", layout="wide")
st.title("🧙 MTG EDH Deckbuilder — EDHREC + Collection aware (Debug Mode)")

# ------------------------------
# Normalisierung / Helpers
# ------------------------------
def _ascii_slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s

def _norm_name(s: str) -> str:
    if not s: return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    # Remove leading counts like "2 " and trailing set info in parentheses: " (M21)"
    s = re.sub(r'^\d+\s+', '', s)
    s = re.sub(r'\s*\(.*?\)\s*$', '', s)
    s = re.sub(r'[\u2018\u2019`\'"]', '', s)  # quotes
    s = re.sub(r'[^a-z0-9\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

@st.cache_data(show_spinner=False)
def get_card_info(name: str):
    """Cached Scryfall named fuzzy lookup."""
    if not name:
        return None
    url = f"https://api.scryfall.com/cards/named?fuzzy={requests.utils.quote(name)}"
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None

@st.cache_data(show_spinner=False)
def get_edhrec_card_names_for_commander(commander_name: str):
    """First try EDHREC JSON endpoint, fallback to HTML heuristics."""
    if not commander_name:
        return []
    slug = _ascii_slug(commander_name)
    url_json = f"https://json.edhrec.com/pages/commanders/{slug}.json"
    try:
        r = requests.get(url_json, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            names = []
            if isinstance(data, dict):
                # Many pages expose 'cardlists' with 'cards' arrays
                for section in data.get("cardlists", []):
                    for c in section.get("cards", []):
                        nm = c.get("name")
                        if nm:
                            names.append(nm)
            # dedupe preserve order
            seen = set(); out=[]
            for n in names:
                kn = _norm_name(n)
                if kn and kn not in seen:
                    seen.add(kn); out.append(n)
            if out:
                return out
    except Exception:
        # ignore and fallback
        pass

    # Fallback: try scraping HTML (heuristic)
    url_html = f"https://edhrec.com/commanders/{slug}"
    try:
        r2 = requests.get(url_html, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if r2.status_code != 200:
            return []
        soup = BeautifulSoup(r2.text, "lxml")
        names_set = []
        # Try several selector heuristics
        # 1) anchor tags with class containing 'card' or 'card__name'
        for a in soup.select("a.card__name, a.card, a[href*='/card/'], a[href*='/cards/']"):
            txt = a.get_text(strip=True)
            if txt:
                names_set.append(txt)
        # 2) data attributes e.g. data-card-name
        for el in soup.find_all(attrs={"data-card-name": True}):
            txt = el.get("data-card-name")
            if txt:
                names_set.append(txt)
        # dedupe and return
        seen=set(); out=[]
        for n in names_set:
            kn=_norm_name(n)
            if kn and kn not in seen:
                seen.add(kn); out.append(n)
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
    if "add {m" in o or "search your library for a land" in o: return "Ramp"
    if "draw a card" in o or "scry" in o: return "Card Draw"
    if "destroy target" in o or "exile target" in o or "counter target" in o: return "Removal/Interaction"
    if "you win the game" in o or "extra turn" in o or "infinite" in o: return "Wincon"
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

# ------------------------------
# UI Controls
# ------------------------------
st.markdown("**Hinweis:** Es werden EDHREC-Empfehlungen mit deiner Collection abgeglichen. "
            "Falls du weiterhin 0 Treffer siehst, achte bitte darauf, dass deine CSV in der ersten Spalte die reinen Kartennamen enthält (ohne Mengen/Set) — der Debug-Output unten hilft weiter.")

uploaded = st.file_uploader("📂 Deine Moxfield Collection (.csv empfohlen; erste Spalte = Kartennamen) — .txt geht auch", type=["csv","txt"])
commander_name = st.text_input("👑 Commander (voller Name empfohlen)", "")
max_price = st.number_input("💰 Max Preis pro vorgeschlagener Karte (€)", min_value=0.0, value=5.0, step=0.5)
avg_cmc = st.slider("⚖️ Ziel-Mana-Curve (höher = teurer)", 1.0, 7.0, 3.2, 0.1)
sort_after = st.selectbox("🔽 Sortierung nach dem Bauen", ["Keine", "Kartentyp", "Funktion"])

# ------------------------------
# Build process
# ------------------------------
if st.button("🚀 Deck bauen"):
    # basic checks
    if not commander_name:
        st.error("Bitte gib einen Commander ein.")
        st.stop()
    if not uploaded:
        st.error("Bitte lade deine Collection hoch (.csv oder .txt).")
        st.stop()

    # load commander
    with st.spinner("Lade Commander von Scryfall …"):
        commander = get_card_info(commander_name)
    if not commander:
        st.error("Commander nicht gefunden (Scryfall). Bitte vollständigen Namen verwenden.")
        st.stop()

    # read collection names (robust)
    try:
        if uploaded.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded, dtype=str, keep_default_na=False)
            # find first non-empty column as name column
            first_col = df.columns[0]
            raw_names = df[first_col].dropna().astype(str).tolist()
        else:
            # txt: lines
            raw_lines = uploaded.read().decode('utf-8', errors='ignore').splitlines()
            raw_names = [ln.strip() for ln in raw_lines if ln.strip()]
    except Exception as e:
        st.error(f"Collection konnte nicht gelesen werden: {e}")
        st.stop()

    st.write(f"🔎 Geladene Einträge in Collection: {len(raw_names)} (erste 10): {raw_names[:10]}")

    # normalize raw names and attempt Scryfall lookup (cached)
    pool = []
    not_found_raw = []
    progress = st.progress(0)
    for i, raw in enumerate(raw_names):
        # normalize raw (strip counts, sets)
        nm = _norm_name(raw)
        if not nm:
            continue
        info = get_card_info(raw)  # try with original raw line (Scryfall fuzzy)
        if not info:
            # try with normalized name
            info = get_card_info(nm)
        if info:
            pool.append(info)
        else:
            not_found_raw.append(raw)
        progress.progress((i+1)/max(1, len(raw_names)))
        time.sleep(0.04)

    st.write(f"✅ Scryfall-Infos gefunden für {len(pool)} Karten.")
    if not_found_raw:
        st.warning(f"⚠️ Für {len(not_found_raw)} Collection-Einträge kein Scryfall-Match: (erste 8): {not_found_raw[:8]}")

    # Ask EDHREC for recommended card names
    st.write("🔎 Frage EDHREC nach häufigen Karten für diesen Commander …")
    edhrec_names = get_edhrec_card_names_for_commander(commander.get("name"))
    st.write(f"🧾 EDHREC lieferte {len(edhrec_names)} Kartennamen (erste 20): {edhrec_names[:20]}")

    # Build indices for matching: normalized scryfall names -> card info
    scry_idx = {_norm_name(c.get("name","")): c for c in pool}

    # Owned EDHREC cards that are legal for the commander
    commander_identity = set(commander.get("color_identity") or [])
    owned_edhrec_cards = []
    for ename in edhrec_names:
        key = _norm_name(ename)
        c = scry_idx.get(key)
        if c and is_commander_legal(c, commander_identity):
            owned_edhrec_cards.append(c)

    st.write(f"🔎 Gefundene EDHREC-Treffer in deiner Collection: {len(owned_edhrec_cards)} (erste 12): {[c.get('name') for c in owned_edhrec_cards[:12]]}")

    # Fill rest from collection (legal cards), prefer lower edhrec_rank and low cmc
    remaining = [c for k,c in scry_idx.items() if c not in owned_edhrec_cards and is_commander_legal(c, commander_identity)]
    remaining.sort(key=lambda x: (x.get("edhrec_rank") or 999999, x.get("cmc") or 99))
    # apply a gentle avg_cmc bias: rotate list
    bias = max(0.0, min((avg_cmc-1)/6.0, 1.0))
    if remaining:
        shift = int(len(remaining) * bias * 0.45)
        remaining = remaining[shift:] + remaining[:shift]

    deck = [commander] + owned_edhrec_cards
    for c in remaining:
        if len(deck) >= 100: break
        deck.append(c)

    st.success(f"✅ Deck gebaut: {len(deck)} Karten (EDHREC-Treffer aus Collection: {len(owned_edhrec_cards)})")

    # Show deck table
    rows=[]
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

    # Suggestions: EDHREC names you don't own
    missing_names = [n for n in edhrec_names if _norm_name(n) not in scry_idx]
    st.write(f"🔍 EDHREC-Karten, die dir fehlen: {len(missing_names)} (erste 20): {missing_names[:20]}")

    suggested_rows=[]
    # limit to first 150 missing names for requests
    for nm in missing_names[:150]:
        info = get_card_info(nm)
        if not info: 
            continue
        if not is_commander_legal(info, commander_identity):
            continue
        price = get_price_eur(info)
        if max_price <= 0 or price <= max_price:
            suggested_rows.append({
                "Name": info.get("name"),
                "Price (EUR/USD)": price,
                "Type": info.get("type_line"),
                "Mana Value": info.get("cmc"),
                "Function": detect_function(info)
            })
        time.sleep(0.03)
    if suggested_rows:
        df_sugg = pd.DataFrame(suggested_rows)
        st.subheader("💡 Vorschläge (EDHREC-Karten, die du nicht besitzt, Preisfilter angewendet)")
        st.dataframe(df_sugg.sort_values(["Price (EUR/USD)","Mana Value","Name"], kind="stable"), use_container_width=True)
    else:
        st.info("Keine Vorschläge innerhalb des Preisrahmens gefunden oder EDHREC lieferte keine Kartenvorschläge.")

    # Export deck
    st.download_button("📤 Deck als CSV exportieren", data=df_deck.to_csv(index=False), file_name="deck.csv", mime="text/csv")
