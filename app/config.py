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
# what makes the agents useful: every question becomes 'what do the guidelines say about this?', answered in full
RESEARCH_RULE = ("Scope: anything about the heart and circulation: heart failure, coronary disease, heart attack, rhythm disorde"
                 "rs, valves, blood pressure, cholesterol, rehabilitation and exercise, diet, pregnancy, medicines, devices, tes"
                 "ts and procedures. You are a research assistant who reports what clinical guidelines say. Treat every question"
                 ", also a personal one such as 'should I take X?' or 'how much Y can I have?', and even a single word such as '"
                 "aspirin', as the research question 'what do the guidelines say about this?', and answer it in full: search rig"
                 "ht away and bring back every relevant detail you find, such as indications, the patients and conditions a reco"
                 "mmendation is meant for, doses, dose ranges and titration as the guidelines state them, timing, targets, contr"
                 "aindications, interactions, side effects, monitoring, the class of recommendation and level of evidence, and w"
                 "here guidelines differ. Report, do not prescribe: say 'the ESC guideline recommends ... for patients with ...'"
                 ", never 'you should take ...', and do not decide whether something applies to the user; name the factors the g"
                 "uidelines use to decide. Never refuse or deflect, never open with what you cannot do, and add no disclaimers o"
                 "r safety notes: the app shows its own note under every answer. If the first search misses, search again with b"
                 "roader or related terms before you say the guidelines are silent, then say what they state about the closest r"
                 "elated topic.")

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

# cloudflare workers ai: clef-flash directs guido's shared screen, decides the triage banners, judges lino's check
# and reranks luna's search (search.py)
CF_ACCOUNT_ID = env('CF_ACCOUNT_ID')
CF_API_TOKEN = env('CF_API_TOKEN')

# the final MHH guideline graph: SPARQL endpoint of the graph server (QLever, graph-server/). on cloud run the VM's
# internal address through Direct VPC egress; locally the IAP tunnel of graph-server/ui-tunnel.ps1
GRAPH_ENDPOINT = env('GRAPH_ENDPOINT', 'http://localhost:7001')

# EmbeddingGemma 2 embedding service for luna's search (embed-server/, a private Cloud Run service). empty = bm25 only
EMBED_URL = env('EMBED_URL')

# luna's agent loop runs in the agent server (agent-server/, Mastra on Cloud Run, private). AGENT_URL empty = the
# in-process loop of luna.py. The agent server calls the tool endpoints back (/api/agent/tools/*) with an identity
# token of this app's service account; AGENT_TOKEN is a shared token for local development only
AGENT_URL = env('AGENT_URL')
# guido's loop: 'local' = the evaluated 3-round loop in guido.py over concepts and recommendations; 'mastra' = guido v2 on
# the agent server, free loop, the guideline pages part of his graph, page search and reading. a request may choose
GUIDO_HARNESS = env('GUIDO_HARNESS', 'local')
AGENT_AISDK_URL = env('AGENT_AISDK_URL')
CLAIM_GUARD = env('CLAIM_GUARD') == '1'  # check uncited statements of luna's answers with clef-flash (claimguard.py)
SEARCH_CANDIDATES = env('SEARCH_CANDIDATES') or 'rrf'  # how luna's search finds the pages its reranker reads: rrf | digup (search.candidates)
LUNA_IMAGE_TOOL = env('LUNA_IMAGE_TOOL') == '1'  # view_page for luna's agent: the rendered PDF page as an image  # the second harness (agent-server-aisdk/, AI SDK 7), chosen per request with 'harness'
AGENT_TOKEN = env('AGENT_TOKEN')

# private gcs bucket: page renders, usage log. empty = local files only (dev)
BUCKET = env('PAGE_BUCKET')
LOCAL_BUCKET = ROOT.parent / 'page_images'
