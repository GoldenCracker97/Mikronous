"""`mik model` — fit a local GGUF model to this machine's hardware.

detect    -> hardware profile (GPU vendor/VRAM, RAM, CPU threads)
recommend -> best preset for the hardware, optionally applied
use       -> any GGUF (local path or hf:owner/repo[:file]) with a fit verdict, optionally applied
tune      -> change ctx / KV cache / GPU layers / threads / backend and restart
status    -> what is configured and running now
bench     -> prompt-processing and generation speed on the running server
"""
