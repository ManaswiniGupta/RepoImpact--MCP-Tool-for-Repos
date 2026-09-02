# RepoImpact MCP — From-Scratch MVP

## 1. Project Goal

Build a small, local repository-intelligence application from scratch.

The application accepts a local Python repository or GitHub repository URL, analyzes its Python source code structurally, stores a lightweight local index in SQLite, builds a dependency/call graph, and exposes repository intelligence through MCP and a simple web interface.

The primary differentiator is:

> **Change-impact analysis: determine what code, workflows, APIs, and tests may be affected when a function/class changes.**

The system should not rely on embeddings or a vector database.

This is a portfolio MVP, not a production platform.

Target implementation time:

**2–3 days**

Target size:

**~1,000–2,500 LOC**

---

# 2. Core Architecture

Build this:

```text
                         ┌─────────────────────┐
                         │     Web UI          │
                         │                     │
                         │ GitHub URL          │
                         │ Repository selector │
                         │ Chat                │
                         │ Search              │
                         │ Impact              │
                         └──────────┬──────────┘
                                    │
                                    ▼
                           Repository Manager
                                    │
                           ┌────────┴────────┐
                           ▼                 ▼
                       Git Clone        Local Folder
                           │                 │
                           └────────┬────────┘
                                    ▼
                              Python Parser
                                    │
                   ┌────────────────┼────────────────┐
                   ▼                ▼                ▼
                Symbols          Imports           Calls
                   │                │                │
                   └────────────────┼────────────────┘
                                    ▼
                                SQLite
                                    │
                                    ▼
                              Code Graph
                                    │
                  ┌─────────────────┼─────────────────┐
                  ▼                 ▼                 ▼
                Search          Workflow           Impact
                                    │                 │
                                    └────────┬────────┘
                                             ▼
                                         MCP Server
                                             │
                                             ▼
                                           LLM
```

---

# 3. Fundamental Design Principle

Do NOT build conventional RAG.

Do NOT use:

* embeddings
* vector databases
* Chroma
* Qdrant
* Pinecone
* FAISS
* Weaviate
* LangChain
* LlamaIndex
* LangGraph
* agent frameworks

The system should use:

```text
AST
+
SQLite
+
graph traversal
+
text search
+
MCP
+
optional LLM explanation
```

The structural analysis must happen locally and deterministically.

---

# 4. Why No Embeddings?

The system is primarily answering structural questions.

Examples:

> Who calls this function?

> What breaks if I remove this function?

> What workflow reaches this function?

> Which tests depend on this feature?

These are dependency/relationship questions.

A vector embedding can tell us that two pieces of code are semantically similar.

It cannot reliably establish:

```text
A calls B
B is imported by C
C is reached by /login
test_login exercises A
```

Therefore, use explicit structural relationships.

Embeddings may be a future optional feature.

They are not part of MVP.

---

# 5. Supported Language

MVP supports:

**Python only.**

Use Python's built-in:

```python
ast
```

Do not implement Tree-sitter in MVP unless it becomes necessary.

Do not support:

* JavaScript
* TypeScript
* Java
* Go
* Rust
* C++
* PHP

Future versions can add Tree-sitter and multiple languages.

---

# 6. Technology Stack

Use:

```text
Python 3.11+
Python ast
SQLite
FastMCP / official MCP Python SDK
Streamlit
Git CLI
pytest
```

Keep dependencies minimal.

Do not introduce a framework unless it solves a real MVP problem.

---

# 7. Project Structure

Use:

```text
repoimpact/
│
├── repoimpact/
│   ├── __init__.py
│   ├── repository.py
│   ├── parser.py
│   ├── models.py
│   ├── storage.py
│   ├── graph.py
│   ├── search.py
│   ├── impact.py
│   ├── workflow.py
│   ├── mcp_server.py
│   └── app.py
│
├── tests/
│   ├── test_parser.py
│   ├── test_storage.py
│   ├── test_graph.py
│   ├── test_search.py
│   ├── test_impact.py
│   └── test_workflow.py
│
├── examples/
│   └── demo_repo/
│       ├── app.py
│       ├── routes.py
│       ├── auth.py
│       ├── users.py
│       ├── database.py
│       └── tests/
│
├── data/
│   └── .gitkeep
│
├── pyproject.toml
├── README.md
└── .gitignore
```

