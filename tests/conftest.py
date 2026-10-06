import os

# Tests never download the embedding model; memory tests that need vectors pass a fake embedder explicitly.
os.environ["QUAERA_MEMORY_EMBED"] = "off"
