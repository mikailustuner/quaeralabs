import os

# Tests never download the embedding model; memory tests that need vectors pass a fake embedder explicitly.
os.environ["QUAERA_MEMORY_EMBED"] = "off"
# The completion cache is per machine; tests must see every call (they count calls and costs).
os.environ["QUAERA_CACHE"] = "off"