Keep modules small.

---

# 8. Repository Input

The UI should accept:

```text
GitHub URL
```

Example:

```text
https://github.com/user/project
```

Also support:

```text
Local repository path
```

if easy.

The URL is untrusted input. For a GitHub URL:

```text
URL
 ↓
strict validation: must match https://github.com/<owner>/<repo>(/)?
   (reject file://, ssh://, git://, and anything else)
 ↓
derive a safe repository id (hash of the normalized URL — never the raw
   owner/repo string used as a filesystem path)
 ↓
subprocess.run(["git", "clone", "--depth", "1", url, dest], shell=False)
 ↓
local repository under data/repositories/<repo-id>/source/
 ↓
index
```

Use shallow cloning initially.

Do not implement GitHub API authentication.

Do not require GitHub tokens for public repositories.

Never use `os.system(...)` or `shell=True`. Never interpolate the URL
into a shell command string — pass it as a subprocess argument list.

---

# 9. Repository Manager

Create:

```python
RepositoryManager
```

Responsibilities:

* validate repository path
* validate GitHub URL
* clone repository
* determine repository name
* provide repository root
* trigger indexing

Do not make this class responsible for parsing or analysis.

---

# 10. File Scanner

Recursively find Python files.

Include:

```text
*.py
```

Ignore:

```text
.git/
.venv/
venv/
env/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
node_modules/
dist/
build/
```

Skip binary files.

Add a configurable maximum file size.

---

# 11. Parser

Use:

```python
ast.parse(source)
```

For every Python file extract:

## Functions

Extract:

```text
name
qualified_name
file
start_line
end_line
```

Support:

```text
def
async def
```

## Classes

Extract:

```text
name
qualified_name
file
start_line
end_line
```

## Imports

Handle:

```python
import auth
```

and:

```python
from auth import login
```

## Calls

Detect:

```python
login()
```

and:

```python
auth.login()
```

where possible.

## Decorators

Record decorators.

This will later help detect API routes.

---

# 12. Data Models

Use simple dataclasses.

Example conceptual models:

```text
File
Symbol
Reference
Import
Test
```

Do not create dozens of abstractions.

---

# 13. SQLite

Use SQLite as the only persistent database.

Suggested schema:

```sql
files
-----
id
path
hash
size

symbols
-------
id
file_id
name
qualified_name
type
start_line
end_line
parent_id
is_test

references
----------
id
source_symbol_id
target_symbol_id
file_id
line
kind
resolution
confidence

imports
-------
id
file_id
module
name
line
```

Use foreign keys.

Create indexes on:

```text
symbols.name
symbols.qualified_name
symbols.is_test
files.path
references.source_symbol_id
references.target_symbol_id
references.confidence
```

Do not use a graph database.

There is no separate `tests` table. A test is a symbol with
`is_test = true` (§19) — reusing the normal call graph means
`get_callers(x)` filtered by `is_test` answers "which tests exercise x"
without a second relationship system.

`references.resolution` records how the edge was produced (e.g.
`same_module`, `self_method`, `imported_symbol`, `instance_assignment`,
`qualified_module`, `name_fallback`). `references.confidence` is one of
`HIGH`, `MEDIUM`, `LOW`, `UNRESOLVED` (§15).

---

# 14. Indexing Pipeline

A full re-index is the only indexing strategy in MVP — no incremental
upsert/deduplication logic.

```text
BEGIN TRANSACTION
    ↓
clear repository's existing index tables
    ↓
scan repository (file scanner)
    ↓
parse files (AST parser)
    ↓
normalize into models
    ↓
insert files, symbols, imports
    ↓
resolve references (§15)
    ↓
insert references
    ↓
build call graph
    ↓
COMMIT
```

If any step fails: `ROLLBACK`. The previous valid index remains intact.

