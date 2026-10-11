from services.model_recommendations import (
    AGENT_RECOMMENDATIONS, TASK_RECOMMENDATIONS, resolve_available_model,
)
from services.anthropic_client import AnthropicClientWrapper
from services.deepseek_client import DeepSeekClientWrapper
from services.gemini_client import GeminiClientWrapper
from services.kimi_client import KimiClientWrapper
from services.openai_client import OpenAIClientWrapper


KNOWN = {
    "openai": OpenAIClientWrapper.KNOWN_MODELS,
    "deepseek": DeepSeekClientWrapper.KNOWN_MODELS,
    "kimi": KimiClientWrapper.KNOWN_MODELS,
    "gemini": GeminiClientWrapper.KNOWN_MODELS,
    "anthropic": AnthropicClientWrapper.KNOWN_MODELS,
}


def test_every_cloud_recommendation_exists_in_its_provider_catalog():
    recommendations = list(AGENT_RECOMMENDATIONS.values()) + list(
        TASK_RECOMMENDATIONS.values()
    )
    for recommendation in recommendations:
        if recommendation.provider != "ollama":
            assert recommendation.model in KNOWN[recommendation.provider]


def test_no_text_agent_recommends_an_image_only_model():
    recommendations = list(AGENT_RECOMMENDATIONS.values()) + list(
        TASK_RECOMMENDATIONS.values()
    )
    assert all("image" not in rec.model.lower() for rec in recommendations)


def test_unavailable_model_falls_back_to_provider_catalog():
    assert resolve_available_model("retired-model", ["current-model"]) == "current-model"


def test_speed_first_is_not_routed_like_balanced():
    from services.model_recommendations import RATING_MARGIN
    assert "speed" not in RATING_MARGIN
