from pathlib import Path
from typing import List, Final, Dict

OPENAI_MODELS: Final[List[str]] = [
    "gpt4",
    "gpt4-legacy",
    "gpt4-0125",
    "gpt3-0125",
    "gpt4-turbo",
    "gpt4o",
    "gpt-4o-mini",
    "gpt4omini",
    "o1",
    "o1-mini",
]

ANTHROPIC_MODELS: Final[List[str]] = [
    "claude-2",
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
    "claude-3-5-sonnet",
]

MODEL_NAME_TO_ENVAR_NAME: Final[Dict[str, str]] = (
        {model: "OPENAI_API_KEY" for model in OPENAI_MODELS} |
        {model: "ANTHROPIC_API_KEY" for model in ANTHROPIC_MODELS}
)

SUPPORTED_MINER_MODELS: Final[List[str]] = OPENAI_MODELS + ANTHROPIC_MODELS

# TODO: Add support for other models on validator
SUPPORTED_VALIDATOR_MODELS: Final[List[str]] = OPENAI_MODELS

SENTINEL_FLOAT_FAILURE_VALUE: Final[float] = -1.
SENTINEL_INT_FAILURE_VALUE: Final[int] = -1
SENTINEL_STRING_FAILURE_VALUE: Final[str] = "N/A"

SAMPLE_GENERATED_PROBLEMS_FILE: Final[Path] = Path("tests/sample_generated_problems.json")

PRICING_DATA_PER_MILLION_TOKENS: Final[Dict[str, Dict[str, float]]] = {
    "gpt-4o": {
        "input": 2.50,
        "output": 10.00,
    },
    "gpt-4o-2024-11-20": {
        "input": 2.50,
        "output": 10.00,
    },
    "gpt-4o-2024-08-06": {
        "input": 2.50,
        "output": 10.00,
    },
    "gpt-4o-audio-preview": {
        "text": {
            "input": 2.50,
            "output": 10.00,
        },
        "audio": {
            "input": 100.00,
            "output": 200.00,
        }
    },
    "gpt-4o-audio-preview-2024-10-01": {
        "text": {
            "input": 2.50,
            "output": 10.00,
        },
        "audio": {
            "input": 100.00,
            "output": 200.00,
        }
    },
    "gpt-4o-2024-05-13": {
        "input": 5.00,
        "output": 15.00,
    },
    "gpt-4o-mini": {
        "input": 0.150,
        "output": 0.600,
    },
    "gpt4omini": {
        "input": 0.150,
        "output": 0.600,
    },
    "gpt-4o-mini-2024-07-18": {
        "input": 0.150,
        "output": 0.600,
    },
    "o1-preview": {
        "input": 15.00,
        "output": 60.00,
    },
    "o1-preview-2024-09-12": {
        "input": 15.00,
        "output": 60.00,
    },
    "o1-mini": {
        "input": 3.00,
        "output": 12.00,
    },
    "o1-mini-2024-09-12": {
        "input": 3.00,
        "output": 12.00,
    },
    "claude-3.5-sonnet": {
        "input": 3.00,
        "output": 15.00,
    },
    "claude-3.5-haiku": {
        "input": 1.00,
        "output": 5.00,
    },
    "claude-3-opus": {
        "input": 15.00,
        "output": 75.00
    },
    "claude-3-haiku": {
        "input": 0.25,
        "output": 1.25,
    },
    "claude-3-sonnet": {
        "input": 3.00,
        "output": 15.00,
    }
}

BASE_DASHBOARD_URL: Final[str] = "https://agentao-dashboard.vercel.app"

EXAMPLE_PATCH = """
diff --git a/tests/test_matrix.py b/tests/test_matrix.py
index c8019b47..9416d2e3 100644
--- a/tests/test_matrix.py
+++ b/tests/test_matrix.py
@@ -61,8 +61,8 @@ class TestHeatmap:
             def __init__(self, data):
                 self.data = data

-            def __array__(self, **kwargs):
-                return np.asarray(self.data, **kwargs)
+            def __array__(self, dtype=None, copy=None):
+                return np.asarray(self.data, dtype=dtype, copy=copy)

         p = mat._HeatMapper(ArrayLike(self.x_norm), **self.default_kws)
         npt.assert_array_equal(p.plot_data, self.x_norm)

"""