This guarantees `index(repo); index(repo)` does not duplicate records —
because the second run clears the tables before writing, not because of
upsert logic.

Incremental (file-by-file) indexing is out of scope for MVP; it is a
V1.1 feature.

---

# 15. Symbol Resolution

Symbol resolution is the most correctness-sensitive part of RepoImpact,
and the highest-risk part of the MVP to overbuild. Use a **fixed
resolution order** and do not go further than this list.

For a bare call `foo()`, try in order, stopping at the first match:

```text
1. same module        — bare name defined in the current file
2. self.method()       — resolve via the enclosing class
3. imported symbol     — `from auth import create_token` / `import auth`
                          (track simple aliases: `as token`, `as auth`)
4. instance assignment — `x = ClassName()` then `x.method()` in the same
                          function/module scope → ClassName.method
5. qualified module    — `auth.create_token()` where `auth` is an
                          imported module
6. unique bare-name     — exactly one symbol in the whole index has this
   fallback               name → link it at LOW confidence
7. unresolved           — zero or multiple candidates → no edge
```

For `obj.foo()`, the same idea applies with a shorter chain:

```text
1. self.foo() / enclosing class
2. statically known object type (step 4 above)
3. imported module qualification
4. unresolved
```

### Bounds on step 4 (instance assignment)

Only track a direct, literal assignment (`x = ClassName()`) in the same
scope as the call. Do not follow the variable across function
boundaries, do not track reassignment, do not track factory functions or
dependency injection, do not do general data-flow analysis. If the
assignment isn't a direct `Name()` call, fall through to the next step.

### Bounds on step 6 (bare-name fallback)

If multiple symbols share the name, do **not** create an edge to any of
them — false-positive dependency edges are more harmful to impact
analysis than a missing edge. Report `unresolved / ambiguous` with the
candidate names if useful.

### Confidence

Every reference gets a confidence level, stored alongside how it was
resolved (`references.resolution`, `references.confidence` — §13):

```text
HIGH        same module, self.method(), imported symbol, instance assignment
LOW         unique bare-name fallback
UNRESOLVED  ambiguous or no candidate
```

The impact engine must surface this distinction (§22, §23) rather than
presenting LOW/UNRESOLVED relationships as fact.

Do not implement a complete Python compiler/type system. Do not add
cross-file type inference beyond step 4. Do not guess beyond this list.

---

# 16. Call Graph

Represent:

```text
caller → callee
```

Example:

```text
login
 ├── validate_user
 └── create_token
```

Graph operations must support:

```text
get_callers(symbol)
get_callees(symbol)
get_transitive_callers(symbol)
get_transitive_callees(symbol)
```

Traversal must have:

* cycle protection
* configurable depth
* deterministic ordering

---

# 17. Search

Implement local repository search.

No embeddings.

Search should prioritize:

```text
1. exact symbol
2. qualified symbol
3. filename
4. symbol substring
5. source text
```

Example:

```text
search_code("authentication")
```

could return:

```text
auth.py
  login()

routes.py
  login_endpoint()

tests/test_auth.py
  test_login()
```

Return concise results.

Do not return entire files.

---

# 18. Source Context

Implement:

```text
get_source_context(file, start_line, end_line)
```

Return:

* relevant source
* line numbers
* small surrounding context

Do not return the entire file to the LLM.

---

# 19. Test Detection

Detect likely tests.

Recognize files such as:

```text
test_*.py
*_test.py
```

and functions such as:

```text
test_*
```

Mark matching symbols with `is_test = true` (§13). There is no separate
test-relationship system: a test's calls are ordinary edges in the same
call graph, so `get_callers(x)` filtered by `is_test` already answers
"which tests exercise x" (§22).

Do not attempt perfect test-impact analysis.

---

# 20. API Entry Point Detection

Detect common Python web decorators.

At minimum:

```python
@app.get(...)
@app.post(...)
@app.put(...)
@app.delete(...)
@router.get(...)
@router.post(...)
```

Extract:

```text
HTTP method
route
function
```

