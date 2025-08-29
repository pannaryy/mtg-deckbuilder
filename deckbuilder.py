import streamlit as st
import requests
import pandas as pd
import time
import re
import unicodedata

st.set_page_config(page_title="MTG EDH Deckbuilder (EDHREC + Collection)", layout="wide")
st.title("🧙 MTG EDH Deckbuilder — EDHREC + Collection aware")

# ------------------------------
# Helpers
# ------------------------------
def _ascii_slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^\w\s-]", "", s)           # drop punctuation
    s = re.sub(r"\s+", "-", s).strip("-")    # spaces -> hyphens
    s = re.sub(r"-{2,}", "-", s)             # collapse ---
    return s

def _norm_name(s: str) -> str:
    """Normalize a card name for reliable matching."""
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s

@st.cache_data(show_spinner=False)
def get_card_info(name: str):
    """Fetch a card from Scryfall by fuzzy name (cached)."""
    url = f"https://api.scryfall.com/cards/named?fuzzy={requests.utils.quote(name)}"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return r.json()
    except requests.exceptions.RequestException:
        return None
    return None

@st.cache_data(show_spinner=False)
def get_edhrec_card_names_for_commander(commander_name: str):
    """
    Pull the EDHREC commander's JSON page (no scraping; official json host) and return a list of card names.
    Example: https://json.edhrec.com/pages/commanders/atraxa-praetors-voice.json
    """
    slug = _ascii_slug(commander_name)
    url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []

    names = []
    # Typical layout: data["cardlists"] -> list of sections, each with ["cards"] list, each card has ["name"]
    if isinstance(data, dict) and "cardlists" in data:
        for section in data.get("cardlists", []):
            for c in section.get("cards", []):
                nm = c.get("name")
                if nm:
                    names.append(nm)

    # De-duplicate, keep order
    seen = set()
    out = []
    for n in names:
        key = _norm_name(n)
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out

def is_commander_legal(card: dict, commander_identity: set) -> bool:
    """Commander color identity legality check (uses color_identity, not colors)."""
    cid = set(card.get("color_identity", [])) if card else set()
    return cid.issubset(commander_identity)

def detect_function(card: dict) -> str:
    """Very light heuristic to label a card's role (for sorting)."""
    if not card:
        return "Other"
    type_line = (card.get("type_line") or "").lower()
    text = (card.get("oracle_text") or "").lower()
    if "land" in type_line:
        return "Land"
    if "creature" in type_line:
        # simple tribe indicator keeps it 'Creature' still
        return "Creature"
    if "add {m" in text or "search your library for a land" in text or "rampage" in text:
        return "Ramp"
    if "draw a card" in text or "scry" in text or "investigate" in text:
        return "Card Draw"
    if "destroy target" in text or "exile target" in text or "counter target" in text or "fight target" in text:
        return "Removal/Interaction"
    if "you win the game" in text or "extra turn" in text or "infinite" in text:
        return "Wincon/Finisher"
    if "artifact" in type_line:
        return "Artifact"
    if "enchantment" in type_line:
        return "Enchantment"
    if "instant" in type_line:
        return "Instant"
    if "sorcery" in type_line:
        return "Sorcery"
    return "Other"

# ------------------------------
# UI Controls
# ------------------------------
uploaded = st.file_uploader("📂 Deine Moxfield Collection (.csv empfohlen; erste Spalte = Kartennamen) — .txt geht auch", type=["csv", "txt"])
commander_name = st.text_input("👑 Commander", "", placeholder="z. B. Hakbal of the Surging Soul")
keywords = st.text_input("🔑 Zusätzliche Keywords (optional, komma-getrennt)", "")
avg_cmc = st.slider("⚖️ Ziel-Mana-Curve (höher = teurer)", 1.0, 7.0, 3.2, 0.1)
max_price = st.number_input("💰 Maximaler Preis pro empfohlener Karte (€)", min_value=0.0, value=5.0, step=0.5)
sort_after = st.selectbox("🔽 Sortierung nach dem Bauen", ["Keine", "Kartentyp", "Funktion"])

