"""Streamlit UI for RepoImpact.

Presentation layer only: all repository intelligence comes from the same core
engine used by the MCP server.
"""

from __future__ import annotations

import streamlit as st

from repoimpact.impact import analyze_impact_by_name
from repoimpact.llm import GeminiProvider, load_default_provider
from repoimpact.mcp_server import build_explanation
from repoimpact.repository import InvalidRepositoryError, open_repository
from repoimpact.search import get_source_context, search_code


st.set_page_config(
    page_title="RepoImpact",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Minimal visual polish. No custom theme or heavy frontend dependencies.
st.markdown(
    """
    <style>
    .block-container { max-width: 1200px; padding-top: 2rem; }
    .repo-subtitle { color: #6b7280; margin-top: -0.8rem; margin-bottom: 1.5rem; }
    .impact-high {
        padding: 0.7rem 1rem;
        border: 1px solid #dc2626;
        border-radius: 0.5rem;
        font-weight: 700;
    }
    .impact-medium {
        padding: 0.7rem 1rem;
        border: 1px solid #d97706;
        border-radius: 0.5rem;
        font-weight: 700;
    }
    .impact-low {
        padding: 0.7rem 1rem;
        border: 1px solid #16a34a;
        border-radius: 0.5rem;
        font-weight: 700;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

if "session" not in st.session_state:
    st.session_state.session = None
if "source_label" not in st.session_state:
    st.session_state.source_label = None
if "llm" not in st.session_state:
    st.session_state.llm = load_default_provider()
if "gemini_enabled" not in st.session_state:
    st.session_state.gemini_enabled = st.session_state.llm is not None


# --------------------------------------------------------------------------
# Sidebar: AI configuration
# --------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## RepoImpact")
    st.caption("Repository intelligence")

    st.divider()
    st.markdown("### AI Explanation")
    st.caption("Optional. Used only to turn RepoImpact's analysis into natural-language answers.")

    api_key = st.text_input(
        "Gemini API key",
        type="password",
        placeholder="Paste your Gemini API key",
        help="The key is kept only in this Streamlit session and is never written to the repository.",
        key="gemini_api_key_input",
    )

    if st.button("Enable AI", use_container_width=True):
        if api_key.strip():
            try:
                st.session_state.llm = GeminiProvider(api_key=api_key.strip())
                st.session_state.gemini_enabled = True
                st.success("AI explanations enabled.")
            except Exception as exc:
                st.session_state.llm = None
                st.session_state.gemini_enabled = False
                st.error(f"Could not initialize Gemini: {exc}")
        else:
            st.session_state.llm = load_default_provider()
            st.session_state.gemini_enabled = st.session_state.llm is not None
            if st.session_state.gemini_enabled:
                st.info("Using GEMINI_API_KEY from the environment.")
            else:
                st.warning("Enter a Gemini API key to enable AI explanations.")

    if st.session_state.gemini_enabled:
        st.caption("● AI explanations enabled")
    else:
        st.caption("○ Evidence-only mode")

    st.divider()
    st.caption("RepoImpact analyzes code locally. Gemini explains the resulting evidence; it does not determine dependencies.")


# --------------------------------------------------------------------------
# Header + repository selection
# --------------------------------------------------------------------------

st.title("RepoImpact")
st.markdown(
    '<div class="repo-subtitle">Understand a repository, find code, trace dependencies, and see what could break before you change it.</div>',
    unsafe_allow_html=True,
)

with st.form("open_repo"):
    source = st.text_input(
        "Repository",
        placeholder="https://github.com/owner/repo or C:\\path\\to\\repo",
        label_visibility="collapsed",
    )
    submitted = st.form_submit_button("Analyze Repository", type="primary", use_container_width=True)

if submitted and source.strip():
    with st.spinner("Cloning and indexing repository..."):
        try:
            if st.session_state.session is not None:
                st.session_state.session.close()
            st.session_state.session = open_repository(source.strip())
            st.session_state.source_label = source.strip()
        except InvalidRepositoryError as exc:
            st.error(str(exc))

session = st.session_state.session

if session is None:
    st.info("Enter a GitHub repository URL or local path above to get started.")
    st.stop()


# --------------------------------------------------------------------------
# Repository summary
# --------------------------------------------------------------------------

file_paths = {f["id"]: f["path"] for f in session.storage.list_files()}
symbols = session.storage.list_symbols()

stats = {
    "Files": len(file_paths),
    "Functions": sum(1 for s in symbols if s["type"] == "function"),
    "Classes": sum(1 for s in symbols if s["type"] == "class"),
    "Tests": sum(1 for s in symbols if s["is_test"]),
    "APIs": sum(1 for s in symbols if s["entry_point_method"] is not None),
}

st.markdown(f"**Repository:** `{st.session_state.source_label}`")
cols = st.columns(len(stats))
for col, (label, value) in zip(cols, stats.items()):
    col.metric(label, value)


# --------------------------------------------------------------------------
# Main navigation
# --------------------------------------------------------------------------

overview_tab, ask_tab, search_tab, impact_tab = st.tabs(
    ["Overview", "Ask", "Search", "Impact"]
)


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------

with overview_tab:
    st.markdown("### Understand the repository")
    st.caption(
        "A quick structural summary of what RepoImpact found during indexing."
    )

    st.markdown("#### Major modules")
    module_counts: dict[str, int] = {}
    for symbol in symbols:
        path = file_paths[symbol["file_id"]]
        top_level = path.split("/", 1)[0]
        module_counts[top_level] = module_counts.get(top_level, 0) + 1

    top_modules = sorted(
        module_counts.items(), key=lambda kv: kv[1], reverse=True
    )[:10]

    if top_modules:
        st.dataframe(
            {"Module": [m for m, _ in top_modules], "Symbols": [c for _, c in top_modules]},
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No modules detected.")

    entry_point_rows = [s for s in symbols if s["entry_point_method"] is not None]
    st.markdown("#### API entry points")
    if entry_point_rows:
        st.dataframe(
            {
                "Method": [s["entry_point_method"] for s in entry_point_rows],
                "Route": [s["entry_point_route"] for s in entry_point_rows],
                "Handler": [s["qualified_name"] for s in entry_point_rows],
            },
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No API entry points detected.")

    st.markdown("#### Top dependencies")
    edge_counts: dict[tuple[str, str], int] = {}
    for ref in session.storage.list_references():
        if ref["target_symbol_id"] is None:
            continue
        target = session.storage.get_symbol_by_id(ref["target_symbol_id"])
        source_file = file_paths[ref["file_id"]]
        target_file = file_paths[target["file_id"]]
        if source_file != target_file:
            key = (source_file, target_file)
            edge_counts[key] = edge_counts.get(key, 0) + 1

    top_edges = sorted(edge_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
    if top_edges:
        st.dataframe(
            {
                "From": [a for (a, _), _ in top_edges],
                "To": [b for (_, b), _ in top_edges],
                "Calls": [c for _, c in top_edges],
            },
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No cross-file dependencies detected.")


# --------------------------------------------------------------------------
# Ask
# --------------------------------------------------------------------------

with ask_tab:
    st.markdown("### Ask about the repository")
    st.caption(
        "Ask a question in plain English. RepoImpact finds the relevant evidence; Gemini only explains it."
    )

    question = st.text_input(
        "Question",
        key="chat_question",
        placeholder="How does authentication work?",
    )

    if st.button("Ask", key="chat_ask", type="primary") and question.strip():
        with st.spinner("Analyzing repository..."):
            result = build_explanation(
                session.storage,
                session.graph,
                session.root,
                question.strip(),
                llm=st.session_state.llm,
            )

        if result["status"] == "insufficient_evidence":
            st.warning(result["message"])
        else:
            symbol = result["symbol"]
            impact = result["impact"]

            st.markdown(
                f"**Relevant symbol:** `{symbol['qualified_name']}`  \n"
                f"`{symbol['file']}:{symbol['start_line']}`"
            )

            st.markdown("#### Answer")
            if "answer" in result:
                st.markdown(result["answer"])
            elif "llm_error" in result:
                st.error(f"AI explanation failed ({result['llm_error']}) — showing evidence instead.")
                st.info(f"**{impact['impact_level']} impact.** {impact['reason']}")
            else:
                st.info(
                    f"Evidence-only mode — **{impact['impact_level']} impact**. "
                    "Enter a Gemini API key in the sidebar for a natural-language explanation."
                )

            with st.expander("Evidence"):
                st.write("Impact:", impact["impact_level"])
                st.write("Reason:", impact["reason"])
                st.write(
                    "Direct callers:",
                    [c["qualified_name"] for c in impact["direct_callers"]] or ["None"],
                )
                st.write(
                    "Indirect callers:",
                    [c["qualified_name"] for c in impact["indirect_callers"]] or ["None"],
                )
                st.write(
                    "Affected files:",
                    impact["affected_files"] or ["None"],
                )
                st.write(
                    "Related tests:",
                    [c["qualified_name"] for c in impact["affected_tests"]] or ["None"],
                )
                if result["workflow"]:
                    st.write("Workflow:")
                    st.json(result["workflow"])


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

with search_tab:
    st.markdown("### Find code")
    st.caption("Search for functions, classes, files, and source references.")

    query = st.text_input(
        "Search",
        key="search_query",
        placeholder="cancel_order",
    )

    if query.strip():
        results = search_code(session.storage, session.root, query.strip())

        if not results:
            st.info(f"No results for `{query}`.")
        else:
            st.markdown(f"**{len(results)} result(s)**")

            for r in results:
                label = r.qualified_name or r.name
                location = f"{r.file_path}:{r.line}" if r.line else r.file_path

                if r.match_type == "exact_symbol":
                    kind = "Symbol"
                elif r.match_type == "source_text":
                    kind = "Source match"
                else:
                    kind = "Match"

                st.markdown(f"**{kind}** · `{label}`  \n`{location}`")

        options = [(r.file_path, r.qualified_name) for r in results if r.qualified_name]

        if options:
            st.markdown("#### Inspect symbol")
            labels = [f"{fp} · {qn}" for fp, qn in options]
            choice = st.selectbox(
                "Select a symbol",
                options=labels,
                key="search_select",
            )

            file_path, qualified_name = options[labels.index(choice)]
            symbol = session.storage.get_symbol(file_path, qualified_name)

            callers = session.graph.get_callers(symbol["id"])
            callees = session.graph.get_callees(symbol["id"])
            tests = [c for c in callers if c.symbol["is_test"]]
            entry_points = session.graph.find_reaching_entry_points(symbol["id"])
            ctx = get_source_context(
                session.root,
                file_path,
                symbol["start_line"],
                symbol["end_line"],
            )

            st.markdown(f"### `{qualified_name}`")
            st.caption(
                f"{file_path}:{symbol['start_line']}-{symbol['end_line']}"
            )

            info_cols = st.columns(4)
            info_cols[0].metric("Callers", len(callers))
            info_cols[1].metric("Callees", len(callees))
            info_cols[2].metric("Tests", len(tests))
            info_cols[3].metric("API paths", len(entry_points))

            with st.expander("Source", expanded=True):
                st.code(
                    "\n".join(f"{ln:>4} | {text}" for ln, text in ctx.lines),
                    language="python",
                )

            rel_cols = st.columns(2)
            with rel_cols[0]:
                st.markdown("**Callers**")
                st.write(
                    [c.symbol["qualified_name"] for c in callers] or ["None"]
                )

                st.markdown("**Tests**")
                st.write(
                    [c.symbol["qualified_name"] for c in tests] or ["None"]
                )

            with rel_cols[1]:
                st.markdown("**Callees**")
                st.write(
                    [c.symbol["qualified_name"] for c in callees] or ["None"]
                )

                st.markdown("**API entry points**")
                st.write(
                    [
                        f"{e['entry_point_method']} {e['entry_point_route']}"
                        for e in entry_points
                    ]
                    or ["None"]
                )


# --------------------------------------------------------------------------
# Impact — flagship feature
# --------------------------------------------------------------------------

with impact_tab:
    st.markdown("### Change Impact Analysis")
    st.caption(
        "See what could be affected if you change or remove a function or class."
    )

    impact_input_cols = st.columns([4, 1])
    with impact_input_cols[0]:
        symbol_name = st.text_input(
            "Symbol",
            key="impact_symbol",
            placeholder="create_token",
            label_visibility="collapsed",
        )
    with impact_input_cols[1]:
        analyze_clicked = st.button(
            "Analyze Impact",
            key="impact_button",
            type="primary",
            use_container_width=True,
        )

    if analyze_clicked and symbol_name.strip():
        impact_results = analyze_impact_by_name(
            session.storage,
            session.graph,
            symbol_name.strip(),
        )

        if not impact_results:
            st.warning(f"No symbol named `{symbol_name.strip()}` found.")

        for result in impact_results:
            level = result.impact_level.upper()
            css_class = {
                "HIGH": "impact-high",
                "MEDIUM": "impact-medium",
                "LOW": "impact-low",
            }.get(level, "impact-medium")

            st.markdown(
                f'<div class="{css_class}">{level} IMPACT</div>',
                unsafe_allow_html=True,
            )

            target_path = file_paths[result.target["file_id"]]
            st.markdown(
                f"### `{result.target['qualified_name']}`"
            )
            st.caption(
                f"{target_path}:{result.target['start_line']}"
            )

            st.info(result.reason)

            summary_cols = st.columns(4)
            summary_cols[0].metric("Direct callers", len(result.direct_callers))
            summary_cols[1].metric("Indirect callers", len(result.indirect_callers))
            summary_cols[2].metric("Affected files", len(result.affected_files))
            summary_cols[3].metric("Tests", len(result.affected_tests))

            st.markdown("#### Dependency impact")

            dep_cols = st.columns(2)
            with dep_cols[0]:
                st.markdown("**Direct callers**")
                st.write(
                    [r.symbol["qualified_name"] for r in result.direct_callers]
                    or ["None"]
                )

                st.markdown("**Affected files**")
                st.write(result.affected_files or ["None"])

            with dep_cols[1]:
                st.markdown("**Indirect callers**")
                st.write(
                    [r.symbol["qualified_name"] for r in result.indirect_callers]
                    or ["None"]
                )

                st.markdown("**Related tests**")
                st.write(
                    [r.symbol["qualified_name"] for r in result.affected_tests]
                    or ["None"]
                )

            if result.workflows:
                st.markdown("#### Affected workflows")
                st.write(result.workflows)

            if result.entry_points:
                st.markdown("#### API entry points")
                st.write(
                    [f"{e.method} {e.route}" for e in result.entry_points]
                )

            if result.possible_references:
                with st.expander(
                    "Possible references · LOW confidence · not counted toward score"
                ):
                    st.write(
                        [r.symbol["qualified_name"] for r in result.possible_references]
                    )

            st.divider()