Example:

```python
@app.post("/login")
def login_endpoint():
    return login()
```

Store:

```text
POST /login
    ↓
login_endpoint
```

Only support common patterns.

Do not build a complete FastAPI/Django/Flask analyzer.

---

# 21. Workflow Engine

Implement:

```python
trace_workflow(symbol)
```

It should trace:

```text
entry point
 ↓
function
 ↓
called functions
 ↓
dependencies
```

Example:

```text
POST /login
    ↓
login_endpoint()
    ↓
login()
    ↓
validate_user()
    ↓
database.get_user()
    ↓
create_token()
```

Limit graph traversal depth to avoid enormous outputs.

---

# 22. Impact Engine

This is the main feature.

Implement:

```python
analyze_impact(symbol)
```

Given:

```text
create_token
```

find:

```text
direct callers
indirect callers
affected files
related tests
API entry points
affected workflows
```

Confidence matters as much as the list itself: HIGH-confidence callers
(§15) are reported as direct/indirect impact; LOW-confidence and
UNRESOLVED references are reported separately and never merged silently
into the same list.

Example:

```text
TARGET

create_token()
auth.py:67

DIRECT CALLERS

login()

INDIRECT CALLERS

login_endpoint()

AFFECTED FILES

auth.py
routes.py

RELATED TESTS

test_login()

ENTRY POINTS

POST /login

POSSIBLE REFERENCES (LOW confidence, not counted toward impact score)

process() in payments.py — name matched via fallback, not import/scope
verified
```

---

# 23. Impact Score

Add a simple heuristic.

Do not call it machine learning.

Example:

```text
LOW
MEDIUM
HIGH
```

Possible heuristic:

```text
LOW:
0–1 downstream callers

MEDIUM:
2–4 downstream callers

HIGH:
5+ downstream callers
OR
public API entry point affected
OR
multiple workflows affected
```

The score counts only HIGH-confidence callers (§15). LOW-confidence and
UNRESOLVED references never affect the score — they are surfaced
separately as "possible impact" (§22), so a name-collision fallback
can't silently inflate a symbol's risk level.

This is only a heuristic.

Document this clearly. Never describe it as "ML prediction", "AI
probability", "99% confidence", or a "production breakage probability" —
unless that functionality is actually implemented and validated.

Explain the score in plain terms, e.g.:

```text
Impact: HIGH

Reason:
The target has 6 downstream callers, including a detected public API
entry point and 4 related tests.
```

Do not pretend it predicts production breakage.

---

# 24. Feature Impact

Add a lightweight concept of "feature".

A feature is represented by:

```text
entry point
+
workflow
+
related symbols
+
tests
```

Example:

```text
Feature:
Authentication

Entry:
POST /login

Workflow:
login_endpoint
 → login
 → validate_user
 → create_token

Tests:
test_login
test_invalid_password
```

Then:

```text
analyze_impact("create_token")
```

can say:

```text
Potentially affected feature:

Authentication
```

Do not build a complicated feature ontology.

Infer features from workflows and entry points.

---

# 25. MCP Server

Use the official MCP Python SDK / FastMCP.

Do not implement MCP protocol internals yourself.

Expose exactly five tools:

```text
search_code
find_symbol
analyze_impact
trace_workflow
explain
```

---

# 26. `search_code`

Signature:

```text
search_code(query: str)
```

Returns concise matching symbols/files.

---

# 27. `find_symbol`

Signature:

```text
find_symbol(name: str)
```

Return:

```text
symbol
location
callers
callees
tests
entry points
```

---

# 28. `analyze_impact`

Signature:

```text
analyze_impact(symbol: str)
```

Return structured:

```text
target
direct_callers
indirect_callers
affected_files
affected_tests
entry_points
workflows
impact_level
reason
```

This is the flagship MCP tool.

---

# 29. `trace_workflow`

Signature:

```text
trace_workflow(symbol: str)
```

Return a compact workflow tree/path.

---

# 30. `explain`

Signature:

```text
explain(question: str)
```

The system should first perform deterministic analysis.

