from services.chartedge_core.ai_signal import SignalEngine


def test_signal_engine_selects_openai_provider() -> None:
    engine = SignalEngine(
        {
            "provider": "openai",
            "model": "claude-sonnet-4-20250514",
            "openai_model": "gpt-5.4-mini",
            "temperature": 0.1,
            "max_tokens": 500,
        },
        {"buy_threshold": 0.6, "sell_threshold": -0.6},
    )

    assert engine.provider.name == "openai"


def test_signal_engine_selects_anthropic_provider() -> None:
    engine = SignalEngine(
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "openai_model": "gpt-5.4-mini",
            "temperature": 0.1,
            "max_tokens": 500,
        },
        {"buy_threshold": 0.6, "sell_threshold": -0.6},
    )

    assert engine.provider.name == "anthropic"
