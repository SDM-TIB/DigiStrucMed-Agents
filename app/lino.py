"""Lino: Llama 3.2 1B with Mahsa's NeSyCoT LoRA, served by vLLM on the Modal GPU (gpu/serve.py).

no retrieval, no tools, no guardrails. Lino answers from what the fine-tune put into the weights.
that is the point of the demo: compare him with Luna (rag) and Guido (graph walk).
"""
import config
import gpu
import llm

# persona and language lines for the demo, then Mahsa's DEFAULT_DOMAIN_PROMPT from BRINK/brink_chat.py, unchanged
SYSTEM = (
  "Your name is Lino. You answer from your own knowledge, stating what the "
  "clinical guideline evidence you have internalized supports. "
  "Answer in the language of the question: English, German, Spanish, Russian or Hindi. "
  "You are a language model fine-tuned on a knowledge graph built from "
  "scientific medical guidelines. The graph encodes relationships between "
  "entities such as populations, patients, procedures, drugs, side effects, "
  "recommendations, levels of evidence, and the source documents they come "
  "from. You were fine-tuned with neuro-symbolic chain-of-thought rules mined "
  "from this graph, so you have internalized those relationships. Answer each "
  "question using this medical-guideline knowledge, directly and concisely."
)
MODEL = llm.Endpoint(config.LINO_URL + '/v1', config.LINO_API_KEY, 'lino', {'temperature': 0, 'repetition_penalty': 1.1})


def chat(messages):
  if not gpu.online():
    yield {'type': 'error', 'message': 'Lino is offline. Wake him up first.'}
    return
  try:
    # 1024 tokens: storyboards come back as json with several scenes
    for kind, val in llm.stream(llm.history(messages, keep=10, max_chars=4000, system=SYSTEM), max_tokens=1024, ep=MODEL, timeout=120):
      if kind == 'text':
        yield {'type': 'text', 'delta': val}
    yield {'type': 'done'}
  except Exception as e:
    if getattr(e, 'code', None) == 503:  # modal took the container away
      gpu.mark_offline()
    yield llm.error_event(e, 'Lino')


def answer(question):
  """one shot answer for the video call. it gets spoken verbatim, so cut at the last full sentence"""
  messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': str(question)[:2000]}]
  text, finish = llm.complete(messages, max_tokens=400, ep=MODEL)
  if finish == 'length':
    cut = max(text.rfind('. '), text.rfind('! '), text.rfind('? '), text.rfind('.\n'))
    text = text[:cut + 1] if cut > len(text) * 0.4 else text
  return text