# ------------------------------
# Build logic
# ------------------------------
def build_deck_from_collection_and_edhrec(commander_info, collection_cards, keywords, avg_cmc):
    """Returns (deck_cards:list[dict], owned_hits:int, edhrec_list:list[str], missing_edhrec_names:list[str])"""
    commander_identity = set(commander_info.get("color_identity", []))
    edhrec_names = get_edhrec_card_names_for_commander(commander_info["name"])

    # Index collection by normalized name
    idx = {_norm_name(c.get("name", "")): c for c in collection_cards if c}

    # Owned EDHREC cards (legal only)
    owned_edhrec_cards = []
    for nm in edhrec_names:
        key = _norm_name(nm)
        c = idx.get(key)
        if c and is_commander_legal(c, commander_identity):
            owned_edhrec_cards.append(c)

    # Fillers from collection if needed (sorted by edhrec_rank then cmc)
    fillers = [c for key, c in idx.items() if c not in owned_edhrec_cards and is_commander_legal(c, commander_identity)]
    fillers.sort(key=lambda x: (x.get("edhrec_rank", 999999) or 999999, x.get("cmc", 99)))

    # crude curve control: bias start index by avg_cmc / 7.0
    fillers.sort(key=lambda x: x.get("cmc", 0) or 0)
    bias = min(max(avg_cmc / 7.0, 0.0), 1.0)
    start_i = int(len(fillers) * bias * 0.6)  # mild shift
    fillers = fillers[start_i:] + fillers[:start_i]

    deck = [commander_info]
    # Take up to 99 slots
    for c in owned_edhrec_cards:
        if len(deck) >= 100:
            break
        if c not in deck:
            deck.append(c)
    for c in fillers:
        if len(deck) >= 100:
            break
        if c not in deck:
            deck.append(c)

    # Missing EDHREC names (not owned)
    missing_names = [nm for nm in edhrec_names if _norm_name(nm) not in idx]

    return deck[:100], len(owned_edhrec_cards), edhrec_names, missing_names

def get_price_eur(card: dict) -> float:
    prices = (card or {}).get("prices") or {}
    eur = prices.get("eur")
    usd = prices.get("usd")
    try:
        if eur is not None:
            return float(eur)
        if usd is not None:
            return float(usd)
    except Exception:
        pass
    return 0.0

if st.button("🚀 Deck bauen"):
    if not commander_name:
        st.error("Bitte gib einen Commander ein.")
        st.stop()
    if not uploaded:
        st.error("Bitte lade deine Collection hoch (.csv oder .txt).")
        st.stop()

    with st.spinner("Lade Commander von Scryfall …"):
        commander = get_card_info(commander_name)
    if not commander:
        st.error("Commander nicht gefunden (Scryfall).")
        st.stop()

    # Read collection names
    try:
        if uploaded.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded)
            # Use first column as names
            card_names = df.iloc[:, 0].dropna().astype(str).tolist()
        else:
            # .txt lines
            card_names = [line.decode("utf-8", errors="ignore").strip() for line in uploaded if line.strip()]
    except Exception as e:
        st.error(f"Collection konnte nicht gelesen werden: {e}")
        st.stop()

    # Fetch Scryfall info for collection cards (cached + gentle pacing)
    pool = []
    progress = st.progress(0)
    for i, nm in enumerate(card_names):
        info = get_card_info(nm)
        if info:
            pool.append(info)
        progress.progress((i + 1) / max(1, len(card_names)))
        time.sleep(0.05)  # gentle on Streamlit Cloud

    # Build deck using EDHREC + collection
    deck, owned_hits, edhrec_list, missing_edhrec_names = build_deck_from_collection_and_edhrec(
        commander, pool, keywords, avg_cmc
    )

    # If EDHREC returned nothing, inform user
    if not edhrec_list:
        st.warning("⚠️ Von EDHREC kamen keine Daten zurück (oder der Commander-Slug wurde nicht gefunden). Ich habe dein Deck nur aus deiner Collection gefüllt.")

    # Show deck
    st.success(f"✅ Deck gebaut! Commander: {commander.get('name')} — {len(deck)} Karten (davon {owned_hits} EDHREC-Treffer aus deiner Collection)")

    # Prepare DataFrame for view & export
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
        df_deck = df_deck.sort_values(["Type", "Mana Value", "Name"], kind="stable")
    elif sort_after == "Funktion":
        df_deck = df_deck.sort_values(["Function", "Mana Value", "Name"], kind="stable")

    st.dataframe(df_deck, use_container_width=True)

    # Suggestions (EDHREC cards you don't own), price filtered and color-legal
    st.subheader("💡 Vorschläge (EDHREC, nicht in deiner Collection, Preis-Filter)")
    commander_identity = set(commander.get("color_identity", []))
    suggested_rows = []
    # limit suggestions to first 120 names to keep requests reasonable
    for nm in missing_edhrec_names[:120]:
        cinfo = get_card_info(nm)
        if not cinfo:
            continue
        if not is_commander_legal(cinfo, commander_identity):
            continue
        price = get_price_eur(cinfo)
        if max_price <= 0 or price <= max_price:
            suggested_rows.append({
                "Name": cinfo.get("name"),
                "Price (EUR/USD)": price,
                "Type": cinfo.get("type_line"),
                "Mana Value": cinfo.get("cmc"),
                "Function": detect_function(cinfo)
            })
        time.sleep(0.03)  # gentle pacing
    if suggested_rows:
        df_sugg = pd.DataFrame(suggested_rows)
        st.dataframe(df_sugg.sort_values(["Price (EUR/USD)", "Mana Value", "Name"], kind="stable"), use_container_width=True)
    else:
        st.info("Keine Vorschläge im Preisrahmen gefunden (oder EDHREC hat für diesen Commander keine Daten geliefert).")

    # Export
    st.download_button("📤 Deck als CSV exportieren", data=df_deck.to_csv(index=False), file_name="deck.csv", mime="text/csv")
