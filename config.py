import os

from pathlib import Path



from dotenv import load_dotenv



load_dotenv()



BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))

CNZSQH_BASE_URL = os.getenv("CNZSQH_BASE_URL", "https://www.cnzsqh.com")
RDQH_BASE_URL = os.getenv("RDQH_BASE_URL", "https://www.rdqh.com")
PDF_OCR_MAX_PAGES = int(os.getenv("PDF_OCR_MAX_PAGES", "8"))
OCR_TALL_MAX_CHUNKS = int(os.getenv("OCR_TALL_MAX_CHUNKS", "3"))



DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'financial_forecast.db'}")



LLM_ENABLED = os.getenv("LLM_ENABLED", "true").lower() in {"1", "true", "yes"}

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "wenhua").lower()

LLM_ONLY_UNKNOWN = os.getenv("LLM_ONLY_UNKNOWN", "true").lower() in {"1", "true", "yes"}

LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

LLM_MAX_CONTENT_CHARS = int(os.getenv("LLM_MAX_CONTENT_CHARS", "4000"))

LLM_RETRY_COUNT = int(os.getenv("LLM_RETRY_COUNT", "3"))
LLM_RETRY_DELAY = float(os.getenv("LLM_RETRY_DELAY", "1.5"))
LLM_REQUEST_DELAY = float(os.getenv("LLM_REQUEST_DELAY", "0.3"))



WENHUA_AI_URL = os.getenv(

    "WENHUA_AI_URL",

    "https://swarm.wenhua.com.cn/aiservice/api/ShiXi/GetContent",

)



OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")



FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() in {"1", "true", "yes"}



TREND_LABELS = ["看涨", "看跌", "震荡", "偏多", "偏空", "中性", "未知"]

