from typing import List, Type, Dict

import sweagent.agent.models as models
from itertools import chain

ALL_MODEL_CLASSES: List[Type[models.BaseModel]] = [
    models.OpenAIModel,
    models.DeepSeekModel,
    models.GroqModel,
    models.AnthropicModel,
    models.BedrockModel,
    models.OllamaModel,
    models.CorcelModel,
]

ALL_MODEL_NAMES: List[str] = list(chain.from_iterable(
    list(set(model_class.MODELS.keys()) | set(model_class.SHORTCUTS.keys()))
    if model_class != models.CorcelModel else
        [
            f"corcel:{model_name}"
            for model_name in list(models.CorcelModel.MODELS.keys()) + list(models.CorcelModel.SHORTCUTS.keys())
        ]
    for model_class in ALL_MODEL_CLASSES
)) + ["ollama:<model name>, e.g. ollama:llama-7b"]

MODEL_CLASS_TO_ENVAR_NAMES: Dict[Type[models.BaseModel], List[str]] = {
    models.TogetherModel: ["TOGETHER_API_KEY"],
    models.OpenAIModel: [
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "OPENAI_API_BASE_URL",
        "OPENAI_API_KEY",
    ],
    models.DeepSeekModel: [
        "DEEPSEEK_API_BASE_URL",
        "DEEPSEEK_API_KEY"
    ],
    models.GroqModel: ["GROQ_API_KEY"],
    models.AnthropicModel: ["ANTHROPIC_API_KEY"],
    models.OllamaModel: [],
    models.CorcelModel: ["CORCEL_API_KEY"],
}


def get_model_from_model_name(model_name: str) -> Type[models.BaseModel]:
    if (
            model_name.startswith("gpt")
            or model_name.startswith("ft:gpt")
            or model_name.startswith("azure:gpt")
            or model_name in models.OpenAIModel.SHORTCUTS
    ):
        return models.OpenAIModel
    elif model_name.startswith("claude"):
        return models.AnthropicModel
    elif model_name.startswith("bedrock"):
        return models.BedrockModel
    elif model_name.startswith("ollama"):
        return models.OllamaModel
    elif model_name.startswith("corcel:"):
        return models.CorcelModel
    elif model_name.startswith("deepseek"):
        return models.DeepSeekModel
    elif model_name in models.TogetherModel.SHORTCUTS:
        return models.TogetherModel
    elif model_name in models.GroqModel.SHORTCUTS:
        return models.GroqModel
    else:
        msg = f"Invalid model name: {model_name}"
        raise ValueError(msg)


def get_envar_names_from_model_name(model_name: str) -> List[str]:
    model_class = get_model_from_model_name(model_name)
    return MODEL_CLASS_TO_ENVAR_NAMES[model_class]
