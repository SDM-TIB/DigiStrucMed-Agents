"""Live boards: what an agent just said in a call, restructured into cards next to the video.

the llm writes SpecStream (json-render's format, github.com/vercel-labs/json-render): JSONL, one RFC 6902 patch
per line. the browser applies every finished line, so cards appear while the model is still writing.
the model only restructures. it is told not to add a single fact.
"""
import config
import llm

# every name checked against lucide-static 1.52.0 on jsdelivr. the browser falls back to 'sparkles'
ICONS = ['heart-pulse', 'heart', 'pill', 'pill-bottle', 'activity', 'droplet', 'droplets', 'shield-alert', 'shield-check',
         'stethoscope', 'syringe', 'thermometer', 'brain', 'scale', 'apple', 'dumbbell', 'footprints', 'moon', 'cigarette-off',
         'wine-off', 'salad', 'clock', 'calendar', 'triangle-alert', 'info', 'circle-check', 'circle-x', 'ban', 'hospital',
         'microscope', 'flask-conical', 'dna', 'test-tube', 'bed-double', 'siren', 'zap', 'gauge', 'trending-up', 'trending-down',
         'list-checks', 'book-open', 'file-text', 'network', 'search', 'sparkles', 'user', 'users', 'baby', 'leaf', 'utensils',
         'bike', 'timer', 'hand-heart', 'bandage', 'ambulance']

# the component catalogue, in one place: the prompt is generated from it, and the browser checks every element against
# it (GET /api/board-schema) before showing it. an element that breaks it is dropped alone, the rest of the board stays.
# the idea comes from openui (github.com/thesysdev/openui, spec/language.md section 8): typed props, smallest unit dropped
COMPONENTS = {
  'Point': {'use': 'one separate idea; not for a sequence (use Steps) or two options (use Compare)',
            'props': {'icon': 'icon', 'title': ('text', 6), 'text': ('text', 18)}, 'required': ['title']},
  'Stat': {'use': 'a short value the speaker gave, e.g. "Class I", "Level A", "< 140 mmHg", "2 weeks"',
           'props': {'icon': 'icon', 'value': ('text', 4), 'label': ('text', 5)}, 'required': ['value', 'label']},
  'Warning': {'use': 'cautions, contraindications, side effects, emergencies the speaker mentioned',
              'props': {'icon': 'icon', 'text': ('text', 20)}, 'required': ['text']},
  'Steps': {'use': 'an order of actions or stages the speaker described, in that order',
            'props': {'title': ('text', 5), 'items': ('list', 5, 10)}, 'required': ['items']},
  'Compare': {'use': 'two options the speaker contrasted, side by side',
              'props': {'leftTitle': ('text', 4), 'left': ('list', 4, 8), 'rightTitle': ('text', 4), 'right': ('list', 4, 8)},
              'required': ['leftTitle', 'left', 'rightTitle', 'right']},
  'Chips': {'use': 'a labelled list of short items (drugs, symptoms, foods)',
            'props': {'label': ('text', 4), 'items': ('list', 6, 4)}, 'required': ['items']},
  'Ask': {'use': 'always last, once: 2 or 3 questions the patient could ask next, about what was just said',
          'props': {'items': ('questions', 3, 10)}, 'required': ['items']},
}


def _prop(name, kind):
  if kind == 'icon':
    return 'icon'
  if kind[0] == 'text':
    return f'{name}: max {kind[1]} words'
  return f'{name}: {"up to " + str(kind[1])} {"questions" if kind[0] == "questions" else "strings"} of max {kind[2]} words'


def schema():
  """the catalogue as the browser checks it: types, word limits, icons"""
  return {'icons': ICONS, 'components': {'Board': {'props': {'title': ['text', 6]}, 'required': []},
          **{k: {'props': {p: list(v) if isinstance(v, tuple) else v for p, v in c['props'].items()}, 'required': c['required']}
             for k, c in COMPONENTS.items()}}}


SYSTEM = (
  "You turn what a medical video assistant just said into a small visual board shown next to the video. "
  "Output ONLY JSONL: one RFC 6902 JSON Patch operation per line, nothing else (no prose, no code fences).\n"
  "The UI spec is {\"root\": string, \"elements\": {key: {\"type\": string, \"props\": object, \"children\": [keys]}}}.\n"
  "Line 1: {\"op\":\"add\",\"path\":\"/root\",\"value\":\"board\"}\n"
  "Line 2: {\"op\":\"add\",\"path\":\"/elements/board\",\"value\":{\"type\":\"Board\",\"props\":{\"title\":\"...\"},\"children\":[]}}\n"
  "Then for each item two lines: add the element, then append its key, e.g.\n"
  "{\"op\":\"add\",\"path\":\"/elements/p1\",\"value\":{\"type\":\"Point\",\"props\":{...},\"children\":[]}}\n"
  "{\"op\":\"add\",\"path\":\"/elements/board/children/-\",\"value\":\"p1\"}\n"
  "Components (props):\n- Board {title: max 6 words}\n"
  + ''.join(f"- {k} {{{', '.join(_prop(p, v) for p, v in c['props'].items())}}}  {c['use']}\n" for k, c in COMPONENTS.items())
  + f"icon must be one of: {', '.join(ICONS)}. Pick the icon that best fits each item.\n"
  "Choose the components that fit how the speaker structured it: Steps for a sequence, Compare for two options, "
  "Point for separate ideas. End with one Ask unless the text is small talk. Rules: 2 to 5 children under the board. Use only what the text says, in its own words: "
  "never add facts, numbers, mechanisms or explanations that are not in the text, and never complete a sentence the "
  "speaker did not say. Ask holds questions only, never answers. "
  "Write in the language of the text. If the text is only small talk with no content, output just the two Board lines with a fitting title "
  "and one Point summarising it."
)


def board(text):
  if not config.LLM_API_KEY:
    yield {'type': 'error', 'message': 'not configured'}
    return
  try:
    for kind, val in llm.stream([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': str(text)[:2500]}]):
      if kind == 'text':
        yield {'type': 'delta', 'text': val}
    yield {'type': 'done'}
  except Exception as e:
    yield llm.error_event(e)
