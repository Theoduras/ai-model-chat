# Prints generate_video's Dockerfile RUN steps as one shell script.
import re

text = re.sub(r'\\\n', ' ', open('/src/Dockerfile').read())
print('set -e; cd /')
for line in text.splitlines():
    if line.startswith('RUN '):
        print(line[4:])
