"""Typer CLI: crawl | inspect | load | db-init | validate."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from jobfather_crawler import __version__
from jobfather_crawler.config import Runtime, load_runtime
from jobfather_crawler.logging_conf import get_logger, setup_logging
from jobfather_crawler.models import ExpectedSalary, RunRequest, default_run_id
from jobfather_crawler.registry import plan_urls

app = typer.Typer(
    add_completion=False,
    help="URL-driven job ingestion for the-job-father (Crawl4AI + Postgres/pgvector).",
)
console = Console()
log = get_logger(__name__)


def _runtime() -> Runtime:
    runtime = load_runtime()
    setup_logging(runtime.env.log_level)
    return runtime


def _read_urls_file(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


@app.command("version")
def version() -> None:
    """Print the crawler version."""
    console.print(f"jobfather-crawler {__version__}")


@app.command("validate")
def validate(
    urls: Path = typer.Option(None, "--urls", exists=True, dir_okay=False),
) -> None:
    """Check configs load and show how URLs route to sources / caps."""
    runtime = _runtime()
    from jobfather_crawler.sources import build_registry

    registry = build_registry(runtime.sources_raw)
    table = Table(title="Configured sources")
    for column in ("source", "label", "max_jobs", "concurrency", "cdp", "dispatcher"):
        table.add_column(column, justify="right" if column in {"max_jobs", "concurrency"} else "left")
    for key, adapter in registry.items():
        table.add_row(
            key,
            adapter.spec.label,
            str(adapter.spec.max_jobs),
            str(adapter.spec.max_concurrency),
            "yes" if adapter.needs_cdp else "no",
            adapter.spec.dispatcher,
        )
    console.print(table)
    console.print(f"global ceiling : {runtime.doc.run.max_jobs_total} jobs/run")
    console.print(f"staging dir    : {runtime.staging_dir}")
    console.print(f"cdp url        : {runtime.cdp_url or '(none)'}")

    if urls:
        sample = _read_urls_file(Path(urls))
        grouped, unroutable = plan_urls(
            sample, registry, max_jobs_total=runtime.doc.run.max_jobs_total
        )
        for key, items in grouped.items():
            console.print(f"[green]{key}[/green]: {len(items)} URL(s)")
        for url, reason in unroutable:
            console.print(f"[red]unroutable[/red] {url} - {reason}")


@app.command("crawl")
def crawl_cmd(
    input_file: Path = typer.Option(None, "--input", exists=True, dir_okay=False, help="run_request.json"),
    urls: Path = typer.Option(None, "--urls", exists=True, dir_okay=False, help="file with one URL per line"),
    state: str = typer.Option(None, "--state"),
    arrangement: str = typer.Option(None, "--arrangement", help="remote | hybrid | onsite"),
    salary_min: float = typer.Option(None, "--salary-min"),
    salary_max: float = typer.Option(None, "--salary-max"),
    currency: str = typer.Option("MYR", "--currency"),
    period: str = typer.Option("monthly", "--period"),
    run_id: str = typer.Option(None, "--run-id"),
    save_html: str = typer.Option(None, "--save-html", help="never | failures | always"),
) -> None:
    """Crawl pasted job URLs and stage them to disk."""
    runtime = _runtime()

    if input_file:
        request = RunRequest.from_file(input_file)
    else:
        if not urls:
            raise typer.BadParameter("provide --input or --urls")
        defaults = runtime.filters
        request = RunRequest(
            run_id=run_id or default_run_id(),
            state=state or defaults.state,
            work_arrangement=arrangement or defaults.work_arrangement,
            expected_salary=ExpectedSalary(
                min=salary_min, max=salary_max, currency=currency, period=period
            ),
            urls=_read_urls_file(urls),
        )

    from jobfather_crawler.crawler import crawl_run

    console.print(f"[bold]run[/bold] {request.run_id} - {len(request.urls)} URL(s)")
    report = asyncio.run(crawl_run(request, runtime, save_html=save_html))

    table = Table(title=f"run {report.run_id}")
    table.add_column("status")
    table.add_column("count", justify="right")
    for key, value in report.counts.items():
        table.add_row(key, str(value))
    console.print(table)
    console.print(f"staged to: [cyan]{report.run_dir}[/cyan]")
    if report.counts.get("failed"):
        console.print("[yellow]some URLs failed - see dead_letter.jsonl[/yellow]")


@app.command("inspect")
def inspect(
    url: str = typer.Argument(..., help="A single job posting URL to dump raw HTML for."),
) -> None:
    """Fetch one URL and save raw HTML + markdown so selectors can be calibrated."""
    runtime = _runtime()
    from jobfather_crawler.crawler import _browser_config, _load_crawl4ai, _result_markdown
    from jobfather_crawler.registry import resolve_source
    from jobfather_crawler.sources import build_registry

    registry = build_registry(runtime.sources_raw)
    key = resolve_source(url, registry)
    if key is None:
        console.print(f"[red]no configured source matches[/red] {url}")
        raise typer.Exit(code=1)
    adapter = registry[key]

    out_dir = runtime.staging_dir / "inspect"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = (url.rstrip("/").split("/")[-1] or "job")[:60].replace("?", "_")

    async def _go():
        c4a = _load_crawl4ai()
        async with c4a.AsyncWebCrawler(config=_browser_config(c4a, runtime, adapter)) as crawler:
            return await crawler.arun(url=url, config=c4a.CrawlerRunConfig())

    result = asyncio.run(_go())
    html = getattr(result, "html", "") or ""
    (out_dir / f"{slug}.html").write_text(html, encoding="utf-8")
    (out_dir / f"{slug}.md").write_text(_result_markdown(result), encoding="utf-8")
    console.print(f"[green]source[/green] {key}  success={getattr(result, 'success', None)}")
    console.print(f"saved: {out_dir / (slug + '.html')}")


@app.command("db-init")
def db_init() -> None:
    """Apply db/001_init.sql to the jobfather database."""
    runtime = _runtime()
    from jobfather_crawler.loader import apply_schema

    apply_schema(runtime.env.libpq_dsn, runtime.schema_sql)
    console.print("[green]schema applied[/green]")


@app.command("load")
def load_cmd(
    run_id: str = typer.Option(None, "--run-id", help="Run to load (default: latest)."),
    run_dir: Path = typer.Option(None, "--run-dir", exists=True, file_okay=False),
    no_embed: bool = typer.Option(False, "--no-embed", help="Skip OpenAI embeddings."),
) -> None:
    """Load a staged run into Postgres + pgvector."""
    runtime = _runtime()
    from jobfather_crawler.loader import load_run, record_failures, record_run
    from jobfather_crawler.staging import find_latest_run

    if run_dir is None:
        target_id = run_id or find_latest_run(runtime.staging_dir)
        if not target_id:
            console.print("[red]no staged runs found[/red]")
            raise typer.Exit(code=1)
        run_dir = runtime.staging_dir / "runs" / target_id
    target_id = run_id or run_dir.name

    dsn = runtime.env.libpq_dsn
    stats = load_run(
        dsn,
        run_dir,
        embed=not no_embed,
        api_key=runtime.env.openai_api_key,
        embedding_model=runtime.env.embedding_model,
    )
    failures = record_failures(dsn, target_id, run_dir)
    loaded = stats.inserted + stats.updated
    record_run(dsn, run_id=target_id, url_count=loaded, staged=loaded, failed=failures)

    console.print(
        f"[green]loaded[/green] inserted={stats.inserted} updated={stats.updated} "
        f"embedded={stats.embedded} failures={failures}"
    )
    for error in stats.errors:
        console.print(f"[yellow]{error}[/yellow]")


if __name__ == "__main__":  # pragma: no cover
    try:
        app()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)