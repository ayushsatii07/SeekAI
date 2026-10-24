"""
SeekAI CLI commands.

Commands:
    seekai search "query"        — web search, shows results + answer
    seekai ask "query"           — AI-focused answer with citations
    seekai history               — show search history
    seekai show <id>             — show saved answer for a search
    seekai local-search "query"  — search indexed content via FTS5
    seekai stats                 — show usage statistics

Uses Typer for parsing and Rich for beautiful terminal output.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Optional

import typer
from rich import print as rprint
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from app.config import settings
from app.models.answer import AnswerResponse
from app.models.search import SearchFreshness, SearchMode, SearchRequest
from app.utils.logging_config import setup_logging
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(legacy_windows=False)
logger = logging.getLogger(__name__)


# ─── Banner ──────────────────────────────────────────────────────────────────

def _banner() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]🔎 SEEKAI[/bold cyan]\n[dim]AI-Powered Search Engine[/dim]",
            border_style="cyan",
            padding=(0, 4),
        )
    )


# ─── Factory helpers ─────────────────────────────────────────────────────────

def _make_manager():
    """Build a SearchManager with all dependencies."""
    from app.ai.client import AIClient
    from app.database.repository import SearchRepository
    from app.search.search_manager import SearchManager
    from app.search.tavily_provider import TavilySearchProvider

    ai = AIClient()
    provider = TavilySearchProvider()
    repo = SearchRepository()
    return SearchManager(
        search_provider=provider,
        ai_client=ai,
        repository=repo,
    )


# ─── Result rendering ────────────────────────────────────────────────────────

def _render_response(response: AnswerResponse, verbose: bool = False) -> None:
    """Render the AnswerResponse to the terminal."""
    meta = response.metadata

    if verbose:
        # Pipeline summary
        console.print(Rule("[bold]SEEKAI SEARCH PIPELINE[/bold]", style="cyan"))
        console.print(f"[bold]Original Query:[/bold]  {response.query}")
        console.print(f"[bold]Queries Generated:[/bold] {meta.queries_generated}")
        console.print(f"[bold]Raw Results:[/bold]     {meta.results_retrieved}")
        console.print(f"[bold]Unique Results:[/bold]  {meta.results_after_deduplication}")
        console.print(f"[bold]Sources Used:[/bold]    {meta.sources_used}")
        console.print(f"[bold]Mode:[/bold]            {meta.mode}")
        console.print()

    # Answer section
    if response.answer:
        console.print(Rule("[bold green]ANSWER[/bold green]", style="green"))
        console.print()
        console.print(response.answer)
        console.print()
    elif response.search_id:
        from app.database.repository import SearchRepository
        repo = SearchRepository()
        ranked = repo.get_results_by_search_id(response.search_id)
        if ranked:
            console.print(Rule("[bold blue]SEARCH RESULTS[/bold blue]", style="blue"))
            console.print()
            for i, r in enumerate(ranked[:10], start=1):
                console.print(
                    f"[bold cyan][{i}][/bold cyan] [bold]{r['title']}[/bold] [dim](Score: {r['final_score']:.2f})[/dim]"
                )
                console.print(f"    [link={r['url']}][dim]{r['url']}[/dim][/link]")
                if r.get("snippet"):
                    console.print(f"    {r['snippet'][:200]}")
                console.print()

    # Sources section
    if response.sources:
        console.print(Rule("[bold blue]SOURCES[/bold blue]", style="blue"))
        console.print()
        for src in response.sources:
            console.print(
                f"[bold cyan]\\[{src.citation_number}][/bold cyan] [bold]{src.title}[/bold]"
            )
            console.print(f"    [link={src.url}][dim]{src.url}[/dim][/link]")
            console.print()

    # Stats footer
    console.print(Rule(style="dim"))
    stats = []
    if meta.results_retrieved:
        stats.append(f"[green]✓[/green] {meta.results_retrieved} results retrieved")
    if meta.results_after_deduplication:
        stats.append(f"[green]✓[/green] {meta.results_after_deduplication} unique")
    if meta.sources_used:
        stats.append(f"[green]✓[/green] {meta.sources_used} sources analyzed")
    stats.append(f"[green]✓[/green] {meta.response_time:.2f}s")
    if response.search_id:
        stats.append(f"[dim]Search ID: {response.search_id}[/dim]")

    console.print("  " + "   ".join(stats))
    console.print()


# ─── Async runner ────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine from a sync CLI command."""
    return asyncio.run(coro)


