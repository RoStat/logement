"""Configuration centralisée du projet logement."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DVF_RAW_DIR = DATA_DIR / "raw" / "dvf"
DPE_RAW_DIR = DATA_DIR / "raw" / "dpe"
GEO_RAW_DIR = DATA_DIR / "raw" / "geo"
AIDES_RAW_DIR = DATA_DIR / "raw" / "aides"
PARQUET_DIR = DATA_DIR / "parquet"
DB_PATH = DATA_DIR / "logement.db"

DVF_BASE_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv"
DPE_API_URL = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe-v2-logements-existants/lines"
BAN_GEOCODE_URL = "https://api-adresse.data.gouv.fr"
BAN_DATA_URL = "https://adresse.data.gouv.fr/data/ban"

DPE_RATE_LIMIT = 5
DPE_PAGE_SIZE = 10000

COVERAGE_MIN_VENTES = 15
COVERAGE_MIN_DPE = 10

FEATURE_LEADS = False
