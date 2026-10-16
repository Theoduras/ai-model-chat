# char-caption worker: one image in, an explicit plain-text caption out.
# Runs JoyCaption (uncensored LLaVA) because nothing explicit may go to Google.
import base64
import io

import runpod
import torch
from PIL import Image
from transformers import AutoProcessor, LlavaForConditionalGeneration

MODEL = 'fancyfeast/llama-joycaption-beta-one-hf-llava'
PROMPT = ('Write a plain, explicit description of this photo in two or three sentences. '
          'Name every sex toy, sexual act and exposed body part you can see, in plain words '
          '(for example dildo, vibrator, pussy, breasts, nipples, fingering, masturbating, '
          'blowjob). Say nothing about what you cannot see.')

proc = AutoProcessor.from_pretrained(MODEL)
model = LlavaForConditionalGeneration.from_pretrained(
    MODEL, torch_dtype=torch.bfloat16, device_map=0).eval()


def handler(job):
    args = job.get('input') or {}
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
