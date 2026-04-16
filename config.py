import os
from dotenv import load_dotenv

# Load environment variables from .env file when running locally
load_dotenv()


class Config:
    # Flask-style secret key for session middleware
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')

    # Local upload folder for temporary file storage
    UPLOAD_FOLDER = 'uploads'

    # OpenAI configuration — NEVER hardcode API keys
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
    OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')

    # Databricks configuration — all values come from environment variables
    DATABRICKS_HOST = os.environ.get('DATABRICKS_HOST', '')
    DATABRICKS_TOKEN = os.environ.get('DATABRICKS_TOKEN', '')
    DATABRICKS_HTTP_PATH = os.environ.get('DATABRICKS_HTTP_PATH', '')
    DATABRICKS_CATALOG = os.environ.get('DATABRICKS_CATALOG', 'main')
    DATABRICKS_SCHEMA = os.environ.get('DATABRICKS_SCHEMA', 'careerai')