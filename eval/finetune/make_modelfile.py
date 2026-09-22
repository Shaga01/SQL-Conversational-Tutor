"""Write an Ollama Modelfile for a Hugging Face-format Qwen2.5 model directory.

The chat template is copied from the stock Ollama qwen2.5-coder model so that the base
and fine-tuned models are served with exactly the same template.
"""

import subprocess
import sys
from pathlib import Path

weights, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
template = subprocess.run(["ollama", "show", "qwen2.5-coder:7b", "--template"],
                          check=True, capture_output=True, text=True).stdout
out.write_text(f'FROM {weights}\nTEMPLATE """{template}"""\nPARAMETER stop "<|im_end|>"\nPARAMETER stop "<|endoftext|>"\n')
print(f"wrote {out}")
