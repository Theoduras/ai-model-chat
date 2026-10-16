# char-caption worker: one image in, an explicit plain-text caption out.
# Runs JoyCaption (uncensored LLaVA) because nothing explicit may go to Google.
import base64
import io
import os

# The weights are cached on the network volume when there is one; without it
# they go to the container disk.
if os.path.isdir('/runpod-volume'):
    os.environ.setdefault('HF_HOME', '/runpod-volume/hf')
else:
    os.environ['HF_HOME'] = '/root/hf'

import runpod  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402
from transformers import AutoProcessor, LlavaForConditionalGeneration  # noqa: E402

MODEL = 'fancyfeast/llama-joycaption-beta-one-hf-llava'
PROMPT = ('Write a plain, explicit description of this photo in two or three sentences. '
          'Name every sex toy, sexual act and exposed body part you can see, in plain words '
          '(for example dildo, vibrator, pussy, breasts, nipples, fingering, masturbating, '
          'blowjob). Say nothing about what you cannot see.')

proc = model = None


def load():
    # On the first job, not at import: a worker that fails to load still
    # answers, so the error comes back on the job instead of a silent exit.
    global proc, model
    if model is None:
        proc = AutoProcessor.from_pretrained(MODEL)
        model = LlavaForConditionalGeneration.from_pretrained(
            MODEL, torch_dtype=torch.bfloat16, device_map=0).eval()


def handler(job):
    args = job.get('input') or {}
    try:
        load()
    except Exception as e:
        return {'error': f'model did not load: {e}'[:500]}
    try:
        img = Image.open(io.BytesIO(base64.b64decode(args['image']))).convert('RGB')
    except Exception as e:
        return {'error': f'unreadable image: {e}'}
    convo = [{'role': 'system', 'content': 'You are a helpful image captioner.'},
             {'role': 'user', 'content': args.get('prompt') or PROMPT}]
    text = proc.apply_chat_template(convo, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], images=[img], return_tensors='pt').to('cuda')
    inputs['pixel_values'] = inputs['pixel_values'].to(torch.bfloat16)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=300, do_sample=False)[0]
    caption = proc.tokenizer.decode(out[inputs['input_ids'].shape[1]:], skip_special_tokens=True)
    return {'caption': caption.strip()}


runpod.serverless.start({'handler': handler})
