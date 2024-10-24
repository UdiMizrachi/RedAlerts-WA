# Script from https://github.com/UdiMizrachi/RedAlerts-WA/tree/main 
# This script was based on https://github.com/t0mer/Redalert project .
# Disclaimer: Please do not use this for indications or anything related; it`s a project. Please consume Red Alerts through the proper official channels supplied by Pikud Ha Oref.
# **** All responsibility for using this script or data is your full responsibility only. ****

import aiohttp
import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from whatsapp_api_client_python import API
import pathlib
from loguru import logger

with open('config.json', 'r', encoding='utf-8-sig') as config_file:
    config = json.load(config_file)


GREEN_API_INSTANCE = config['green_api_instance']
GREEN_API_TOKEN = config['green_api_token']


WHATSAPP_GROUP_IDS = config['whatsapp_group_ids']
WHATSAPP_ERROR_NUMBER = config['whatsapp_error_number']


region = config['region']
debug = config['debug']
alerts_url = config['alerts_url']
lamas_github_url = config['lamas_github_url']
LOG_ALERTS_TO_FILE = config.get('log_alerts_to_file', False)
log_file_path = config.get('log_file_path', './logs')  # Path to log files
log_retention_days = config.get('log_retention_days', 7)
log_cleanup_time = config.get('log_cleanup_time', '03:00')  # Time to run log cleanup (HH:MM format)
MONITOR_INTERVAL = config.get('monitor_interval', 1)  # Monitor interval in seconds

# --- Configure Logger to File ---
script_name = pathlib.Path(__file__).stem
log_file = os.path.join(log_file_path, f"{script_name}_log.txt")
received_alerts_file = os.path.join(log_file_path, f"{script_name}_receivedalerts.txt")

# Create log directory if it doesn't exist
os.makedirs(log_file_path, exist_ok=True)

# Configure the logger to write to the log file
logger.add(log_file, rotation="1 MB", encoding="utf-8", enqueue=True)
logger.info(f"Logging to file: {log_file}")

# Setting Request Headers
_headers = {
    'Referer': 'https://www.oref.org.il/',
    'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.97 Safari/537.36",
    'X-Requested-With': 'XMLHttpRequest'
}

alerts = [0]
last_download_time = None
lamas = None
session = None


greenAPI = API.GreenAPI(GREEN_API_INSTANCE, GREEN_API_TOKEN)

#  Log Cleanup Function 
# --- Log Cleanup Function ---
def cleanup_logs():
    now = datetime.now()
    retention_period = timedelta(days=log_retention_days)

    # Check if the log directory exists
    if not os.path.exists(log_file_path):
        logger.warning(f"Log directory does not exist: {log_file_path}")
        return

    # Check if there are any log files in the directory
    log_files = os.listdir(log_file_path)
    if not log_files:
        logger.info(f"No log files found in the directory: {log_file_path}")
        return

    # Iterate over log files and remove those older than the retention period
    for filename in log_files:
        file_path = os.path.join(log_file_path, filename)

        if os.path.isfile(file_path):
            file_modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            if now - file_modified_time > retention_period:
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted old log file: {file_path}")
                except Exception as e:
                    logger.error(f"Failed to delete log file {file_path}: {e}")

#  Calculate Next Cleanup Time 
def get_next_cleanup_time():
    now = datetime.now()

    
    cleanup_hour, cleanup_minute = map(int, log_cleanup_time.split(":"))
    cleanup_time_today = now.replace(hour=cleanup_hour, minute=cleanup_minute, second=0, microsecond=0)

    # If the cleanup time today has passed, schedule it for the next day
    if now >= cleanup_time_today:
        cleanup_time_today += timedelta(days=1)

    return cleanup_time_today

#  Periodic Log Cleanup Task 
async def periodic_log_cleanup():
    while True:
        next_cleanup_time = get_next_cleanup_time()
        wait_time = (next_cleanup_time - datetime.now()).total_seconds()

        await asyncio.sleep(wait_time)

        cleanup_logs()
        logger.info("Log cleanup completed.")

#  Other Functions 
async def send_notification(number, message):
    try:
        await asyncio.to_thread(greenAPI.sending.sendMessage, number, message)
        logger.info(f"Notification message sent to {number}")
    except Exception as e:
        logger.error(f"Failed to send notification message to {number}: {str(e)}")

async def alarm_on(data):
    title = str(data["title"])
    wapp_title = f"*{title}*"
    categorized_places = categorize_places(lamas, data["data"])
    
    logger.debug(f"Categorized places: {categorized_places}")
    
    places = format_output(categorized_places)
    body = 'באזורים הבאים: \r\n ' + places
    logger.debug(f"Message body: {body}")

    try:
        await send_whatsapp_message(WHATSAPP_GROUP_IDS, wapp_title + "\r\n" + body)
        
        if LOG_ALERTS_TO_FILE:
            with open(received_alerts_file, 'a', encoding='utf-8') as file:
                file.write(f"Time: {datetime.now()}\nAlert Data:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n")
            logger.info(f"Alert data written to {received_alerts_file}")
    except Exception as e:
        error_message = f"Error sending WhatsApp message: {str(e)}"
        logger.error(error_message)
        await send_notification(WHATSAPP_ERROR_NUMBER, f"*Error Alert* \r\n {error_message}")

async def send_whatsapp_message(recipients, message):
    async def send_message(recipient):
        try:
            await asyncio.to_thread(greenAPI.sending.sendMessage, recipient, message)
            logger.info(f"WhatsApp message sent to {recipient}")
        except Exception as e:
            logger.error(f"Failed to send message to {recipient}: {str(e)}")

    await asyncio.gather(*[send_message(recipient) for recipient in recipients])

def alarm_off():
    logger.info("No active alerts")

