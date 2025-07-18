import time
import os
import sys
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime
import pytz
import re
import json
import logging
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from dateutil.parser import parse as parse_date

# --- Custom Imports from our project ---
from database import SessionLocal, Inventory, Store, init_db

# --- Configurable Variables ---
URL = "https://manager.yokaiexpress.com/#/standStoreManager"
# Load credentials from environment variables for production, with fallbacks for local development
USERNAME = os.getenv("YOKAI_USERNAME", "overthere")
PASSWORD = os.getenv("YOKAI_PASSWORD", "88888888")


def get_credentials():
    """從環境變數中獲取帳號密碼"""
    username = os.getenv('YOKAI_USERNAME')
    password = os.getenv('YOKAI_PASSWORD')
    if not username or not password:
        raise ValueError("錯誤：環境變數 YOKAI_USERNAME 或 YOKAI_PASSWORD 未設定。")
    return username, password


def scrape_all_inventory_text():
    """
    Launches Selenium, logs into the website, scrapes all inventory data from all pages,
    and returns it as a single raw text string.
    Runs in headless mode for server deployment.
    """
    logging.info("Initializing browser in headless mode...")
    
    # --- Chrome Options for Headless Execution on a Server ---
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox") # A standard requirement for running as root/in a container
    options.add_argument("--disable-dev-shm-usage") # Overcomes limited resource problems
    options.add_argument("--disable-gpu") # Applicable to windows os only
    options.add_argument("window-size=1920,1080") # Set a window size to avoid issues with responsive layouts

    driver = webdriver.Chrome(options=options)
    all_scraped_text = ""

    try:
        driver.get(URL)
        wait = WebDriverWait(driver, 10)

        # --- Login ---
        logging.info("Logging in...")
        username, password = get_credentials()
        logging.info(f"找到輸入框，正在輸入帳號: {username[:4]}****") # 出於安全，只顯示部分帳號
        username_field = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@placeholder='User name']")))
        username_field.send_keys(username)
        password_field = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Password']")))
        password_field.send_keys(password)
        login_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Login') or contains(., 'Sign in')]")))
        login_button.click()
        logging.info("Login successful.")

        # --- 等待一下下 ---
        time.sleep(3) # 等待 3 秒，你可以根據需要調整秒數

        # --- Navigate to Inventory ---
        logging.info("Navigating to Store Management...")
        store_management_link = wait.until(EC.element_to_be_clickable((By.XPATH, "//li[.//span[contains(text(), 'Store management')]]")))
        store_management_link.click()

        # --- Full Inventory Scraping Logic with Pagination ---
        page_number = 1
        total_stores_processed = 0

        while True:
            logging.info(f"--- Preparing to process Page {page_number} ---")
            rows_xpath = "//div[contains(@class, 'el-table__body-wrapper')]//tr"
            wait.until(EC.presence_of_all_elements_located((By.XPATH, rows_xpath)))

            num_rows_on_page = len(driver.find_elements(By.XPATH, rows_xpath))
            if num_rows_on_page == 0:
                logging.info("No stores found on the page, assuming end of scraping.")
                break
            logging.info(f"Found {num_rows_on_page} stores on page {page_number}.")

            for i in range(num_rows_on_page):
                total_stores_processed += 1
                logging.info(f"Processing store #{total_stores_processed} (Page {page_number}, Row {i+1})...")
                
                inquiry_buttons = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//button[.//span[contains(text(), 'Inventory inquiry')]]")))
                if i < len(inquiry_buttons):
                    driver.execute_script("arguments[0].click();", inquiry_buttons[i])
                else:
                    logging.error(f"  > Error: Could not find button for row {i+1}. Skipping.")
                    continue

                inventory_container = wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-v-d0a9a5c0 and @class='container']")))
                inventory_text = inventory_container.text
                logging.info("  > Scraped inventory data.")

                # Accumulate text instead of writing to file
                all_scraped_text += f"--- Store #{total_stores_processed} ---\n{inventory_text}\n{'-'*20}\n\n"

                close_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//li[contains(@class, 'tags-li') and contains(@class, 'active')]//i[contains(@class, 'el-icon-close')]")))
                close_button.click()
                logging.info("  > Closed inventory tab.")
                wait.until(EC.presence_of_element_located((By.XPATH, rows_xpath)))

                if page_number > 1:
                    logging.info(f"  > Navigating back to page {page_number}...")
                    target_page_button_xpath = f"//ul[contains(@class, 'el-pager')]//li[text()='{page_number}']"
                    target_page_button = wait.until(EC.element_to_be_clickable((By.XPATH, target_page_button_xpath)))
                    target_page_button.click()
                    time.sleep(0.5)
                    logging.info(f"  > Returned to page {page_number}.")

            # --- Go to next page ---
            try:
                next_page_to_click = page_number + 1
                logging.info(f"\nFinished page {page_number}. Attempting to move to page {next_page_to_click}...")
                next_page_button_xpath = f"//ul[contains(@class, 'el-pager')]//li[text()='{next_page_to_click}']"
                next_page_button = wait.until(EC.element_to_be_clickable((By.XPATH, next_page_button_xpath)))
                next_page_button.click()
                time.sleep(2)
                page_number += 1
            except Exception:
                logging.info(f"\nCould not find button for page {next_page_to_click}. Assuming it's the last page.")
                break
        
        logging.info(f"\nScraping complete. Processed {total_stores_processed} stores in total.")
        
        # Replace text in the accumulated string before returning
        logging.info("\nReplacing 'Last replenishment time' with '上次補貨時間' in memory...")
        all_scraped_text = all_scraped_text.replace("Last replenishment time", "上次補貨時間")
        logging.info("Replacement complete.")
        
        return all_scraped_text

    except Exception as e:
        logging.error(f"An error occurred during scraping: {e}", exc_info=True)
        return "" # Return empty string on error
    finally:
        logging.info("Closing the browser.")
        driver.quit()


