"""Next steps after an answer: questions the patient could ask next, and questions to take to their doctor.

the llm writes questions only, never statements: a question cannot assert a wrong fact. they are built from the
patient's question and the agent's answer, in the patient's language, short enough to tap.
"""
import json

import config
import llm

SYSTEM = (
  "A heart failure patient asked a question and an assistant answered from clinical guidelines. Suggest what the "
  "patient could do next, as questions in the patient's own words and in the language of their question.\n"
  "- ask: 2 short follow-up questions to ask the assistant next, about what the answer left open or a natural next step\n"
  "- doctor: 2 questions to bring to their own doctor, about their personal situation (their dose, their values, "
  "whether it applies to them)\n"
  "Rules: questions only, each at most 12 words, no statements, no facts, no numbers or doses the answer did not "
  "mention, no medical advice. Plain words a patient would use.\n"
  'Return JSON only: {"ask": ["...", "..."], "doctor": ["...", "..."]}')


def suggest(question, answer):
  if not config.LLM_API_KEY or len(str(answer or '')) < 40:
    return {'ask': [], 'doctor': []}
  try:
    raw, _ = llm.complete([{'role': 'system', 'content': SYSTEM},
                           {'role': 'user', 'content': f'Question: {str(question)[:500]}\n\nAnswer:\n{str(answer)[:2500]}'}],
                          max_tokens=300, response_format={'type': 'json_object'})
    out = json.loads(raw[raw.index('{'):raw.rindex('}') + 1])
  except Exception as e:
    return {'error': type(e).__name__}
  keep = lambda xs: [str(x).strip()[:120] for x in (xs or []) if str(x).strip().endswith('?')][:2]  # questions, or nothing
  return {'ask': keep(out.get('ask')), 'doctor': keep(out.get('doctor'))}
