from jarvis.orchestration.router_node import classify_intent


def test_general_intent():
    state = {"user_input": "what is the weather like today"}
    result = classify_intent(state)
    assert result["intent"] == "general"
    assert result["complexity"] == "easy"


def test_coding_intent():
    state = {"user_input": "fix this bug in my python function"}
    result = classify_intent(state)
    assert result["intent"] == "coding"
    assert result["complexity"] == "easy"


def test_complex_intent():
    state = {"user_input": "design a system architecture for this project"}
    result = classify_intent(state)
    assert result["intent"] == "complex"
    assert result["complexity"] == "medium"


def test_traffic_light_prompt_classified_complex():
    prompt = (
        "Design an AI-powered traffic light system for a busy city intersection. "
        "The system should minimize average waiting time for all vehicles, "
        "prioritize emergency vehicles when they approach the intersection, "
        "ensure pedestrian safety with dedicated crossing phases, "
        "and adapt in real-time to changing traffic patterns. "
        "Provide a detailed architecture and optimization strategy."
    )
    state = {"user_input": prompt}
    result = classify_intent(state)
    assert result["intent"] == "complex"
    assert result["complexity"] in ("medium", "difficult")


def test_long_prompt_without_keywords_classified_complex():
    prompt = (
        "I need you to think about all the things that could possibly go wrong "
        "in a very detailed scenario that spans multiple domains and requires "
        "careful step by step analysis of each component and subsystem involved "
        "in the process from start to finish including all edge cases and "
        "potential failure modes that might arise during normal operation or "
        "under exceptional circumstances that would require special handling "
        "and additional consideration for each of the many complex factors "
        "that we have not yet fully accounted for in our initial assessment "
        "of the situation at hand which is becoming increasingly complicated "
        "with every passing moment that we spend thinking about this problem."
    )
    state = {"user_input": prompt}
    result = classify_intent(state)
    assert result["complexity"] == "difficult"
    assert result["intent"] == "complex"


def test_medium_length_general_prompt():
    prompt = (
        "Can you tell me what the capital of France is "
        "and also what the capital of Germany is "
        "and also what the capital of Italy is "
        "and also what the capital of Spain is?"
    )
    state = {"user_input": prompt}
    result = classify_intent(state)
    assert result["intent"] == "general"
    assert result["complexity"] == "medium"