def is_test_alert(alert):
    return 'בדיקה' in alert['data'] or 'בדיקה מחזורית' in alert['data']

def standardize_name(name):
    return re.sub(r'[\(\)\'\"]+', '', name).strip()

async def download_lamas_data_async(url, file_path, etag_file_path):
    etag = None

    try:
        with open(etag_file_path, 'r') as etag_file:
            etag = etag_file.read().strip()
    except FileNotFoundError:
        pass

    headers = {'If-None-Match': etag} if etag else {}

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 304:
                logger.info("Lamas data has not changed. No need to download.")
                return None

            response.raise_for_status()
            lamas_data_text = await response.text()

            try:
                lamas_data = json.loads(lamas_data_text)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode JSON: {e}")
                return None

            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(lamas_data, file, ensure_ascii=False, indent=2)

            logger.info(f"Lamas data downloaded from GitHub and saved to {file_path}")

            if 'ETag' in response.headers:
                new_etag = response.headers['ETag']
                with open(etag_file_path, 'w') as etag_file:
                    etag_file.write(new_etag)

            return lamas_data

    except Exception as e:
        logger.error(f"Error downloading Lamas data: {e}")
        return None

async def load_lamas_data():
    global last_download_time
    file_path = 'lamas.json'
    etag_file_path = 'lamas_etag.txt'

    lamas_data = None

    try:
        with open(file_path, 'r', encoding='utf-8-sig') as file:
            lamas_data = json.load(file)
        logger.info("Lamas data loaded from local file")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error loading Lamas data from local file: {e}")
    
    now = datetime.now()
    if last_download_time is None or (now - last_download_time) > timedelta(hours=24):
        new_lamas_data = await download_lamas_data_async(lamas_github_url, file_path, etag_file_path)
        
        if new_lamas_data is not None:
            lamas_data = new_lamas_data
            last_download_time = now
            logger.info("Lamas data successfully downloaded and updated.")
            await send_notification(WHATSAPP_ERROR_NUMBER, "*Lamas Data Update*\r\nThe Lamas data file has been successfully updated.")
        else:
            logger.info("Lamas data has not changed or couldn't be downloaded. Using existing local data.")
    else:
        logger.info("Using existing Lamas data (last update was less than 24 hours ago)")
    
    if not lamas_data:
        logger.error("Failed to load any Lamas data.")
    
    return lamas_data

def categorize_places(data, places):
    categorized_places = {}
    areas_dict = {area: set(area_places) for area, area_places in data['areas'].items()}

    for place in places:
        found = False
        for area, area_places in areas_dict.items():
            if place in area_places:
                categorized_places.setdefault(area, []).append(place)
                found = True
                break
        
        if not found:
            categorized_places.setdefault("כללי", []).append(place)
    
    return categorized_places

def format_output(categorized_places):
    result = []
    for area in sorted(categorized_places.keys()):
        # Sort places a-z in area 
        sorted_places = sorted(categorized_places[area])
        # Join the cities/places with commas
        places_str = ", ".join(sorted_places)
        result.append(f"*ישובי {area}*: {places_str}")
    return "\r\n".join(result)

async def monitor():
    try:
        if debug:
            # Use the debug_alerts_path from config.json
            with open(debug_alerts_path, 'r', encoding='utf-8-sig') as f:
                alert_data = f.read()
        else:
            async with session.get(alerts_url, headers=_headers) as response:
                response.raise_for_status()
                alert_data = await response.text(encoding='utf-8-sig')

                # Clean the data by removing null bytes and stripping whitespace
                alert_data = alert_data.replace('\x00', '').strip()

        logger.debug(f"Alert data received: {alert_data}")

        if alert_data and alert_data != '' and not alert_data.isspace():
            try:
                alert = json.loads(alert_data)
                logger.info("Alert data successfully parsed.")

                if region in alert["data"] or region == "*":
                    if alert["id"] not in alerts and not is_test_alert(alert):
                        alerts.append(alert["id"])
                        await alarm_on(alert)
                        logger.info(f"Processed alert: {str(alert)}")
            except json.JSONDecodeError as je:
                logger.debug(f"Received invalid JSON data: {je}. Raw data: {alert_data}")
        else:
            alarm_off()

    except Exception as ex:
        error_message = f"Error in monitor function: {str(ex)}"
        logger.error(error_message)
        await send_notification(WHATSAPP_ERROR_NUMBER, f"*Error Alert* \r\n {error_message}")

async def main():
    global session, lamas
    
    # Create a single aiohttp session for the entire runtime
    session = aiohttp.ClientSession()
    
    try:
        # Load Lamas data
        lamas = await load_lamas_data()
        
        # Send notification when the script starts
        await send_notification(WHATSAPP_ERROR_NUMBER, "*Script Started*\r\nThe alert monitoring script has started successfully.")

        logger.info(f"Starting monitor loop. Checking every {MONITOR_INTERVAL} seconds.")

        asyncio.create_task(periodic_log_cleanup())

        # Run the monitoring in a continuous loop
        while True:
            start_time = asyncio.get_event_loop().time()

            if lamas:
                await monitor()

            # Calculate the time taken for the monitor() function to run
            elapsed_time = asyncio.get_event_loop().time() - start_time

            # If the monitor() function took less than MONITOR_INTERVAL, wait for the remaining time
            if elapsed_time < MONITOR_INTERVAL:
                await asyncio.sleep(MONITOR_INTERVAL - elapsed_time)
            else:
                # If monitor() took longer than MONITOR_INTERVAL, log a warning
                logger.warning(f"Monitor cycle took longer than interval: {elapsed_time:.2f} seconds")

    finally:
        # Ensure session is closed  
        await session.close()

if __name__ == '__main__':
    asyncio.run(main())
