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
LANGUAGES = [x.strip() for x in env('LANGUAGES', 'en,de,es,ru,hi').split(',') if x.strip()]
LANGUAGE_RULE = ("Languages: you speak English, German, Spanish, Russian and Hindi fluently and you may use any of them. Detect which one" " the user speaks or writes and answer in that language. If the user switches, switch with them. Keep drug names, cl" "asses of recommendation and levels of evidence exact in every language. Sources can be in another language; translate what you quote.")
# what makes the agents useful: look into the guidelines first, answer, caveat last
HELP_RULE = ("Be as helpful as you can. Treat every medical question, even a single word such as 'aspirin', as a request to look int"
             "o the guidelines: search right away and bring back everything relevant you find, such as indications, recommended dose"
             "s and dose ranges, timing, contraindications, interactions, side effects, monitoring, and the class of recommendation "
             "and level of evidence. If the first search misses, search again with broader or related terms before you say the guide"
             "lines are silent. Never refuse or deflect a question the guidelines can inform. Answer a dosing question with what the"
             " guidelines state about doses, for which patients and under which conditions. Only after that, add one short sentence "
             "that the individual dose is set by the treating physician. Do not open with what you cannot do.")

# tavus video calls
# one key per tavus account. plan minutes, renewal day and agent ids per account: data/tavus_accounts.json
TAVUS_API_KEY = env('TAVUS_API_KEY')
TAVUS_EXTRA_KEYS = env('TAVUS_EXTRA_KEYS')  # comma separated. a new account: add its key here, then run tavus/sync.py
TAVUS_KEYS = [k.strip() for k in [TAVUS_API_KEY, *TAVUS_EXTRA_KEYS.split(',')] if k.strip()]
MAX_CALL_SECONDS = int(env('MAX_CALL_SECONDS', '300'))

# the llm behind luna, guido and the live boards. any openai compatible api works.
# default and recommendation: gemini 3.5 flash-lite through google's openai endpoint. fast, cheap, good at tools
LLM_BASE_URL = env('LLM_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai')
LLM_API_KEY = env('LLM_API_KEY') or env('GEMINI_API_KEY')
LLM_MODEL = env('LLM_MODEL', 'gemini-3.5-flash-lite')
LLM_FALLBACK_MODEL = env('LLM_FALLBACK_MODEL', 'gemini-3.1-flash-lite')  # own rate limit, used when the main model is out of quota. empty disables
LLM_REASONING_EFFORT = env('LLM_REASONING_EFFORT', 'low')  # empty for apis that don't know the parameter

# lino lives on a modal gpu that sleeps when nobody talks to it. one modal workspace per token, each with its own
# monthly credits and its own copy of lino. names and urls: data/modal_accounts.json (deploy/deploy_new_modal.py)
LINO_API_KEY = env('LINO_API_KEY')
MODAL_TOKENS = [(i.strip(), s.strip()) for i, s in [(env('MODAL_TOKEN_ID'), env('MODAL_TOKEN_SECRET')),
                *(p.split(':', 1) for p in env('MODAL_EXTRA_TOKENS').split(',') if ':' in p)] if i.strip() and s.strip()]

# cloudflare workers ai, clef-flash directs guido's shared screen
CF_ACCOUNT_ID = env('CF_ACCOUNT_ID')
CF_API_TOKEN = env('CF_API_TOKEN')

# private gcs bucket: page renders, usage log. empty = local files only (dev)
BUCKET = env('PAGE_BUCKET')
LOCAL_BUCKET = ROOT.parent / 'page_images'
