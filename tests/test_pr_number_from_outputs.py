"""Only a result from open_pr can identify the PR without a sign-off."""

from types import SimpleNamespace as NS

from crew.orchestration import pr_number_from_outputs


def test_pr_number_matches_open_pr_result_by_call_id():
    pull = "https://github.com/warni001/agent-crew/pull/"
    calls = NS(contents=[
        NS(type="function_call", call_id="open", name="open_pr"),
        NS(type="function_call", call_id="read", name="read_file"),
    ])
    results = NS(contents=[
        NS(type="function_result", call_id="read", result=f"See {pull}12"),
        NS(type="function_result", call_id="open", result=f"{pull}42"),
    ])
    outputs = [NS(messages=[calls, results])]

    assert pr_number_from_outputs(outputs, []) == 42
    assert pr_number_from_outputs(outputs, [{"pr_number": 43}]) == 43
    results.contents.pop()
    assert pr_number_from_outputs(outputs, []) is None