For example:

```text
question:
"What happens if I remove create_token?"
```

Determine:

```text
symbol
callers
files
tests
workflow
impact
```

Then provide that structured information to the LLM.

The LLM should produce the natural-language explanation.

Do not let the LLM independently scan the repository.

---

# 31. LLM Usage

LLM usage should be minimal.

Preferred:

```text
User question
    ↓
Local analysis
    ↓
compact structured context
    ↓
one LLM call
    ↓
answer
```

Avoid:

```text
LLM → file 1
LLM → file 2
LLM → file 3
LLM → file 4
...
```

Avoid an LLM call per symbol.

Avoid an LLM call per file.

The project should demonstrate efficient context construction.

---

# 32. Web Interface

Build a simple Streamlit interface.

Do not build React/Next.js for MVP.

Main page:

```text
RepoImpact

GitHub Repository
[ https://github.com/...             ]

[ Analyze Repository ]
```

After indexing:

```text
Repository: example-project

Files       82
Functions   413
Classes     57
Tests       96
APIs        18
```

Tabs:

```text
Overview
Chat
Search
Impact
```

---

# 33. Overview Tab

Show:

```text
Repository statistics

Architecture summary

Major modules

Detected API entry points

Top-level dependency relationships
```

Keep it simple.

No complex visualization library required.

---

# 34. Chat Tab

Provide:

```text
Ask about this repository...

[ How does authentication work? ]
```

The application should call the same core analysis functions used by MCP.

Do not create a separate intelligence implementation for the UI.

Important architecture:

```text
             Core Engine
             /         \
            /           \
           ▼             ▼
       MCP Server     Streamlit
           │             │
           ▼             ▼
         Claude         User
```

Both should use the same core modules.

---

# 35. Search Tab

Allow:

```text
Search repository
[ payment ]

Results:
PaymentService
process_payment
PaymentRepository
test_payment
```

Selecting a symbol should display:

```text
Location
Source
Callers
Callees
Tests
Entry points
```

---

# 36. Impact Tab

Allow:

```text
Symbol:
[ create_token ]

[ Analyze Impact ]
```

Show:

```text
Impact: HIGH

Direct callers
Indirect callers
Affected files
Affected tests
Affected workflows
API entry points

Reason
```

This should be the strongest UI screen.

---

# 37. UI Design Principle

Do not spend significant time on visual design.

The purpose of the interface is to demonstrate the underlying system.

Use:

* clean layout
* simple navigation
* readable code
* clear impact results

No authentication.

No user accounts.

No database hosted online.

No payment system.

---

# 38. Local Data

All repository indexes should be local.

Use:

```text
data/
    repositories/
        <repo-id>/
            source/
            repo.db
```

`<repo-id>` is derived from a hash of the normalized repository URL (or
local path) — never the raw owner/repo string used directly as a
filesystem path (§8).

Do not store source code in a remote service.

Do not upload repositories to a third-party indexing service.

---

# 39. Caching

Use a repository identifier/hash.

If the same repository is analyzed again and has not changed:

```text
reuse local index
```

Optional for MVP.

If implementing caching increases complexity significantly, defer it.

---

# 40. Git

Git is optional for MVP.

If easy, expose basic metadata:

```text
branch
commit
changed files
```

But do not make Git history analysis a core requirement.

Future version can add:

```text
git diff
+
impact engine
```

to answer:

> What could this PR break?

---

# 41. Error Handling

Handle:

```text
invalid GitHub URL
repository clone failure
invalid local path
malformed Python
missing symbol
ambiguous symbol
unresolved reference
cyclic dependency
empty repository
```

Never fabricate relationships.

If uncertain:

```text
Reference could not be resolved statically.
```

---

# 42. Testing

Tests are mandatory.

Test:

### Parser

```text
function extraction
class extraction
imports
calls
decorators
```

### Storage

```text
insert
query
update
re-index
```

### Graph

```text
A → B → C
```

Verify:

```text
callers
callees
transitive traversal
```

### Cycle

```text
A → B → C → A
```