def parse_inventory_from_text(raw_text):
    """
    Parses the raw inventory text string, which is grouped by store,
    into a structured list of dictionaries.
    """
    logging.info("\nStarting to parse data in Python...")
    final_result = []
    
    # Define the target timezone
    taipei_tz = pytz.timezone('Asia/Taipei')
    
    # Split the entire text into blocks for each store
    store_blocks = raw_text.split('--- Store #')[1:]
    
    if not store_blocks:
        logging.warning("Warning: Could not find any store blocks in the raw text.")
        return []

    for block in store_blocks:
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        
        if "No Data" in lines:
            continue

        # Extract time information for the current block
        last_updated = "N/A"
        replenish_match = re.search(r'上次補貨時間\s*:\s*(.+)', block)
        if replenish_match:
            last_updated = replenish_match.group(1).strip()
            
        # The actual data starts after the header 'Store name Machine name Product name Inventory quantity'
        try:
            data_start_index = lines.index('Store name Machine name Product name Inventory quantity') + 1
        except ValueError:
            # If header is not found, start from the first line that looks like data
            data_start_index = 1 

        # The lines we need to process are from data_start_index to before the '---...---' separator
        data_lines = []
        for line in lines[data_start_index:]:
            if line.startswith('Total') or line.startswith('--------------------'):
                break
            data_lines.append(line)

        # Process data in chunks of 4 lines
        if len(data_lines) % 4 != 0:
            logging.warning(f"Warning: Data lines count ({len(data_lines)}) is not a multiple of 4 for a store block. Skipping block.")
            continue
            
        for i in range(0, len(data_lines), 4):
            store_name = data_lines[i]
            machine_id = data_lines[i+1]
            product_name = data_lines[i+2]
            quantity_str = data_lines[i+3]
            
            if not quantity_str.isdigit():
                logging.warning(f"Warning: Expected a number for quantity but got '{quantity_str}'. Skipping entry.")
                continue

            # Parse process_time string back to a datetime object
            process_time_dt = parse_date(datetime.now(taipei_tz).isoformat())

            final_result.append({
                'store': store_name,
                'machine_id': machine_id,
                'product_name': product_name,
                'quantity': int(quantity_str),
                'last_updated': last_updated,
                'process_time': process_time_dt
            })

    if not final_result:
        logging.warning("Warning: Failed to parse any product items from the raw data.")
        return []

    logging.info(f"Parsing complete. Found {len(final_result)} product items.")
    return final_result


def save_to_database(data: list):
    """
    Saves the structured data to the database using SQLAlchemy, with a retry mechanism
    for transient network errors.
    """
    if not data:
        logging.info("No data to save to database.")
        return

    max_retries = 3
    retry_delay_seconds = 5

    for attempt in range(max_retries):
        db: Session = SessionLocal()
        try:
            db.begin()

            num_deleted = db.query(Inventory).delete()
            logging.info(f"Cleared {num_deleted} old records from the inventory table.")

            inventory_objects = [Inventory(**item) for item in data]
            db.bulk_save_objects(inventory_objects)

            db.commit()
            logging.info(f"Successfully saved {len(inventory_objects)} new records to the database.")
            return  # Success, exit the function

        except OperationalError as e:
            db.rollback()
            logging.error(f"Database connection error (Attempt {attempt + 1}/{max_retries}): {e}")
            if attempt + 1 < max_retries:
                logging.info(f"Retrying in {retry_delay_seconds} seconds...")
                time.sleep(retry_delay_seconds)
            else:
                logging.error("Max retries reached. Giving up.")
                raise e
        except Exception as e:
            logging.error(f"An unexpected database error occurred: {e}", exc_info=True)
            db.rollback()
            raise e
        finally:
            db.close()


