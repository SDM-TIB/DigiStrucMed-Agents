"""Lino on Modal: Llama 3.2 1B Instruct + Mahsa's NeSyCoT LoRA, merged, served by vLLM on one L4 (the next cheapest free GPU when no L4 is free, see GPUS).

  modal run gpu/serve.py::prepare   # once: base from ModelScope + gpu/adapter -> merged bf16 weights in a Volume
  modal deploy gpu/serve.py         # serve. scales to zero, the TIB Chat backend wakes it

follows modal-labs/modal-examples 06_gpu_and_ml/llm-serving/vllm_inference.py (image, volumes, @app.server).
what we changed and why, measured on Lino 2026-10-07:
  fp8                 the L4 (Ada) does fp8 natively. half the weight bytes per token. 97 -> 141 tok/s
  dspark speculation  a drafter trained for the base model. 141 -> ~190 tok/s, acceptance length 1.81
                      (the LoRA moves the target a bit away from what the drafter learned). eagle3 got ~150
  eu-west routing     the backend runs in Frankfurt
the api key comes from the Modal Secret `lino-api-key` and goes to vLLM's --api-key.
"""
import modal

MINUTES = 60
PORT = 8000
BASE = 'LLM-Research/Llama-3.2-1B-Instruct'  # ModelScope mirror, Llama 3.2 community license
DRAFTER = 'rasyosef/Llama-3.2-1B-Instruct-DSpark'
# Modal tries these in order, cheapest first (modal.com/pricing, 2026-11-03, per hour: L4 0.80, A10G 1.10, L40S 1.95,
# A100-40GB 2.10, A100-80GB 2.50, H100 3.95 USD). L4 is the measured setup; on 2026-11-03 Modal had no L4 free and
# the container waited for one without starting. Ada and Hopper (L40S, H100) run fp8 natively; Ampere (A10G, A100)
# runs it as weight-only Marlin kernels, slower and not measured. T4 is left out: no bf16 and no fp8 in vLLM
GPUS = ['L4', 'A10G', 'L40S', 'A100-40GB', 'A100-80GB', 'H100']

vllm_image = (
  modal.Image.from_registry('nvidia/cuda:12.9.0-devel-ubuntu22.04', add_python='3.12')
  .entrypoint([])
  .uv_pip_install('vllm==0.28.0')  # dspark needs vllm >= 0.28
  .env({'VLLM_LOG_STATS_INTERVAL': '5'})
)
prep_image = (
  modal.Image.debian_slim(python_version='3.12')
  .uv_pip_install('torch', 'transformers>=4.46', 'peft>=0.13', 'modelscope', 'safetensors', 'accelerate')
  .add_local_dir('gpu/adapter', '/adapter')
)
model_vol = modal.Volume.from_name('lino-model', create_if_missing=True)
vllm_cache = modal.Volume.from_name('vllm-cache', create_if_missing=True)
hf_cache = modal.Volume.from_name('huggingface-cache', create_if_missing=True)

app = modal.App('lino')


@app.function(image=prep_image, volumes={'/models': model_vol}, timeout=30 * MINUTES, memory=16384)
def prepare():
  import pathlib
  import shutil
  import torch
  from modelscope import snapshot_download
  from peft import PeftModel
  from transformers import AutoModelForCausalLM

  base_dir = snapshot_download(BASE, cache_dir='/tmp/ms')
  base = AutoModelForCausalLM.from_pretrained(base_dir, torch_dtype=torch.float32)  # merge in fp32, store bf16
  model = PeftModel.from_pretrained(base, '/adapter').merge_and_unload().to(torch.bfloat16)
  model.save_pretrained('/models/lino', safe_serialization=True)
  # keep the original tokenizer files. newer transformers re-serialise them in a format other tools choke on
  for name in ('tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'generation_config.json'):
    shutil.copy(f'{base_dir}/{name}', f'/models/lino/{name}')
  model_vol.commit()
  print(sorted(p.name for p in pathlib.Path('/models/lino').iterdir()))


@app.server(
  image=vllm_image,
  gpu=GPUS,
  scaledown_window=5 * MINUTES,  # the backend pings /health while lino should stay awake
  startup_timeout=15 * MINUTES,
  volumes={'/models': model_vol, '/root/.cache/vllm': vllm_cache, '/root/.cache/huggingface': hf_cache},
  secrets=[modal.Secret.from_name('lino-api-key')],
  port=PORT,
  routing_region='eu-west',
  unauthenticated=True,  # access control is vLLM's --api-key
)
class Server:
  @modal.enter()
  def start(self):
    import os
    import subprocess
    self.process = subprocess.Popen([
      'vllm', 'serve', '/models/lino',
      '--served-model-name', 'lino',
      '--host', '0.0.0.0', '--port', str(PORT),
      '--api-key', os.environ['LINO_API_KEY'],
      '--max-model-len', '4096',
      '--gpu-memory-utilization', '0.90',
      '--async-scheduling',
      '--quantization', 'fp8',
      '--speculative-config', f'{{"model": "{DRAFTER}", "num_speculative_tokens": 8, "method": "dspark"}}',
      '--no-enforce-eager',  # torch.compile + cuda graphs: slower boot, faster tokens
      '--uvicorn-log-level=info',
    ])

  @modal.exit()
  def stop(self):
    self.process.terminate()
