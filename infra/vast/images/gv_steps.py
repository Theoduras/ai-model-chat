# Prints generate_video's Dockerfile RUN steps as one shell script. Only those
# before its first COPY: later ones need the copied files, which the caller
# puts in place itself.
import re
import sys

# --no-weights leaves out the Hugging Face model downloads, which the image
# adds as layers of their own.
weights = '--no-weights' not in sys.argv

text = re.sub(r'\\\n', ' ', open('/src/Dockerfile').read())
print('set -e; cd /')
for line in text.splitlines():
    if line.startswith('COPY '):
        break
    if line.startswith('RUN ') and (weights or not line.startswith('RUN wget -q https://huggingface.co/Comfy-Org/')):
        print(line[4:])
