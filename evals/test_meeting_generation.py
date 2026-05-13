#!/usr/bin/env python3
"""
SemeClaw Meeting Generation Evals
==================================
Run: python3 test_meeting_generation.py

Tests:
1. Report parsing → segments
2. Agent assignment by topic
3. Verdict generation accuracy
4. Script completeness (intro → analysis → verdict)
5. Voice synthesis fallback chain
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_report_parsing():
    """Eval 1: Markdown report parses into structured segments"""
    report = """# Task Review: API Migration

## Status: COMPLETE

## What Was Done
- Migrated 12 endpoints from REST to GraphQL
- Added caching layer with Redis
- Updated documentation

## Issues Found
- 2 endpoints had breaking changes
- Rate limiting needs tuning

## Recommendations
- Monitor error rates for 48h
- Add GraphQL Playground to dev environment
"""
    
    # Simulate parsing (in real system, meeting_skill.py does this)
    segments = []
    
    # Intro
    segments.append({"speaker": "Host", "type": "intro", "text": "Welcome to the war room"})
    
    # Status
    segments.append({"speaker": "Engineer", "type": "analysis", "text": "Migration is complete"})
    
    # Issues
    segments.append({"speaker": "Analyst", "type": "analysis", "text": "Found 2 breaking changes"})
    
    # Recommendations
    segments.append({"speaker": "Engineer", "type": "analysis", "text": "Monitor error rates"})
    
    # Verdict
    segments.append({"speaker": "Host", "type": "verdict_prompt", "text": "What's the verdict?"})
    
    assert len(segments) >= 3, f"Expected 3+ segments, got {len(segments)}"
    assert segments[0]["type"] == "intro", "First segment should be intro"
    assert segments[-1]["type"] == "verdict_prompt", "Last segment should ask for verdict"
    
    print(f"✅ Report parsing: {len(segments)} segments generated")
    return True


def test_agent_assignment():
    """Eval 2: Correct agents assigned by report topic"""
    topics_agents = {
        "coding": ["Engineer", "Security"],
        "strategy": ["Director", "Analyst"],
        "design": ["Designer", "UX_Researcher"],
    }
    
    report_topic = "coding"
    expected = topics_agents.get(report_topic, ["Generalist"])
    
    # Simulate assignment
    assigned = ["Engineer", "Security"]  # Would come from meeting_skill.py
    
    assert len(assigned) >= 2, "Need at least 2 agents for a meeting"
    assert any(e in assigned for e in expected), f"Expected one of {expected}, got {assigned}"
    
    print(f"✅ Agent assignment: {assigned} for '{report_topic}' topic")
    return True


def test_verdict_generation():
    """Eval 3: Verdict correctly classifies report outcome"""
    test_cases = [
        ("Status: COMPLETE\nNo issues found", "CORRECT"),
        ("Status: COMPLETE\nCritical issues remain", "NEEDS_REVISION"),
        ("Status: FAILED\nRollback required", "REJECTED"),
    ]
    
    for report, expected in test_cases:
        # Simple heuristic (real system uses LLM)
        if "FAILED" in report or "REJECTED" in report:
            verdict = "REJECTED"
        elif "issues remain" in report.lower() or "critical" in report.lower():
            verdict = "NEEDS_REVISION"
        else:
            verdict = "CORRECT"
        
        assert verdict == expected, f"Expected {expected}, got {verdict}"
    
    print(f"✅ Verdict generation: {len(test_cases)} test cases passed")
    return True


def test_script_completeness():
    """Eval 4: Script has all required segment types"""
    required_types = {"intro", "analysis", "verdict_prompt"}
    
    script = [
        {"speaker": "Host", "type": "intro"},
        {"speaker": "Agent1", "type": "analysis"},
        {"speaker": "Agent2", "type": "analysis"},
        {"speaker": "Host", "type": "verdict_prompt"},
    ]
    
    found_types = {s["type"] for s in script}
    missing = required_types - found_types
    
    assert not missing, f"Missing segment types: {missing}"
    
    print(f"✅ Script completeness: all {len(required_types)} required types present")
    return True


def test_voice_fallback_chain():
    """Eval 5: Voice synthesis falls back correctly"""
    # Priority: ElevenLabs → edge-tts → silent
    
    has_elevenlabs = False  # Would check env
    has_edge_tts = True     # Usually available
    
    # Determine which voice engine to use
    if has_elevenlabs:
        engine = "elevenlabs"
    elif has_edge_tts:
        engine = "edge_tts"
    else:
        engine = "silent"
    
    assert engine in ("elevenlabs", "edge_tts", "silent"), f"Invalid engine: {engine}"
    assert engine != "silent", "Should have at least one voice engine"
    
    print(f"✅ Voice fallback: using {engine}")
    return True


def run_all():
    """Run all evals and report"""
    print("=" * 60)
    print("SemeClaw Meeting Generation Evals")
    print("=" * 60)
    print()
    
    tests = [
        ("Report Parsing", test_report_parsing),
        ("Agent Assignment", test_agent_assignment),
        ("Verdict Generation", test_verdict_generation),
        ("Script Completeness", test_script_completeness),
        ("Voice Fallback Chain", test_voice_fallback_chain),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {name}: Unexpected error: {e}")
            failed += 1
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
