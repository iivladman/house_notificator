import requests
from bs4 import BeautifulSoup
import re
import traceback
import json
import os
import time
from dotenv import load_dotenv
import sys

# Load environment variables from .env file (for local development)
# In GitHub Actions, secrets are automatically available as environment variables
load_dotenv()

# File to store known listing IDs
KNOWN_LISTINGS_FILE = "known_listings.json"

# Load configuration from GitHub Secrets (or .env file for local development)
# GitHub Secrets are automatically exposed as environment variables in GitHub Actions
def get_required_env(key, description=""):
    """Get a required environment variable, raise error if not set."""
    value = os.getenv(key)
    if not value:
        error_msg = f"Required environment variable '{key}' is not set."
        if description:
            error_msg += f" {description}"
        print(f"ERROR: {error_msg}")
        print(f"Please set this as a GitHub Secret or in your .env file.")
        sys.exit(1)
    return value

# URL to monitor (from GitHub Secrets)
URL = get_required_env("URL", "The Kufar URL to monitor for new listings")

# Telegram Bot configuration (from GitHub Secrets)
# Get your bot token from @BotFather on Telegram
# Get your chat ID by messaging @userinfobot on Telegram
TELEGRAM_BOT_TOKEN = get_required_env("TELEGRAM_BOT_TOKEN", "Telegram bot token from @BotFather")
TELEGRAM_CHAT_ID = get_required_env("TELEGRAM_CHAT_ID", "Your Telegram chat ID from @userinfobot")

def get_all_listings():
    """
    Get all listings from the page and return them as a dictionary: {listing_id: (title, url)}
    Handles both /dom/{ID} and /dom/dacha/{ID} patterns in the URL.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    response = requests.get(URL, headers=headers)
    response.raise_for_status()
    # with open("text.txt", "w", encoding="utf-8") as f:
    #     f.write(response.text)
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Match /dom/{ID} or /dom/dacha/{ID} in the href
    listing_links = soup.find_all("a", href=re.compile(r"/dom(?:/dacha)?/\d+"))

    listings = {}
    for link in listing_links:
        href = link.get("href", "")
        # Try to extract listing ID from /dom/{ID} or /dom/dacha/{ID}
        match = re.search(r"/dom(?:/dacha)?/(\d+)", href)
        if match:
            listing_id = int(match.group(1))
            listing_title = link.get_text(strip=True)
            # Ensure full URL
            if href.startswith("http"):
                listing_url = href.split("?")[0]  # Remove query parameters
            else:
                listing_url = "https://re.kufar.by" + href.split("?")[0]
            listings[listing_id] = (listing_title, listing_url)
    
    return listings

def load_known_listings():
    """Load known listing IDs from file. Returns empty dict if file doesn't exist."""
    if os.path.exists(KNOWN_LISTINGS_FILE):
        try:
            with open(KNOWN_LISTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Convert keys back to int (JSON saves them as strings)
                return {int(k): tuple(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            print(f"Error loading known listings: {e}. Starting fresh.")
            return {}
    return {}

def save_known_listings(listings):
    """Save known listing IDs to file."""
    # Convert to dict with string keys for JSON
    data = {str(k): list(v) for k, v in listings.items()}
    with open(KNOWN_LISTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def send_telegram_message(text):
    """Send a message to Telegram chat via bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram bot not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.")
        print(f"[Telegram message would be: {text}]")
        return False
    
    try:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        response = requests.post(api_url, json=payload, timeout=10)
        response.raise_for_status()
        print("Telegram message sent successfully")
        return True
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        traceback.print_exc()
        return False

def notify(title, message, url):
    """Send Telegram notification about a new listing."""
    text = f"<b>{title}</b>\n{message}\n<a href='{url}'>View Listing</a>"
    send_telegram_message(text)

def main():
    # Load known listings from file
    known_listings = load_known_listings()
    
    if not known_listings:
        print("First run: Saving all current listings as baseline...")
        current_listings = get_all_listings()
        save_known_listings(current_listings)
        print(f"Saved {len(current_listings)} listing IDs. Exiting - will check for new listings on next run.")
        return
    
    print(f"Loaded {len(known_listings)} known listing IDs from previous run.")
    
    try:
        # Get all current listings from the page
        current_listings = get_all_listings()
        print(f"Found {len(current_listings)} listings on the page.")
        
        # Find new listings (in current but not in known)
        new_listings = {}
        for listing_id, (title, url) in current_listings.items():
            if listing_id not in known_listings:
                new_listings[listing_id] = (title, url)
        
        if new_listings:
            print(f"Found {len(new_listings)} new listing(s)!")
            
            # Send a separate Telegram notification for each new listing
            for listing_id in sorted(new_listings.keys(), reverse=True):
                title, url = new_listings[listing_id]
                print(f"New listing found: ID {listing_id} - ({url})")
                
                # Send individual notification for each listing
                message_text = f"🏠 <b>Новый дом появился на Kufar!</b>\n\n"
                message_text += f"<a href='{url}'>Просмотреть</a>"
                
                send_telegram_message(message_text)
                # Small delay between messages to avoid rate limiting
                time.sleep(0.5)
            
            # Update known listings with the new ones
            known_listings.update(new_listings)
            save_known_listings(known_listings)
            print(f"Updated known listings. Total: {len(known_listings)}")
        else:
            print("No new listings found.")
            # Still update the file in case some listings were removed from page
            # but keep the known ones for reference
            save_known_listings(known_listings)
            
    except Exception as e:
        print(f"Error checking listings: {e}")
        traceback.print_exc()
        raise  # Re-raise to fail the GitHub Action if there's an error

if __name__ == "__main__":
    main()