Verify traversal terminates.

### Impact

Verify changing:

```text
B
```

identifies:

```text
A
```

as affected.

### Search

Verify:

```text
exact match
partial match
filename match
```

### Workflow

Verify:

```text
POST /login
 ↓
login_endpoint
 ↓
login
 ↓
database
```

---

# 43. Demo Repository

Create a small controlled repository.

Structure:

```text
examples/demo_repo/

app.py
routes.py
auth.py
users.py
payments.py
database.py

tests/
    test_auth.py
    test_users.py
    test_payments.py
```

Create realistic relationships.

Example:

```text
POST /login
     ↓
login_endpoint
     ↓
login
     ├── validate_user
     │      ↓
     │   database.get_user
     │
     └── create_token
```

And:

```text
POST /checkout
     ↓
checkout
     ↓
process_payment
     ↓
PaymentRepository
```

This repository should make every demo deterministic.

---

# 44. Required Demo

README must demonstrate:

## Example 1

```text
Question:

Where is authentication implemented?
```

## Example 2

```text
Question:

Explain the login workflow.
```

## Example 3

```text
Question:

Who calls create_token()?
```

## Example 4

```text
Question:

What breaks if I remove create_token()?
```

## Example 5

```text
Question:

Which tests are affected?
```

---

# 45. Example Impact Output

Aim for:

```text
┌──────────────────────────────────────────────┐
│ CHANGE IMPACT                                │
├──────────────────────────────────────────────┤
│                                              │
│ Target                                       │
│ create_token()                               │
│ auth.py:67                                   │
│                                              │
│ Direct callers                               │
│ └── login()                                  │
│                                              │
│ Indirect callers                             │
│ └── login_endpoint()                         │
│                                              │
│ Affected workflow                            │
│ └── Authentication                           │
│                                              │
│ API entry point                              │
│ └── POST /login                              │
│                                              │
│ Related tests                                │
│ ├── test_login                               │
│ └── test_authentication                      │
│                                              │
│ Impact                                       │
│ HIGH                                         │
│                                              │
└──────────────────────────────────────────────┘
```

---

# 46. What Makes This Different

Do not market it as:

> Another AI chatbot for GitHub repositories.

Position it as:

> **A lightweight repository intelligence engine that uses structural code analysis to determine change impact and execution workflows, exposed through MCP.**

The important architecture is:

```text
LLM
 ↓
MCP
 ↓
structured repository intelligence
 ↓
AST + dependency graph
 ↓
SQLite
```

The LLM is the interface.

The graph is the intelligence.

---

# 47. What NOT To Build

Strictly avoid:

```text
❌ embeddings
❌ vector DB
❌ RAG framework
❌ agents
❌ multi-agent system
❌ LangChain
❌ LlamaIndex
❌ LangGraph
❌ React
❌ Next.js
❌ PostgreSQL
❌ Redis
❌ Docker
❌ Kubernetes
❌ authentication
❌ user accounts
❌ cloud deployment
❌ payment
❌ multiple languages
❌ complete LSP implementation
❌ complete IDE
❌ GitHub OAuth
❌ GitHub App
```

If a feature isn't required for the core demo, defer it.

---

# 48. Development Order

Implement exactly in this sequence.

## Phase 1 — Parser

Build:

```text
repository
 ↓
Python files
 ↓
AST
 ↓
symbols/imports/calls
```

Write tests.

---

## Phase 2 — SQLite

Build:

```text
AST results
 ↓
SQLite
```

Write tests.

---

## Phase 3 — Graph

Build:

```text
SQLite
 ↓
call graph
```

Implement:

```text
callers
callees
transitive callers
transitive callees
```

Write tests.

---

## Phase 4 — Search

Implement:

```text
search_code()
find_symbol()
```

Write tests.

---

## Phase 5 — Impact

Implement:

```text
analyze_impact()
```

This is the core feature.

Write tests.

---

## Phase 6 — Workflow

Implement:

```text
trace_workflow()
```

Add API entry-point detection.

Write tests.

---

## Phase 7 — MCP

