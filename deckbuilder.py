import streamlit as st
import requests
import pandas as pd
import time

st.set_page_config(page_title="MTG Deckbuilder", layout="wide")
st.title("Magic: The Gathering Deckbuilder")

# ------------------------------
# Datei-Upload
# ------------------------------
uploaded = st.file_uploader("Moxfield Collection (.txt oder .csv)", type=["txt", "csv"])

# ------------------------------
# Commander Input
# ------------------------------
commander_name = st.text_input("Commander Name:", "")

# ------------------------------
# Deck-Ideen / Keywords
# ------------------------------
keywords = st.text_input("Deck Keywords (z.B. Ramp, Card Draw, Aggro):", "")

# ------------------------------
# Mana-Curve Slider
# ------------------------------
avg_cmc = st.slider("Durchschnittliche Mana-Kosten", min_value=1, max_value=10, value=5, step=1)

# ------------------------------
# Bracket / Spielstil
# ------------------------------
bracket = st.selectbox("Deck Bracket:", ["Casual", "Competitive", "EDH"])

# ------------------------------
# Preisfilter für neue Karten
# ------------------------------
max_price = st.number_input("Maximaler Preis für vorgeschlagene Karten (€):", min_value=0.0, value=5.0, step=0.5)

# ------------------------------
# Caching der API-Aufrufe
# ------------------------------
@st.cache_data(show_spinner=False)
def get_card_info(name):
    url = f"https://api.scryfall.com/cards/named?fuzzy={name}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except requests.exceptions.RequestException:
        return None
    return None

def get_card_price(card):
    prices = card.get("prices", {})
    eur = prices.get("eur")
    if eur:
        return float(eur)
    usd = prices.get("usd")
    if usd:
        return float(usd)
    return 0.0

# ------------------------------
# Deckbau-Funktion
# ------------------------------
def build_deck(commander_info, pool, keywords, avg_cmc, bracket):
    if not commander_info:
        return []

    commander_colors = commander_info.get("colors", [])
    
    # Filter Pool nach Commander-Farben
    legal_pool = [c for c in pool if all(col in commander_colors for col in c.get("colors", []))]

    # Filter nach Keywords
    filtered_pool = []
    for card in legal_pool:
        name = card.get("name", "").lower()
        text = card.get("oracle_text", "").lower()
        if any(k.lower() in name or k.lower() in text for k in keywords.split(",")) or not keywords:
            filtered_pool.append(card)

    # Sortierung nach EDHREC-Rank / Power-Level
    filtered_pool.sort(key=lambda x: x.get("edhrec_rank", 100000))
    
    # Mana-Curve berücksichtigen
    filtered_pool.sort(key=lambda x: x.get("cmc", 0))
    index = int(len(filtered_pool) * (avg_cmc / 10))
    deck_pool = filtered_pool[index:index+39] if len(filtered_pool) >= 39 else filtered_pool

    deck = [commander_info] + deck_pool
    return deck

# ------------------------------
# Decksortierung nach Typ oder Funktion
# ------------------------------
def sort_deck(deck, sort_option):
    if sort_option == "Kartentyp":
        deck.sort(key=lambda x: x.get("type_line", ""))
    elif sort_option == "Funktion":
        deck.sort(key=lambda x: x.get("oracle_text", ""))
    return deck

# ------------------------------
# Vorschläge für fehlende Karten
# ------------------------------
def suggest_missing_cards(deck, pool_names, max_price):
    suggestions = []
    for card in deck:
        name = card.get("name")
        if name not in pool_names:
            price = get_card_price(card)
            if price <= max_price:
                suggestions.append((name, price))
    return suggestions

# ------------------------------
# Deck bauen Button
# ------------------------------
if st.button("Deck bauen"):
    if not uploaded:
        st.error("Bitte zuerst eine Collection hochladen!")
    else:
        # Karten aus Datei auslesen
        if uploaded.name.endswith(".csv"):
            df = pd.read_csv(uploaded)
            if "Name" not in df.columns:
                st.error("CSV muss eine Spalte 'Name' enthalten!")
                st.stop()
            card_names = df["Name"].tolist()
        else:  # Textdatei
            card_names = [line.decode("utf-8").strip() for line in uploaded]

        # Commander Info
        commander_info = get_card_info(commander_name)
        if not commander_info:
            st.error("Commander nicht gefunden!")
            st.stop()

        # Pool bauen mit Fortschrittsbalken
        pool = []
        progress_bar = st.progress(0)
        for i, card_name in enumerate(card_names):
            info = get_card_info(card_name)
            if info:
                pool.append(info)
            progress_bar.progress((i+1)/len(card_names))
            time.sleep(0.05)  # kleine Pause für Stabilität auf Streamlit Cloud

        # Deck bauen
        deck = build_deck(commander_info, pool, keywords, avg_cmc, bracket)

        if deck:
            st.success(f"Deck mit {len(deck)} Karten gebaut!")

            # Decksortierung nach Auswahl
            sort_option = st.selectbox("Sortiere Deck nach:", ["Keine Sortierung", "Kartentyp", "Funktion"])
            if sort_option != "Keine Sortierung":
                deck = sort_deck(deck, sort_option)

            # Deck anzeigen
            pool_names_set = set(card_names)
            for card in deck:
                name = card.get("name", "")
                type_line = card.get("type_line", "")
                cmc = card.get("cmc", 0)
                st.write(f"- {name} | {type_line} | CMC: {cmc}")

            # Vorschläge für neue Karten
            missing_cards = suggest_missing_cards(deck, pool_names_set, max_price)
            if missing_cards:
                st.info("Vorgeschlagene Karten, die du noch nicht besitzt:")
                for name, price in missing_cards:
                    st.write(f"- {name} | Preis: €{price}")

            # Export als CSV
            export_df = pd.DataFrame([{"Name": c.get("name"), "Type": c.get("type_line"), "CMC": c.get("cmc")} for c in deck])
            csv = export_df.to_csv(index=False).encode('utf-8')
            st.download_button("Deck als CSV exportieren", data=csv, file_name="deck.csv", mime="text/csv")
        else:
            st.error("Deck konnte nicht gebaut werden.")