def save_to_json(data, filename):
    """Saves the structured data to a JSON file."""
    if not data:
        logging.info("No data to save.")
        return
        
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logging.info(f"\nStructured data successfully saved to {filename}")
    except Exception as e:
        logging.error(f"Error saving data to {filename}: {e}", exc_info=True)


def run_scraper(headless=True):
    """
    啟動爬蟲的主函數。
    :param headless: 是否以無頭模式運行瀏覽器。
    """
    logging.info("正在啟動爬蟲...")
    
    try:
        username, password = get_credentials()
    except ValueError as e:
        logging.error(e, file=sys.stderr)
        sys.exit(1) # 終止腳本

    # --- Modern Selenium Setup ---
    options = webdriver.ChromeOptions()
    if headless:
        # These are the crucial arguments for running Chrome in a containerized environment
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("window-size=1920,1080")

    # Selenium's built-in manager will handle the chromedriver
    service = webdriver.ChromeService() 
    driver = webdriver.Chrome(service=service, options=options)
    
    all_scraped_text = ""

    try:
        driver.get(URL)
        # Increase the wait time from 30 to 30 seconds to handle slower server response times
        wait = WebDriverWait(driver, 30)

        # --- Login ---
        logging.info("Logging in...")
        logging.info(f"找到輸入框，正在輸入帳號: {username[:4]}****") # 出於安全，只顯示部分帳號
        username_field = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@placeholder='User name']")))
        username_field.send_keys(username)
        password_field = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Password']")))
        password_field.send_keys(password)
        login_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Login') or contains(., 'Sign in')]")))
        login_button.click()
        logging.info("Login successful.")

        # --- Navigate to Inventory ---
        logging.info("Navigating to Store Management...")
        store_management_link = wait.until(EC.element_to_be_clickable((By.XPATH, "//li[.//span[contains(text(), 'Store management')]]")))
        store_management_link.click()

        # --- Full Inventory Scraping Logic with Pagination ---
        page_number = 1
        total_stores_processed = 0

        # --- Retry logic for initial page load ---
        page_load_retries = 3
        for attempt in range(page_load_retries):
            try:
                logging.info(f"Attempting to load store list (Attempt {attempt + 1}/{page_load_retries})...")
                rows_xpath = "//div[contains(@class, 'el-table__body-wrapper')]//tr"
                # Wait for the first row to be present.
                wait.until(EC.presence_of_element_located((By.XPATH, rows_xpath)))
                # Also check for the inquiry buttons as a secondary confirmation.
                wait.until(EC.presence_of_element_located((By.XPATH, "//button[.//span[contains(text(), 'Inventory inquiry')]]")))
                logging.info("Store list loaded successfully.")
                break  # If successful, exit the retry loop.
            except Exception as e:
                logging.warning(f"Store list failed to load on attempt {attempt + 1}: {e}")
                if attempt + 1 < page_load_retries:
                    logging.info("Refreshing page and retrying...")
                    driver.refresh()
                    time.sleep(2) # Wait a moment after refresh
                    # After refresh, we need to re-navigate.
                    store_management_link = wait.until(EC.element_to_be_clickable((By.XPATH, "//li[.//span[contains(text(), 'Store management')]]")))
                    store_management_link.click()
                else:
                    logging.error("Failed to load store list after multiple retries. Aborting.")
                    raise # Re-raise the last exception to be caught by the main handler

        while True:
            logging.info(f"--- Preparing to process Page {page_number} ---")
            rows_xpath = "//div[contains(@class, 'el-table__body-wrapper')]//tr"
            wait.until(EC.presence_of_all_elements_located((By.XPATH, rows_xpath)))

            num_rows_on_page = len(driver.find_elements(By.XPATH, rows_xpath))
            if num_rows_on_page == 0:
                logging.info("No stores found on the page, assuming end of scraping.")
                break
            logging.info(f"Found {num_rows_on_page} stores on page {page_number}.")

            for i in range(num_rows_on_page):
                total_stores_processed += 1
                logging.info(f"Processing store #{total_stores_processed} (Page {page_number}, Row {i+1})...")
                
                inquiry_buttons = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//button[.//span[contains(text(), 'Inventory inquiry')]]")))
                if i < len(inquiry_buttons):
                    driver.execute_script("arguments[0].click();", inquiry_buttons[i])
                else:
                    logging.error(f"  > Error: Could not find button for row {i+1}. Skipping.")
                    continue

                inventory_container = wait.until(EC.visibility_of_element_located((By.XPATH, "//div[@data-v-d0a9a5c0 and @class='container']")))
                inventory_text = inventory_container.text
                logging.info("  > Scraped inventory data.")

                # Accumulate text instead of writing to file
                all_scraped_text += f"--- Store #{total_stores_processed} ---\n{inventory_text}\n{'-'*20}\n\n"

                close_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//li[contains(@class, 'tags-li') and contains(@class, 'active')]//i[contains(@class, 'el-icon-close')]")))
                close_button.click()
                logging.info("  > Closed inventory tab.")
                wait.until(EC.presence_of_element_located((By.XPATH, rows_xpath)))

                if page_number > 1:
                    logging.info(f"  > Navigating back to page {page_number}...")
                    target_page_button_xpath = f"//ul[contains(@class, 'el-pager')]//li[text()='{page_number}']"
                    target_page_button = wait.until(EC.element_to_be_clickable((By.XPATH, target_page_button_xpath)))
                    target_page_button.click()
                    time.sleep(0.5)
                    logging.info(f"  > Returned to page {page_number}.")

            # --- Go to next page ---
            try:
                next_page_to_click = page_number + 1
                logging.info(f"\nFinished page {page_number}. Attempting to move to page {next_page_to_click}...")
                next_page_button_xpath = f"//ul[contains(@class, 'el-pager')]//li[text()='{next_page_to_click}']"
                next_page_button = wait.until(EC.element_to_be_clickable((By.XPATH, next_page_button_xpath)))
                next_page_button.click()
                time.sleep(2)
                page_number += 1
            except Exception:
                logging.info(f"\nCould not find button for page {next_page_to_click}. Assuming it's the last page.")
                break
        
        logging.info(f"\nScraping complete. Processed {total_stores_processed} stores in total.")
        
        # Replace text in the accumulated string before returning
        logging.info("\nReplacing 'Last replenishment time' with '上次補貨時間' in memory...")
        all_scraped_text = all_scraped_text.replace("Last replenishment time", "上次補貨時間")
        logging.info("Replacement complete.")
        
        return all_scraped_text

    except Exception as e:
        logging.error(f"An error occurred during scraping: {e}", exc_info=True)
        return "" # Return empty string on error
    finally:
        logging.info("Closing the browser.")
        driver.quit()


