import os
import re
import json
import time
from datetime import datetime
from importlib.metadata import version, PackageNotFoundError
import pandas as pd
from dotenv import load_dotenv
from VoucherVision import VoucherVisionClient

# ---------------------------------------------------------------------------
# Load hidden credentials & config
# ---------------------------------------------------------------------------
# Load environment variables from the hidden .env file
load_dotenv(dotenv_path=".env", override=True)

CSV_FILE_PATH = "PLE_20260819_upload.csv"   # Path to your input CSV
INDEX_COLUMN_NAME = "catalogNumber"         # Column name containing the image index
URL_COLUMN_NAME = "associatedMedia"         # Column name containing the image URLs
BASE_OUTPUT_DIR = "output/"                 # Parent directory; each run gets its own subfolder

SERVER_URL = "https://vouchervision-go-738307415303.us-central1.run.app"
API_KEY = os.environ.get("VVGO_API_KEY")
VERTEX_PROJECT = os.environ.get("VVGO_VERTEX_PROJECT")
VERTEX_REGION = os.environ.get("VVGO_VERTEX_REGION", "global")
CLIENT_TIMEOUT = (15, 900)

# Processing options passed to process_url(). Kept in one place so the exact
# settings used are also written to run_parameters.json.
PROCESS_OPTIONS = {
    "verbose": True,
    "engines": ["gemini-3.1-flash-lite"],
    "llm_model": "gemini-3.1-flash-lite",
    "prompt": "SLTPvM_geolocate.yaml",
    "ocr_only": False,
    "notebook_mode": False,
    "skip_label_collage": False,
    "include_wfo": True,
    "include_cop90": True,
    "ocr_thinking_level": "high",
    "llm_thinking_level": "high",
    "on_error": "raise",
}

# ---------------------------------------------------------------------------
# Create a folder for this run, named by its start date and time
# ---------------------------------------------------------------------------
run_start_dt = datetime.now()
RUN_ID = run_start_dt.strftime("%Y%m%d_%H%M%S")
RUN_DIR = os.path.join(BASE_OUTPUT_DIR, f"run_{RUN_ID}")
os.makedirs(RUN_DIR, exist_ok=False)  # exist_ok=False so a run never overwrites another

LOG_CSV_PATH = os.path.join(RUN_DIR, "processing_log.csv")
PARAMS_JSON_PATH = os.path.join(RUN_DIR, "run_parameters.json")

try:
    client_version = version("vouchervision-go-client")
except PackageNotFoundError:
    client_version = "unknown"


def safe_filename(value, fallback):
    """Turn an index value into a filesystem-safe name (catalog numbers can contain / : etc.)."""
    if pd.isna(value) or str(value).strip() == "":
        return fallback
    name = str(value).strip()
    # Drop a trailing ".0" if pandas read an integer column as float
    if re.fullmatch(r"\d+\.0", name):
        name = name[:-2]
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name)


def write_params(params):
    with open(PARAMS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=False, default=str)


def process_url_with_retry(client, image_url):
    for attempt in range(6):
        try:
            return client.process_url(image_url, **PROCESS_OPTIONS)
        except Exception as e:
            is_quota_error = (
                "the gemini account supplied with this request is over its quota."
                in str(e).lower()
            )
            if not is_quota_error or attempt == 5:
                raise
            print(
                f"  --> Quota error: {e}. Waiting 60 seconds "
                f"before retry {attempt + 1}/5."
            )
            time.sleep(60)


# ---------------------------------------------------------------------------
# Read input and record run parameters (written now so they exist even if the run crashes)
# ---------------------------------------------------------------------------
# Read the index column as text so catalog numbers keep leading zeros etc.
df = pd.read_csv(CSV_FILE_PATH, dtype={INDEX_COLUMN_NAME: str})

run_params = {
    "run_id": RUN_ID,
    "run_start_time": run_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
    "run_end_time": None,
    "input_csv": CSV_FILE_PATH,
    "index_column": INDEX_COLUMN_NAME,
    "url_column": URL_COLUMN_NAME,
    "num_records": len(df),
    "server_url": SERVER_URL,
    "client_package_version": client_version,
    "payment_method": "vertex" if VERTEX_PROJECT else "vouchervision_credits",
    "vertex_project": VERTEX_PROJECT,   # API keys are deliberately NOT saved
    "vertex_region": VERTEX_REGION,
    "timeout": CLIENT_TIMEOUT,
    "process_options": PROCESS_OPTIONS,
    "summary": None,
}
write_params(run_params)

