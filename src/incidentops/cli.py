"""CLI: incidentops run|approve|resume|status|trace|eval|scenarios"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from .app import IncidentOps
from .config import Settings
from .llm.base import get_llm
from .tools.mock import list_scenarios
from .trace import configure_logging

app = typer.Typer(help="IncidentOps: multi-agent incident triage", no_args_is_help=True)
LLMOpt = Annotated[str, typer.Option("--llm", help="fake | anthropic | openai | gemini")]


def _ops(llm: str, crash_after: int | None = None, hard_kill: bool = False) -> IncidentOps:
    configure_logging()
    return IncidentOps(Settings(), get_llm(llm), crash_after=crash_after, hard_kill=hard_kill)


def _show(r: dict[str, Any]) -> None:
    typer.echo(f"\nincident  {r['incident_id']}\nstatus    {r['status']}\nalert     {r['alert']}")
    typer.echo(
        f"root cause {r['root_cause']}   steps={r['steps']} tool_calls={r['tool_calls']} "
        f"tokens={r['tokens']} revisions={r['revisions']}"
    )
    if r["plan"]:
        for a in r["plan"]["actions"]:
            typer.echo(
                f"  - {a['action']} [{a['risk']} risk, scope={a['scope']}] rollback: {a['rollback']}"
            )
    if r["errors"]:
        typer.echo(f"errors    {len(r['errors'])} (see `incidentops trace {r['incident_id']}`)")
    if r["escalation_reason"]:
        typer.echo(f"escalated: {r['escalation_reason']}")
    if r["status"] == "awaiting_approval":
        typer.echo(
            f"\nNext: incidentops approve {r['incident_id']}   (or --reject / --edit plan.json)"
        )


@app.command()
def run(
    scenario: Annotated[int, typer.Option(help="scenario id 1-10")] = 1,
    live: Annotated[bool, typer.Option(help="execute in sandbox (default is dry-run)")] = False,
    auto_approve: Annotated[
        bool, typer.Option(help="skip the human gate (demo/eval only)")
    ] = False,
    llm: LLMOpt = "fake",
    incident_id: str | None = None,
    crash_after: Annotated[int | None, typer.Option(hidden=True)] = None,
    hard_kill: Annotated[bool, typer.Option(hidden=True)] = False,
) -> None:
    """Start an incident from a scenario alert."""
    ops = _ops(llm, crash_after, hard_kill)
    _show(ops.start(scenario, dry_run=not live, auto_approve=auto_approve, incident_id=incident_id))


@app.command()
def approve(
    incident_id: str,
    reject: bool = False,
    edit: Annotated[Path | None, typer.Option(help="JSON file with an edited plan")] = None,
    llm: LLMOpt = "fake",
) -> None:
    """Approve / reject / edit the pending plan, then continue the workflow."""
    ops = _ops(llm)
    pending = ops.pending_approval(incident_id)
    if pending is None:
        raise typer.BadParameter("incident is not awaiting approval")
    if edit:
        r = ops.decide(incident_id, "edit", plan=json.loads(edit.read_text()))
    else:
        r = ops.decide(incident_id, "reject" if reject else "approve")
    _show(r)


@app.command()
def resume(incident_id: str, auto_approve: bool = False, llm: LLMOpt = "fake") -> None:
    """Continue from the last checkpoint (works after a crash or process restart)."""
    _show(_ops(llm).resume(incident_id, auto_approve=auto_approve))


@app.command()
def status(incident_id: str, llm: LLMOpt = "fake") -> None:
    _show(_ops(llm).status(incident_id))


@app.command()
def trace(incident_id: str) -> None:
    """Replay the JSONL trace (thought / action / observation / route / plan / critic...)."""
    for e in _ops("fake").deps.trace.read(incident_id):
        body = {k: v for k, v in e.items() if k not in ("ts", "incident_id", "node", "kind")}
        typer.echo(f"{e['node']:>18} {e['kind']:<12} {json.dumps(body, default=str)[:220]}")


@app.command()
def scenarios() -> None:
    for s in list_scenarios(Settings()):
        typer.echo(f"{s.id:>2}  {s.service:<18} {s.alert}")


@app.command(name="eval")
def eval_cmd(
    out: Annotated[Path, typer.Option(help="markdown output")] = Path("evals/RESULTS.md"),
    llm: LLMOpt = "fake",
) -> None:
    """Run all scenarios + critic red-team + crash recovery and write a results table."""
    from .evals.harness import run_eval, to_markdown

    report = run_eval(get_llm(llm))
    md = to_markdown(report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    typer.echo(md)


if __name__ == "__main__":
    app()
