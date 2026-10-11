"""Every knob the service reads from the environment. One place, no surprises."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / 'data'
STATIC = ROOT / 'static'


def _dotenv(path):
  """local dev: the repo's .env. real environment variables win. on cloud run the file doesn't exist"""
  if path.is_file():
    for line in path.read_text(encoding='utf-8').splitlines():
      if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


_dotenv(ROOT.parent / '.env')


def env(name, default=''):
  return os.environ.get(name, default).strip()


PORT = int(env('PORT', '8080'))

# passwords. the team password opens the app, the admin password opens /dashboard
DEMO_PASSWORD = env('DEMO_PASSWORD')
ADMIN_PASSWORD = env('ADMIN_PASSWORD')

# every agent may speak these. calls listen for all of them, the first one opens the call
LANGUAGES = [x.strip() for x in env('LANGUAGES', 'en,de,ru').split(',') if x.strip()]
LANGUAGE_RULE = ("Languages: you speak English, German and Russian fluently and you may use any of them. Detect which one the user "
                 "speaks or writes and answer in that language. If the user switches, switch with them. Keep drug names, classes of "
                 "recommendation and levels of evidence exact in every language. Sources can be in another language; translate what you quote.")

# tavus video calls
TAVUS_API_KEY = env('TAVUS_API_KEY')
MAX_CALL_SECONDS = int(env('MAX_CALL_SECONDS', '300'))
TAVUS_PLAN_MINUTES = float(env('TAVUS_PLAN_MINUTES', '120'))
TAVUS_PERIOD_START = env('TAVUS_PERIOD_START')            # ISO date, empty = first of the month
TAVUS_USED_OFFSET_MIN = float(env('TAVUS_USED_OFFSET_MIN', '0'))  # calibrate against the tavus dashboard

# the llm behind luna, guido and the live boards. any openai compatible api works.
# default and recommendation: gemini 3.5 flash-lite through google's openai endpoint. fast, cheap, good at tools
LLM_BASE_URL = env('LLM_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai')
LLM_API_KEY = env('LLM_API_KEY') or env('GEMINI_API_KEY')
LLM_MODEL = env('LLM_MODEL', 'gemini-3.5-flash-lite')
LLM_REASONING_EFFORT = env('LLM_REASONING_EFFORT', 'low')  # empty for apis that don't know the parameter

# lino lives on a modal gpu that sleeps when nobody talks to it
LINO_URL = env('LINO_URL').rstrip('/')
LINO_API_KEY = env('LINO_API_KEY')

# cloudflare workers ai, clef-flash directs guido's shared screen
CF_ACCOUNT_ID = env('CF_ACCOUNT_ID')
CF_API_TOKEN = env('CF_API_TOKEN')

# private gcs bucket: page renders, usage log. empty = local files only (dev)
BUCKET = env('PAGE_BUCKET')
LOCAL_BUCKET = ROOT.parent / 'page_images'