# ---------------------------------------------------------------------------
# Process CSV using VoucherVisionClient Context Manager
# ---------------------------------------------------------------------------
log_records = []
used_names = set()

print(f"Run folder: {RUN_DIR}")
print(f"Loaded {len(df)} records. Starting processing using VoucherVisionClient...\n")

with VoucherVisionClient(
    server_url=SERVER_URL,
    auth_token=API_KEY,
    gemini_api_key=None,
    vertex_project=VERTEX_PROJECT,
    vertex_region=VERTEX_REGION,
    timeout=CLIENT_TIMEOUT,
    session=None,
) as client:

    for index, row in df.iterrows():
        image_url = row[URL_COLUMN_NAME]

        # Build the file name from the index column (fallback to row number if blank)
        fname = safe_filename(row[INDEX_COLUMN_NAME], fallback=f"specimen_{index}")
        # Avoid overwriting if two rows share the same catalog number
        if fname in used_names:
            fname = f"{fname}_{index}"
        used_names.add(fname)
        json_path = os.path.join(RUN_DIR, f"{fname}.json")

        # Capture start time
        start_dt = datetime.now()
        start_time_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        t0 = time.time()

        status = "Success"
        error_message = ""
        error_code = None
        http_status = None
        request_id = None
        payment_inference = None
        total_cost_usd = None

        print(f"[{index + 1}/{len(df)}] Processing {fname}: {image_url}")

        try:
            # process_url takes the URL as its first argument and does not write
            # files locally, so the JSON is saved below.
            result = process_url_with_retry(client, image_url)

            # Save the full server response, same as `curl ... -o file.json`
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False, default=str)

            if isinstance(result, dict):
                payment_inference = result.get("payment_inference")
                total_cost_usd = result.get("total_request_cost_usd")

        except Exception as e:
            status = "Failed"
            error_message = str(e)
            json_path = ""
            # VoucherVisionAPIError exposes these attributes; other exceptions won't have them
            http_status = getattr(e, "status_code", None)
            error_code = getattr(e, "error_code", None)
            request_id = getattr(e, "request_id", None)
            payment_inference = getattr(e, "payment_inference", None)
            print(f"  --> Error processing row {index} ({fname}): {e}")

        # Capture end time and calculate duration
        end_dt = datetime.now()
        end_time_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        duration_seconds = round(time.time() - t0, 2)

        # Store execution log details
        log_records.append({
            "row_index": index,
            INDEX_COLUMN_NAME: row[INDEX_COLUMN_NAME],
            "file_name": fname,
            "json_path": json_path,
            "image_url": image_url,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "duration_seconds": duration_seconds,
            "status": status,
            "payment_inference": payment_inference,
            "total_request_cost_usd": total_cost_usd,
            "http_status": http_status,
            "error_code": error_code,
            "request_id": request_id,
            "error": error_message,
        })
        pd.DataFrame([log_records[-1]]).to_csv(
            LOG_CSV_PATH, mode="a", header=len(log_records) == 1, index=False
        )

# ---------------------------------------------------------------------------
# Finalize run parameters
# ---------------------------------------------------------------------------
log_df = pd.DataFrame(log_records)

n_success = int((log_df["status"] == "Success").sum()) if len(log_df) else 0
run_params["run_end_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
run_params["summary"] = {
    "succeeded": n_success,
    "failed": len(log_df) - n_success,
    "total_duration_seconds": round(log_df["duration_seconds"].sum(), 2) if len(log_df) else 0,
    "total_request_cost_usd": round(pd.to_numeric(log_df["total_request_cost_usd"], errors="coerce").sum(), 6)
    if len(log_df) else 0,
}
write_params(run_params)

print("\n--- Processing Complete ---")
print(f"Run folder:  {RUN_DIR}")
print(f"Log saved to: {LOG_CSV_PATH}")
print(f"Parameters:  {PARAMS_JSON_PATH}")
log_df[[INDEX_COLUMN_NAME, "image_url", "start_time", "end_time", "duration_seconds", "status"]]