# ─── CLI App ─────────────────────────────────────────────────────────────────

app = typer.Typer(
    name="seekai",
    help="🔎 SeekAI — AI-Powered Custom Search Engine",
    add_completion=False,
    rich_markup_mode="rich",
)


@app.command("search")
def cmd_search(
    query: str = typer.Argument(..., help="Search query"),
    max_results: int = typer.Option(10, "--max-results", "-n", help="Max results to retrieve"),
    mode: SearchMode = typer.Option(SearchMode.web, "--mode", "-m", help="Search mode: web | local | hybrid | academic"),
    domains: Optional[list[str]] = typer.Option(None, "--domains", "-d", help="Restrict to domains"),
    freshness: Optional[SearchFreshness] = typer.Option(None, "--freshness", "-f", help="Freshness filter"),
    no_ai: bool = typer.Option(False, "--no-ai", help="Skip AI answer generation"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show pipeline details"),
) -> None:
    """
    Search the web and get a grounded AI answer.

    Examples:
        seekai search "What is machine learning?"
        seekai search "latest AI news" --freshness week
        seekai search "NIST cybersecurity" --domains nist.gov
        seekai search "Python decorators" --no-ai
    """
    _banner()
    settings.validate_required_for_search()
    setup_logging(settings.log_level)

    request = SearchRequest(
        query=query,
        max_results=max_results,
        mode=mode,
        domains=domains,
        freshness=freshness,
        use_ai=not no_ai,
    )

    manager = _make_manager()

    steps = [
        ("🔍 Understanding query...", None),
        ("🔎 Searching the web...", None),
        ("🧹 Processing results...", None),
        ("📊 Ranking sources...", None),
    ]
    if not no_ai:
        steps.append(("🤖 Generating grounded answer...", None))

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Working…", total=None)

            async def _run_search():
                for desc, _ in steps:
                    progress.update(task, description=desc)
                    await asyncio.sleep(0)  # yield
                return await manager.search(request)

            response = _run(_run_search())

        _render_response(response, verbose=verbose)
    except typer.Exit:
        raise
    except Exception as exc:
        err_msg = str(exc)
        if "insufficient_quota" in err_msg or "credit_balance_exhausted" in err_msg or "RateLimitError" in type(exc).__name__:
            console.print("\n[bold red]OpenAI API Error:[/bold red] You have no credits remaining on your OpenAI account.")
            console.print("  → Add credits or check your billing at: [link=https://platform.openai.com/settings/organization/billing/]https://platform.openai.com/settings/organization/billing/[/link]")
            console.print("  → Tip: You can run searches using [bold cyan]--no-ai[/bold cyan] to retrieve, rank, and index web results!\n")
        elif "AuthenticationError" in type(exc).__name__:
            console.print("\n[bold red]OpenAI API Error:[/bold red] Invalid API key.")
            console.print("  → Verify your key in [bold].env[/bold] or generate a new key at: https://platform.openai.com/api-keys\n")
        else:
            console.print(f"\n[bold red]Search Error:[/bold red] {err_msg}\n")
        raise typer.Exit(1)


@app.command("ask")
def cmd_ask(
    query: str = typer.Argument(..., help="Question to answer"),
    max_results: int = typer.Option(10, "--max-results", "-n"),
    mode: SearchMode = typer.Option(SearchMode.web, "--mode", "-m"),
    domains: Optional[list[str]] = typer.Option(None, "--domains", "-d"),
    freshness: Optional[SearchFreshness] = typer.Option(None, "--freshness", "-f"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    Ask a question and get a grounded AI answer with citations.

    Examples:
        seekai ask "What are the advantages of quantum computing?"
        seekai ask "Best cybersecurity certifications in India?"
    """
    cmd_search(
        query=query,
        max_results=max_results,
        mode=mode,
        domains=domains,
        freshness=freshness,
        no_ai=False,
        verbose=verbose,
    )


@app.command("history")
def cmd_history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent searches to show"),
) -> None:
    """Show recent search history."""
    _banner()
    from app.database.repository import SearchRepository
    repo = SearchRepository()
    rows = repo.get_history(limit=limit)

    if not rows:
        console.print("[yellow]No search history found.[/yellow]")
        raise typer.Exit()

    table = Table(title=f"Recent Searches (last {limit})", show_lines=True)
    table.add_column("ID", style="cyan", width=4)
    table.add_column("Query", style="white", width=26)
    table.add_column("Intent", style="yellow", width=12)
    table.add_column("Mode", style="green", width=6)
    table.add_column("Results", style="magenta", width=7, justify="right")
    table.add_column("Time", style="dim", width=7, justify="right")
    table.add_column("Timestamp", style="dim", width=16)

    for r in rows:
        ts = r["timestamp"].replace("T", " ")[:16]
        table.add_row(
            str(r["id"]),
            r["query"][:24],
            r.get("intent", "") or "—",
            r.get("mode", "web"),
            str(r["result_count"]),
            f"{r['response_time']:.2f}s",
            ts,
        )

    console.print(table)


@app.command("show")
def cmd_show(
    search_id: int = typer.Argument(..., help="Search ID to display"),
) -> None:
    """Show the saved answer and sources for a previous search."""
    _banner()
    from app.database.repository import SearchRepository
    repo = SearchRepository()

    search = repo.get_search_by_id(search_id)
    if not search:
        console.print(f"[red]Search ID {search_id} not found.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Query:[/bold] {search['query']}")
    console.print(f"[bold]Timestamp:[/bold] {search['timestamp']}")
    console.print(f"[bold]Results:[/bold] {search['result_count']}")
    console.print()

    answer = repo.get_answer_by_search_id(search_id)
    if answer:
        console.print(Rule("[bold green]ANSWER[/bold green]", style="green"))
        console.print()
        console.print(answer["answer"])
        console.print()

        sources = repo.get_sources_by_search_id(search_id)
        if sources:
            console.print(Rule("[bold blue]SOURCES[/bold blue]", style="blue"))
            console.print()
            for src in sources:
                console.print(
                    f"[bold cyan]\\[{src['citation_number']}][/bold cyan] [bold]{src['title']}[/bold]"
                )
                console.print(f"    [dim]{src['url']}[/dim]")
                console.print()
    else:
        console.print("[yellow]No AI answer stored for this search.[/yellow]")


@app.command("local-search")
def cmd_local_search(
    query: str = typer.Argument(..., help="Search term"),
    limit: int = typer.Option(10, "--limit", "-n"),
) -> None:
    """Search locally indexed content using SQLite FTS5."""
    _banner()
    from app.database.repository import SearchRepository
    repo = SearchRepository()

    console.print(f"[bold]Searching local index for:[/bold] {query}")
    console.print()

    results = repo.local_search(query, limit=limit)
    if not results:
        console.print("[yellow]No local results found. Run some searches first to build the index.[/yellow]")
        raise typer.Exit()

    for i, r in enumerate(results, 1):
        console.print(f"[bold cyan][{i}][/bold cyan] [bold]{r.get('title', 'Untitled')}[/bold]")
        console.print(f"    [dim]{r.get('url', '')}[/dim]")
        excerpt = r.get("excerpt", "")
        if excerpt:
            # Clean HTML tags from FTS snippet
            import re
            excerpt = re.sub(r"<[^>]+>", "", excerpt)
            console.print(f"    {excerpt[:200]}")
        console.print()


@app.command("vector-search")
def cmd_vector_search(
    query: str = typer.Argument(..., help="Search term for dense vector search"),
    limit: int = typer.Option(10, "--limit", "-n"),
) -> None:
    """Search locally indexed documents using ChromaDB dense embeddings."""
    _banner()
    from app.search.vector.store import ChromaVectorStore
    store = ChromaVectorStore()
    total = store.count()
    if total == 0:
        console.print("[yellow]No vector embeddings stored yet. Run searches to populate the vector store.[/yellow]")
        raise typer.Exit()

    console.print(f"[bold]Searching semantic vector store ({total} indexed documents) for:[/bold] {query}")
    console.print()

    hits = asyncio.run(store.search(query, limit=limit))
    if not hits:
        console.print("[yellow]No semantic matches found.[/yellow]")
        raise typer.Exit()

    for i, h in enumerate(hits, 1):
        meta = h.metadata
        title = meta.get("title", "") or (h.content.split("\n")[0] if h.content else "Document")
        url = meta.get("url", "")
        console.print(f"[bold cyan][{i}][/bold cyan] [bold]{title}[/bold] [dim](Cosine Score: {h.score:.4f})[/dim]")
        if url:
            console.print(f"    [dim]{url}[/dim]")
        console.print(f"    {h.content[:200]}")
        console.print()


@app.command("hybrid-search")
def cmd_hybrid_search(
    query: str = typer.Argument(..., help="Search query for Hybrid RRF search"),
    limit: int = typer.Option(10, "--limit", "-n"),
    ai: bool = typer.Option(False, "--ai", help="Generate AI synthesis over hybrid search results"),
) -> None:
    """Execute hybrid search using Reciprocal Rank Fusion (FTS5 + Vector Embeddings)."""
    _banner()
    mgr = _make_manager()
    req = SearchRequest(
        query=query,
        max_results=limit,
        mode=SearchMode.local,
        use_ai=ai,
    )

    console.print(f"[bold]Executing Hybrid RRF Search (FTS5 + Dense Vectors) for:[/bold] {query}")
    console.print()

    response = asyncio.run(mgr.search(req))
    _render_response(response, verbose=True)


@app.command("academic-search")
def cmd_academic_search(
    query: str = typer.Argument(..., help="Research query"),
    max_results: int = typer.Option(15, "--max-results", "-n", help="Max results per provider"),
    freshness: Optional[SearchFreshness] = typer.Option(None, "--freshness", "-f", help="Filter by freshness: day | week | month | year"),
    providers: Optional[list[str]] = typer.Option(
        None, "--provider", "-p",
        help="Providers to use (s2 | arxiv | crossref). Repeat for multiple. Defaults to all three.",
    ),
    ai: bool = typer.Option(True, "--ai/--no-ai", help="Generate AI synthesis over results"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    Search academic databases: Semantic Scholar, arXiv, and Crossref.

    Runs all three providers in parallel and merges results. No API keys needed.

    Examples:
        seekai academic-search "transformer attention mechanism"
        seekai academic-search "CRISPR gene editing" --freshness year
        seekai academic-search "deep learning" --provider arxiv --provider s2
        seekai academic-search "quantum computing" --no-ai -n 20
    """
    _banner()
    setup_logging(settings.log_level)

    # Optionally filter which providers to use
    from app.search.providers import ArXivProvider, CrossrefProvider, SemanticScholarProvider

    _all_providers = {
        "s2": SemanticScholarProvider,
        "arxiv": ArXivProvider,
        "crossref": CrossrefProvider,
    }
    if providers:
        unknown = [p for p in providers if p not in _all_providers]
        if unknown:
            console.print(f"[red]Unknown provider(s): {', '.join(unknown)}. Valid: s2, arxiv, crossref[/red]")
            raise typer.Exit(1)
        active_providers = [_all_providers[p]() for p in providers]
    else:
        active_providers = [cls() for cls in _all_providers.values()]

    # Build a minimal manager wired to academic providers only
    from app.ai.client import AIClient
    from app.database.repository import SearchRepository
    from app.search.search_manager import SearchManager
    from app.search.tavily_provider import TavilySearchProvider

    if ai:
        if not settings.openai_api_key:
            console.print(
                "[yellow]Warning:[/yellow] OPENAI_API_KEY not set. Falling back to [bold]--no-ai[/bold] mode."
            )
            ai = False

    ai_client = AIClient() if ai else None
    try:
        provider_stub = TavilySearchProvider()  # required positional arg; not used in academic mode
    except Exception:
        from app.search.base import SearchProvider
        class _Stub(SearchProvider):  # type: ignore[misc]
            @property
            def name(self): return "stub"
            def is_available(self): return False
            async def search(self, *a, **kw): return []
        provider_stub = _Stub()

    repo = SearchRepository()

    if ai and ai_client:
        mgr = SearchManager(
            search_provider=provider_stub,
            ai_client=ai_client,
            repository=repo,
            academic_providers=active_providers,
        )
    else:
        # Build a no-AI manager by using a placeholder AI client
        from app.ai.client import AIClient
        mgr = SearchManager(
            search_provider=provider_stub,
            ai_client=AIClient(),
            repository=repo,
            academic_providers=active_providers,
        )

    req = SearchRequest(
        query=query,
        max_results=max_results,
        mode=SearchMode.academic,
        freshness=freshness,
        use_ai=ai,
    )

    provider_labels = ", ".join(p.name for p in active_providers)
    console.print(f"[bold]Academic Search via:[/bold] {provider_labels}")
    console.print(f"[bold]Query:[/bold] {query}")
    console.print()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Querying academic databases...", total=None)
            response = asyncio.run(mgr.search(req))
            progress.update(task, description="Done.")

        if response.answer:
            console.print(Rule("[bold green]AI SYNTHESIS[/bold green]", style="green"))
            console.print(response.answer)
            console.print()

        if response.search_id:
            repo2 = SearchRepository()
            ranked = repo2.get_results_by_search_id(response.search_id)
            hits = ranked or []
        else:
            hits = []

        if hits:
            table = Table(
                title=f"Academic Results ({len(hits)} found)",
                show_lines=True,
                expand=False,
            )
            table.add_column("#", style="cyan", width=3)
            table.add_column("Source", style="magenta", width=10)
            table.add_column("Title", style="bold white", max_width=55)
            table.add_column("Snippet", style="dim", max_width=55)
            table.add_column("URL", style="blue", max_width=40)

            for i, r in enumerate(hits[:max_results], start=1):
                src_type = r.get("source_type", "web")
                src_label = {
                    "academic:s2": "S2",
                    "academic:arxiv": "arXiv",
                    "academic:crossref": "Crossref",
                }.get(src_type, src_type)
                table.add_row(
                    str(i),
                    src_label,
                    (r.get("title") or "")[:55],
                    (r.get("snippet") or "")[:120],
                    (r.get("url") or "")[:40],
                )
            console.print(table)
        else:
            console.print("[yellow]No academic results returned.[/yellow]")

        _render_response(response, verbose=verbose)

    except Exception as exc:
        console.print(f"[bold red]Academic Search Error:[/bold red] {exc}")
        raise typer.Exit(1)


@app.command("stats")
def cmd_stats() -> None:
    """Show usage statistics."""
    _banner()
    from app.database.repository import SearchRepository
    repo = SearchRepository()
    stats = repo.get_stats()

    table = Table(title="SeekAI Statistics", show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white", justify="right")

    table.add_row("Total searches", str(stats["total_searches"]))
    table.add_row("Total results indexed", str(stats["total_results"]))
    table.add_row("Total AI answers", str(stats["total_answers"]))
    table.add_row("Average response time", f"{stats['average_response_time']:.2f}s")

    console.print(table)
