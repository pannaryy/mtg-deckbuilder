import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
import time

# -------------------------------------------------------
# Hilfsfunktionen
# -------------------------------------------------------

@st.cache_data(show_spinner=False)
def get_card_info(name):
    """Holt Kartendaten von Scryfall."""
    url = f"https://api.scryfall.com/cards/named?fuzzy={name}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


@st.cache_data(show_spinner=False)
def get_edhrec_recs(commander_name):
    """Holt empfohlene Karten von EDHREC für den Commander."""
    commander_url = commander_name.lower().replace(",", "").replace("'", "").replace(" ", "-")
    url = f"https://edhrec.com/commanders/{commander_url}"
    st.write(f"🔎 Lade EDHREC-Daten von: {url}")

    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            st.warning("⚠️ EDHREC konnte nicht geladen werden.")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        cards = []

        for tag in soup.find_all("a", class_=re.compile("card__name")):
            name = tag.get_text(strip=True)
            if name and name not in cards:
                cards.append(name)

        return cards

    except Exception as e:
        st.error(f"Fehler beim Laden von EDHREC: {e}")
        return []


def build_deck(commander_info, pool, keywords, avg_cmc, bracket):
    """Baut ein Deck basierend auf Commander, Collection und EDHREC."""
    if not commander_info:
        return []

    commander_colors = commander_info.get("colors", [])

    # EDHREC Empfehlungen laden
    edhrec_cards = get_edhrec_recs(commander_info["name"])

    # Nur Karten, die auch in Collection vorhanden sind
    pool_names = [c.get("name") for c in pool]
    deck_candidates = [c for c in pool if c.get("name") in edhrec_cards]

    # Commander ins Deck
    deck = [commander_info] + deck_candidates

    # Falls zu wenige Karten: Rest aus Collection nehmen
    if len(deck) < 100:
        extra = [
            c for c in pool
            if all(col in commander_colors for col in c.get("colors", []))
        ]
        for c in extra:
            if c not in deck:
                deck.append(c)
            if len(deck) >= 100:
                break

    return deck[:100]


# -------------------------------------------------------
# Streamlit UI
# -------------------------------------------------------

st.set_page_config(page_title="MTG Deckbuilder", layout="wide")

st.title("🧙 Magic: The Gathering - EDH Deckbuilder")

# Commander-Eingabe
commander_name = st.text_input("Commander Name", "")

# CSV Upload
uploaded_csv = st.file_uploader("📂 Deine Moxfield Collection (CSV)", type=["csv"])

# Einstellungen
avg_cmc = st.slider("⚖️ Ziel-Mana-Curve", 1.0, 6.0, 3.0, 0.1)
bracket = st.selectbox("🏆 Power Level Bracket", ["Casual", "Mid", "High", "cEDH"])
keywords = st.text_input("🔑 Keywords (Komma-getrennt, optional)", "")

# Button
if st.button("📥 Deck bauen"):
    if not commander_name:
        st.error("Bitte gib einen Commander ein.")
    elif not uploaded_csv:
        st.error("Bitte lade deine Collection als CSV hoch.")
    else:
        with st.spinner("Baue Deck... ⏳"):
            commander_info = get_card_info(commander_name)
            if not commander_info:
                st.error("Commander nicht gefunden.")
            else:
                # Sammlung einlesen
                import pandas as pd
                df = pd.read_csv(uploaded_csv)
                card_names = df.iloc[:, 0].tolist()

                pool = []
                progress = st.progress(0)
                for i, cname in enumerate(card_names):
                    info = get_card_info(cname)
                    if info:
                        pool.append(info)
                    progress.progress((i + 1) / len(card_names))
                    time.sleep(0.05)  # API entlasten

                deck = build_deck(
                    commander_info, pool, keywords, avg_cmc, bracket
                )

                # Deck-Analyse
                st.success(f"✅ Deck fertig! {len(deck)} Karten gefunden.")
                df_deck = pd.DataFrame([{
                    "Name": c.get("name"),
                    "Mana Value": c.get("cmc"),
                    "Type": c.get("type_line"),
                    "Colors": c.get("colors")
                } for c in deck])

                # Sortier-Option
                sort_choice = st.radio("📊 Sortiere Karten nach", ["Kartentyp", "Funktion"], horizontal=True)
                if sort_choice == "Kartentyp":
                    df_deck = df_deck.sort_values("Type")
                elif sort_choice == "Funktion":
                    df_deck = df_deck.sort_values("Mana Value")

                st.dataframe(df_deck)

                # Export-Option
                st.download_button(
                    "📤 Deckliste als CSV exportieren",
                    df_deck.to_csv(index=False),
                    "deck.csv",
                    "text/csv"
                )

