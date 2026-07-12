from jarvis.orchestration.router_node import classify_intent


def test_general_intent():
    state = {"user_input": "what is the weather like today"}
    result = classify_intent(state)
    assert result["intent"] == "general"


def test_coding_intent():
    state = {"user_input": "fix this bug in my python function"}
    result = classify_intent(state)
    assert result["intent"] == "coding"


def test_complex_intent():
    state = {"user_input": "design a system architecture for this project"}
    result = classify_intent(state)
    assert result["intent"] == "complex"
