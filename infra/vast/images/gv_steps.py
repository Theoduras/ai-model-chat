# Prints generate_video's Dockerfile RUN steps as one shell script. Only those
# before its first COPY: later ones need the copied files, which the caller
# puts in place itself.
import re

text = re.sub(r'\\\n', ' ', open('/src/Dockerfile').read())
print('set -e; cd /')
for line in text.splitlines():
    if line.startswith('COPY '):
        break
    if line.startswith('RUN '):
        print(line[4:])