if __name__ == "__main__":
    # Setup basic logging for local testing
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        # 這裡的代碼只在直接運行 scraper.py 時執行，方便本地測試
        # 在伺服器環境中，server.py 會導入 run_scraper 函數並調用它
        
        # 為了本地測試，我們需要手動加載 .env 文件
        try:
            from dotenv import load_dotenv
            # 我們假設 .env 文件與 scraper.py 在同一個目錄
            dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
            if os.path.exists(dotenv_path):
                logging.info(f"正在從 {dotenv_path} 加載環境變數...")
                load_dotenv(dotenv_path=dotenv_path)
            else:
                logging.info(".env 文件未找到，將依賴於系統已設置的環境變數。")

        except ImportError:
            logging.warning("警告: python-dotenv 未安裝。本地測試時請確保手動設置環境變數。")
        
        # 1. Execute the web scraper to get raw text
        # We set headless=True for any automated run, local test or server.
        raw_inventory_text = run_scraper(headless=True)
        
        if raw_inventory_text:
            # 2. Parse the raw text into structured data
            structured_data = parse_inventory_from_text(raw_inventory_text)
            
            # --- Define output paths ---
            script_dir = os.path.dirname(os.path.abspath(__file__))
            output_json_path = os.path.join(script_dir, 'structured_inventory.json')
            
            # Convert datetime objects to string for JSON serialization
            for item in structured_data:
                if 'process_time' in item and hasattr(item['process_time'], 'isoformat'):
                    item['process_time'] = item['process_time'].isoformat()

            # 3. Save to JSON for verification
            save_to_json(structured_data, output_json_path)
            
            # 4. Initialize and save to the database (PostgreSQL or SQLite)
            init_db()
            # Re-parse dates before saving to DB
            for item in structured_data:
                if 'process_time' in item and isinstance(item['process_time'], str):
                    item['process_time'] = parse_date(item['process_time'])

            save_to_database(structured_data)
            
    except Exception as e:
        logging.error(f"An error occurred in the main execution block: {e}", exc_info=True)