Expose the five tools:

```text
search_code
find_symbol
analyze_impact
trace_workflow
explain
```

Test through MCP Inspector.

---

## Phase 8 — Web UI

Build Streamlit UI.

Use the same core engine.

Do not duplicate logic.

---

## Phase 9 — LLM

Add `explain()`.

LLM receives only compact structured context.

---

## Phase 10 — Polish

Add:

```text
README
architecture diagram
screenshots
demo
error handling
```

---

# 49. Dependency Rule

Before adding a dependency, ask:

> Is this necessary for MVP?

Prefer:

```text
Python stdlib
```

over another package.

Allowed core dependencies:

```text
mcp
streamlit
pytest
```

Git is an external executable, not a Python dependency.

---

# 50. Performance / Token Rule

The application should minimize LLM context.

Bad:

```text
repository
 ↓
all source files
 ↓
LLM
```

Good:

```text
repository
 ↓
AST
 ↓
SQLite
 ↓
graph
 ↓
impact analysis
 ↓
300–1000 tokens of relevant context
 ↓
LLM
```

The MCP server should return concise structured information.

---

# 51. Security

Never execute arbitrary repository code.

The MVP should:

* parse source
* read source
* run Git commands safely
* store metadata

Do NOT:

```text
pip install repository
python setup.py
run repository
execute tests automatically
execute arbitrary scripts
```

Git clone is allowed.

Repository code should be treated as untrusted input.

---

# 52. README Positioning

README should explain:

### Problem

Large repositories are difficult for LLMs to understand efficiently.

### Conventional approach

Generic RAG often relies on:

```text
chunk
→ embed
→ vector search
→ LLM
```

### RepoImpact approach

```text
AST
→ symbols
→ relationships
→ dependency graph
→ impact analysis
→ compact LLM context
```

### Key benefit

Structural questions can be answered deterministically without requiring semantic embeddings.

---

# 53. Limitations

Explicitly state:

Static analysis cannot perfectly understand:

* dynamic imports
* monkey patching
* reflection
* runtime-generated attributes
* dependency injection
* dynamic dispatch
* metaprogramming
* complex decorators

Therefore impact results are:

> **static-analysis estimates, not guaranteed production breakage predictions.**

---

# 54. Future Roadmap

Do not implement these now.

Future:

```text
V1.1
- Tree-sitter
- better symbol resolution
- Git history
- incremental indexing
- PR impact analysis

V2
- TypeScript/JavaScript
- LSP integration
- VS Code extension
- architecture visualization

V3
- optional semantic search
- hybrid structural + semantic retrieval
- historical risk scoring
- team/repository analytics
```

---

# 55. Definition of Done

The MVP is complete when:

```text
[ ] GitHub URL accepted
[ ] Repository cloned locally
[ ] Python files discovered
[ ] AST parsed
[ ] Functions extracted
[ ] Classes extracted
[ ] Imports extracted
[ ] Calls extracted
[ ] Symbols stored in SQLite
[ ] References stored
[ ] Call graph built
[ ] Search works
[ ] Symbol lookup works
[ ] Impact analysis works
[ ] Workflow tracing works
[ ] API entry points detected
[ ] Tests detected
[ ] MCP server works
[ ] Five MCP tools work
[ ] Streamlit UI works
[ ] Chat works
[ ] LLM receives compact context
[ ] Demo repository works
[ ] Tests pass
[ ] README complete
```

---

# 56. Final Instruction to Claude

Do not over-engineer this project.

The most important feature is:

```text
"What breaks if I change this?"
```

Everything else exists to support that.

Prioritize:

```text
AST
→ SQLite
→ graph
→ impact
→ MCP
→ UI
```

Do not prioritize:

```text
LLM sophistication
embeddings
vector search
agents
frontend polish
multi-language support
cloud infrastructure
```

The finished system should be small enough that a developer can understand the entire architecture in one sitting.

Before coding, review this specification and identify any unnecessary complexity or technical flaw.

Then propose the final architecture.

Do not implement until the architecture has been reviewed.
