import streamlit as st
import requests
import pandas as pd

st.set_page_config(page_title="MTG Deckbuilder", layout="wide")
st.title("Magic: The Gathering Deckbuilder")

# ------------------------------
# Datei-Upload
# ------------------------------
uploaded = st.file_uploader("Moxfield Collection (.txt oder .csv)", type=["txt", "csv"])

# ------------------------------
# Commander Input
# ------------------------------
commander = st.text_input("Commander Name:", "")

# ------------------------------
# Deck-Ideen / Keywords
# ------------------------------
keywords = st.text_input("Deck Keywords (z.B. Ramp, Card Draw, Aggro):", "")

# ------------------------------
# Mana-Curve Slider
# ------------------------------
avg_cmc = st.slider("Durchschnittliche Mana-Kosten", min_value=1, max_value=10, value=5, step=1)

# ------------------------------
# Decksortierung
# ------------------------------
sort_option = st.selectbox("Sortiere Deck nach:", ["Kartentyp", "Funktion"])

# ------------------------------
# Bracket / Spielstil
# ------------------------------
bracket = st.selectbox("Deck Bracket:", ["Casual", "Competitive", "EDH"])

# ------------------------------
# Scryfall API Funktion
# ------------------------------
def get_card_info(name):
    url = f"https://api.scryfall.com/cards/named?fuzzy={name}"
    r = requests.get(url)
    if r.status_code == 200:
        return r.json()
    return None

# ------------------------------
# Einfache Deckbau-Funktion
# ------------------------------
def build_deck(commander, pool, keywords, avg_cmc, sort_option, bracket):
    if not commander:
        return []
    commander_info = get_card_info(commander)
    if not commander_info:
        return []
    
    # Filter Pool nach Keywords
    filtered_pool = []
    for card in pool:
        name = card.get("name", "").lower()
        text = card.get("oracle_text", "").lower()
        if any(k.lower() in name or k.lower() in text for k in keywords.split(",")) or not keywords:
            filtered_pool.append(card)
    
    # Mana-Curve Anpassung (einfaches Beispiel: höhere avg_cmc = spätere Karten)
    filtered_pool.sort(key=lambda x: x.get("cmc", 0))
    index = int(len(filtered_pool) * (avg_cmc / 10))
    deck_pool = filtered_pool[index:index+39] if len(filtered_pool) >= 39 else filtered_pool
    
    deck = [commander_info] + deck_pool
    
    # Sortierung
    if sort_option == "Kartentyp":
        deck.sort(key=lambda x: x.get("type_line", ""))
    elif sort_option == "Funktion":
        # Vereinfachte Sortierung nach Keywords im Text
        deck.sort(key=lambda x: x.get("oracle_text", ""))
    
    return deck

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
        
        # Pool bauen mit Fortschrittsbalken
        pool = []
        progress_bar = st.progress(0)
        for i, card_name in enumerate(card_names):
            info = get_card_info(card_name)
            if info:
                pool.append(info)
            progress_bar.progress((i+1)/len(card_names))
        
        # Deck bauen
        deck = build_deck(commander, pool, keywords, avg_cmc, sort_option, bracket)
        
        if deck:
            st.success(f"Deck mit {len(deck)} Karten gebaut!")
            
            # Deck anzeigen
            for card in deck:
                name = card.get("name", "")
                type_line = card.get("type_line", "")
                cmc = card.get("cmc", 0)
                st.write(f"- {name} | {type_line} | CMC: {cmc}")
            
            # Optional: Export Button (CSV)
            export_df = pd.DataFrame([{"Name": c.get("name"), "Type": c.get("type_line"), "CMC": c.get("cmc")} for c in deck])
            csv = export_df.to_csv(index=False).encode('utf-8')
            st.download_button("Deck als CSV exportieren", data=csv, file_name="deck.csv", mime="text/csv")
        else:
            st.error("Commander nicht gefunden oder Deck konnte nicht gebaut werden.")

