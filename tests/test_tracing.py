async def test_run_with_two_steps_roundtrip(tracer):
    rid = await tracer.start_run("hello", "fake")
    await tracer.add_step(rid, 0, "llm_call", output={"a": 1}, input_tokens=4, output_tokens=2)
    await tracer.add_step(rid, 1, "tool_call", tool_name="t", input={"x": 1}, duration_ms=7)
    await tracer.finish_run(rid, "completed", "bye", 4, 2)
    run = await tracer.get_run(rid)
    assert run.status == "completed" and run.final_answer == "bye" and run.finished_at
    assert [s.idx for s in run.steps] == [0, 1]
    assert run.steps[1].tool_name == "t" and run.steps[1].input == {"x": 1}
    assert (await tracer.list_runs())[0].id == rid
