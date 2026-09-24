"""
Models offered through Together AI (https://api.together.ai/v1).
Edit this list to change what teachers can pick in the activity form.
"""

TOGETHER_BASE_URL = "https://api.together.ai/v1"

# (Together model ID, label shown to teachers)
TOGETHER_MODELS = [
    ("Prism-ML/Ternary-Bonsai-27B", "Bonsai 27B (free)"),
    ("openai/gpt-oss-120b", "GPT-OSS 120B (OpenAI)"),
    ("meta-models/Muse-Glimmer-30B", "Muse Glimmer 30B (Meta)"),
    ("thinkingmachines/Inkling", "Inkling (Thinking Machines)"),
    ("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Llama 3.3 70B (Meta)"),
]

DEFAULT_TOGETHER_MODEL = TOGETHER_MODELS[0][0]


def resolve_together_model(model_id):
    """Return model_id if it is in the list, otherwise the default model."""
    if model_id in {m[0] for m in TOGETHER_MODELS}:
        return model_id
    return DEFAULT_TOGETHER_MODEL
