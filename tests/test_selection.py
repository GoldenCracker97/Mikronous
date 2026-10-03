"""Selected-text actions: prompt building (no display needed)."""
from mikronous_tray import selection as S


def test_prompts_and_labels():
    assert [a.key for a in S.ACTIONS] == ["explain", "summarize", "rewrite", "translate", "ask"]
    p = S.build_prompt("translate", "  Hola mundo ", "German")
    assert p.startswith("Translate the following into German.") and "---\nHola mundo\n---" in p
    assert S.label(S.action("translate"), "German") == "Translate → German"
    assert S.build_prompt("ask", "x y") == '"""\nx y\n"""\n'
    long = S.build_prompt("summarize", "a" * 20000)
    assert "[… cut after 12,000 characters]" in long and len(long) < 12_300
    assert S.action("rewrite").copy_result and not S.action("explain").copy_result
