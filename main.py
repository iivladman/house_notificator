import requests
from bs4 import BeautifulSoup
import re
import traceback
import json
import os
import time
from dotenv import load_dotenv
import sys
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Load environment variables from .env file (for local development)
# In GitHub Actions, secrets are automatically available as environment variables
load_dotenv()

# Files to store known listing IDs (one per source)
KNOWN_LISTINGS_FILE = "known_listings.json"
KNOWN_LISTINGS_FILE_REALT = "known_listings_realt.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

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
# Realt.by URL (optional - skipped if not set)
URL_2 = os.getenv("URL_2")

# Telegram Bot configuration (from GitHub Secrets)
# Get your bot token from @BotFather on Telegram
# Get your chat ID by messaging @userinfobot on Telegram
TELEGRAM_BOT_TOKEN = get_required_env("TELEGRAM_BOT_TOKEN", "Telegram bot token from @BotFather")
TELEGRAM_CHAT_ID = get_required_env("TELEGRAM_CHAT_ID", "Your Telegram chat ID from @userinfobot")

# Safety limit for the number of Kufar pages to scan
KUFAR_MAX_PAGES = 100

def set_query_param(url, key, value):
    """Return the URL with the given query parameter set to value."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[key] = [str(value)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

def get_page_listings_kufar(url):
    """
    Get listings from a single Kufar page.
    Returns (listings, next_cursor) where listings is a dictionary {listing_id: (title, url)}
    and next_cursor is the token for the next page (None on the last page).
    Handles both /dom/{ID} and /dom/dacha/{ID} patterns in the URL.
    """
    response = requests.get(url, headers=HEADERS, timeout=30)
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

    # Kufar embeds pagination as JSON: {"label":"next","num":2,"token":"..."}
    match = re.search(r'"label":"next","num":\d+,"token":"([^"]+)"', response.text)
    next_cursor = match.group(1) if match else None
    
    return listings, next_cursor

def get_all_listings(url):
    """
    Get Kufar listings from all result pages by following the 'next' cursor tokens.
    """
    listings = {}
    page_url = url
    for page in range(1, KUFAR_MAX_PAGES + 1):
        page_listings, next_cursor = get_page_listings_kufar(page_url)
        listings.update(page_listings)
        print(f"Kufar page {page}: {len(page_listings)} listings")
        if not next_cursor:
            break
        page_url = set_query_param(url, "cursor", next_cursor)
        # Small delay between requests to be polite to the site
        time.sleep(1)

    return listings

# Safety limit for the number of Realt.by pages to scan
REALT_MAX_PAGES = 100

def get_page_listings_realt(url):
    """
    Get listings from a single Realt.by page and return them as a dictionary: {listing_id: (title, url)}
    Handles /sale-cottages/object/{ID}/ pattern in the URL.
    """
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    listing_links = soup.find_all("a", href=re.compile(r"/object/\d+"))

    listings = {}
    for link in listing_links:
        href = link.get("href", "")
        match = re.search(r"/object/(\d+)", href)
        if match:
            listing_id = int(match.group(1))
            listing_title = link.get_text(strip=True)
            if href.startswith("http"):
                listing_url = href.split("?")[0]
            else:
                listing_url = "https://realt.by" + href.split("?")[0]
            listings[listing_id] = (listing_title, listing_url)

    return listings

def get_all_listings_realt(url):
    """
    Get Realt.by listings from all result pages, starting from page 1.
    Stops when a page has no listings or only listings already seen on earlier pages.
    """
    listings = {}
    for page in range(1, REALT_MAX_PAGES + 1):
        page_listings = get_page_listings_realt(set_query_param(url, "page", page))
        new_ids = page_listings.keys() - listings.keys()
        if not new_ids:
            break
        listings.update(page_listings)
        print(f"Realt page {page}: {len(page_listings)} listings")
        # Small delay between requests to be polite to the site
        time.sleep(1)

    return listings

def load_known_listings(filename):
    """Load known listing IDs from file. Returns empty dict if file doesn't exist."""
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Convert keys back to int (JSON saves them as strings)
                return {int(k): tuple(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            print(f"Error loading known listings: {e}. Starting fresh.")
            return {}
    return {}

def save_known_listings(listings, filename):
    """Save known listing IDs to file."""
    # Convert to dict with string keys for JSON
    data = {str(k): list(v) for k, v in listings.items()}
    with open(filename, "w", encoding="utf-8") as f:
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

def check_source(source_name, url, fetch_listings, known_listings_file):
    """Check one source for new listings and send a Telegram notification for each new one."""
    print(f"--- Checking {source_name} ---")

    # Load known listings from file
    known_listings = load_known_listings(known_listings_file)

    # First run (file doesn't exist, or is empty/corrupted): save baseline without notifications
    if not os.path.exists(known_listings_file) or not known_listings:
        print("First run: Saving all current listings as baseline (no notifications)...")
        current_listings = fetch_listings(url)
        save_known_listings(current_listings, known_listings_file)
        print(f"Saved {len(current_listings)} listing IDs. Will check for new listings on next run.")
        return
    
    print(f"Loaded {len(known_listings)} known listing IDs from previous run.")
    
    # Get all current listings from the page
    current_listings = fetch_listings(url)
    print(f"Found {len(current_listings)} listings on the page.")
    
    # Find new listings (in current but not in known)
    new_listings = {}
    for listing_id, (title, listing_url) in current_listings.items():
        if listing_id not in known_listings:
            new_listings[listing_id] = (title, listing_url)
    
    if new_listings:
        print(f"Found {len(new_listings)} new listing(s)!")
        
        # Send a separate Telegram notification for each new listing
        for listing_id in sorted(new_listings.keys(), reverse=True):
            title, listing_url = new_listings[listing_id]
            print(f"New listing found: ID {listing_id} - ({listing_url})")
            
            # Send individual notification for each listing
            message_text = f"🏠 <b>Новый дом появился на {source_name}!</b>\n\n"
            message_text += f"<a href='{listing_url}'>Просмотреть</a>"
            
            send_telegram_message(message_text)
            # Small delay between messages to avoid rate limiting
            time.sleep(0.5)
        
        # Update known listings with the new ones
        known_listings.update(new_listings)
        save_known_listings(known_listings, known_listings_file)
        print(f"Updated known listings. Total: {len(known_listings)}")
    else:
        print("No new listings found.")

def main():
    sources = [("Kufar", URL, get_all_listings, KNOWN_LISTINGS_FILE)]
    if URL_2:
        sources.append(("Realt", URL_2, get_all_listings_realt, KNOWN_LISTINGS_FILE_REALT))
    else:
        print("URL_2 is not set - skipping Realt.by check.")

    failed = False
    for source in sources:
        # Check each source independently so one failing site doesn't block the others
        try:
            check_source(*source)
        except Exception as e:
            print(f"Error checking {source[0]} listings: {e}")
            traceback.print_exc()
            failed = True

    if failed:
        sys.exit(1)  # Fail the GitHub Action if there's an error

if __name__ == "__main__":
    main()
