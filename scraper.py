import json
import time
import re
import logging
import random
import os
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from webdriver_manager.chrome import ChromeDriverManager

# ========= CONFIG =========
JSON_FILE      = "D:\\projects\\College_gsoc\\contributors.json"
OUTPUT_FILE    = "linkedin_results.csv"
BATCH_SIZE     = 50       # how many users to process per run
PAGE_LOAD_WAIT = 20

SCHOOL_SIGNALS = (
    "university", "college", "institute", "iit", "nit", "vit", "bits",
    "school of", "academy", "polytechnic", "deemed",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger(__name__)


# ========= HELPERS =========

def human_delay(a=4, b=9):
    time.sleep(random.uniform(a, b))

def split_name(username):
    return re.sub(r'([a-z])([A-Z])', r'\1 \2', username)

def build_query(username):
    name = split_name(username)
    name = re.sub(r'[^a-zA-Z0-9 ]', ' ', name).strip()
    return f"{name} GSoC'26"

def safe_get(driver, url, retries=3):
    for attempt in range(retries):
        try:
            driver.get(url)
            return True
        except Exception as e:
            log.warning(f"  Retry {attempt+1}: {e}")
            time.sleep(4)
    return False


# ========= DRIVER =========

def create_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """
    })
    return driver


# ========= LOAD USERS =========

def load_usernames(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    users = []
    for p in data.get("entities", {}).get("projects", []):
        name = p.get("contributor_name", "").strip("_").strip()
        if name:
            users.append(name)

    log.info(f"Total users in JSON: {len(users)}")
    return users


# ========= RESUME LOGIC =========

def load_done_users():
    """Return set of usernames already processed in previous runs."""
    if not os.path.exists(OUTPUT_FILE):
        return set(), []

    try:
        df = pd.read_csv(OUTPUT_FILE)
    except pd.errors.EmptyDataError:
        log.warning("CSV file is empty — starting fresh")
        return set(), []

    # Old CSV may not have a 'username' column — handle gracefully
    if "username" not in df.columns:
        log.warning("Old CSV format detected (no 'username' column) — starting fresh but keeping old file as backup")
        df.to_csv(OUTPUT_FILE + ".bak", index=False)
        return set(), []

    done = set(df["username"].dropna().tolist())
    records = df.to_dict("records")
    log.info(f"Resuming — {len(done)} users already done from previous runs")
    return done, records


# ========= PROFILE SCRAPER =========

def scrape_profile(driver, profile_url):
    log.info(f"  → Visiting: {profile_url}")

    if not safe_get(driver, profile_url):
        return "Unknown", "N/A"

    wait = WebDriverWait(driver, PAGE_LOAD_WAIT)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    try:
        wait.until(EC.visibility_of_element_located((By.XPATH, "//h1")))
    except TimeoutException:
        pass

    human_delay(3, 6)

    # Scroll to trigger lazy-loaded sections (education etc.)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
    time.sleep(2)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)

    # ---- NAME ----
    name = "Unknown"
    for xpath in ["//h1//span[@aria-hidden='true']", "//h1"]:
        try:
            txt = driver.find_element(By.XPATH, xpath).text.strip()
            if txt:
                name = txt
                break
        except NoSuchElementException:
            continue

    # ---- COLLEGE ----
    college = "N/A"
    NAV_NOISE = {
        "home", "my network", "jobs", "messaging", "notifications",
        "search", "follow", "connect", "message", "more"
    }

    # Strategy 1: scan all visible leaf text nodes for school keywords
    try:
        els = driver.find_elements(
            By.XPATH, "//*[not(*) and string-length(normalize-space(text())) > 2]"
        )
        for el in els[:100]:
            t = el.text.strip()
            if t.lower() in NAV_NOISE:
                continue
            if any(sig in t.lower() for sig in SCHOOL_SIGNALS):
                college = t
                break
    except Exception:
        pass

    # Strategy 2: targeted education section selectors
    if college == "N/A":
        for xpath in [
            "//*[contains(@class,'school')]//span[@aria-hidden='true']",
            "//*[contains(@class,'education')]//li[1]//span[@aria-hidden='true']",
            "//section[contains(@id,'education')]//li[1]//span[@aria-hidden='true']",
        ]:
            try:
                txt = driver.find_element(By.XPATH, xpath).text.strip()
                if txt:
                    college = txt
                    break
            except NoSuchElementException:
                continue

    return name, college


# ========= SEARCH =========

def search_and_scrape(driver, query):
    """Search LinkedIn for query, grab first result profile, return (name, college)."""
    url = f"https://www.linkedin.com/search/results/people/?keywords={query.replace(' ', '%20')}"

    if not safe_get(driver, url):
        return None

    wait = WebDriverWait(driver, PAGE_LOAD_WAIT)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(5)  # wait for React to render results

    driver.execute_script("window.scrollTo(0, 400);")
    time.sleep(2)

    # Grab first valid profile link from results
    try:
        links = driver.find_elements(By.XPATH, "//a[contains(@href, '/in/')]")
        for link in links:
            href = link.get_attribute("href") or ""
            if "/in/" not in href:
                continue
            path = href.split("/in/")[-1].split("?")[0]
            if len(path) < 3:           # skip junk like /in/a
                continue
            clean = f"https://www.linkedin.com/in/{path}"
            human_delay(2, 5)
            return scrape_profile(driver, clean)
    except Exception as e:
        log.warning(f"  Link extraction failed: {e}")

    log.warning("  No profile link found on search page")
    return None


# ========= SAVE =========

def save(records):
    pd.DataFrame(records).to_csv(OUTPUT_FILE, index=False)
    log.info(f"💾 Saved {len(records)} records → {OUTPUT_FILE}")


# ========= MAIN =========

def main():
    all_users = load_usernames(JSON_FILE)
    done_set, records = load_done_users()

    # Users not yet processed
    pending = [u for u in all_users if u not in done_set]
    log.info(f"Pending: {len(pending)} users")

    if not pending:
        log.info("✅ All users already processed!")
        return

    # Take only next BATCH_SIZE users
    batch = pending[:BATCH_SIZE]
    log.info(f"This run will process {len(batch)} users (batch of {BATCH_SIZE})")
    log.info(f"First: {batch[0]}  |  Last: {batch[-1]}")

    # ---- START BROWSER ----
    driver = create_driver()
    driver.get("https://www.linkedin.com/login")
    input("\n👉 Log in manually in the browser, then press ENTER here...\n")
    time.sleep(3)
    log.info("✅ Logged in — starting batch\n")

    for i, user in enumerate(batch):
        log.info(f"[{i+1}/{len(batch)}] Processing: {user}")

        query = build_query(user)
        log.info(f"  🔍 Query: {query}")

        result = search_and_scrape(driver, query)

        if result:
            name, college = result
            records.append({"username": user, "name": name, "college": college})
            log.info(f"  ✅ {name} | 🏫 {college}")
        else:
            records.append({"username": user, "name": "Not found", "college": "N/A"})
            log.warning(f"  ⚠️  No result for {user}")

        # Save after every user so no data is lost on crash
        save(records)

        # Brief pause to glance at result before next search
        log.info("  ⏳ Next search in 5s...")
        time.sleep(5)

        human_delay()   # additional random polite delay

    driver.quit()

    # ---- SUMMARY ----
    df = pd.DataFrame(records)
    log.info(f"\n🎉 Batch done! {len(batch)} users processed.")
    log.info(f"   Total in CSV so far: {len(records)}")
    log.info(f"   Remaining after this run: {len(pending) - len(batch)}")

    # ---- COLLEGE-WISE OUTPUT ----
    college_df = df[df["college"] != "N/A"]

    # JSON: { "IIT Bombay": ["Alice", "Bob"], ... }
    grouped = college_df.groupby("college")["name"].apply(list).reset_index()
    college_dict = dict(zip(grouped["college"], grouped["name"]))
    with open("college_wise.json", "w", encoding="utf-8") as f:
        json.dump(college_dict, f, indent=2, ensure_ascii=False)

    # CSV: one row per college with count + names
    grouped["count"] = grouped["name"].apply(len)
    grouped["names"] = grouped["name"].apply(lambda x: ", ".join(x))
    grouped[["college", "count", "names"]].sort_values("count", ascending=False).to_csv(
        "college_wise.csv", index=False
    )

    log.info("   📊 College breakdown → college_wise.json + college_wise.csv")


if __name__ == "__main__":
    main()