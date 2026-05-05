# Project Report: LogicLens Code Dependency Analyzer

## 1. Abstract
As software systems scale, understanding structural and temporal dependencies becomes increasingly difficult. LogicLens is an advanced code analysis and visualization tool designed to resolve this complexity by converting source code into a highly structured, interactive graph representation. Utilizing Tree-sitter for multi-language Abstract Syntax Tree (AST) extraction, LogicLens stores structural dependencies in a Neo4j graph database and embeds semantic logic into a ChromaDB vector database. Furthermore, LogicLens integrates CrewAI to provide autonomous "What-If" impact analysis and leverages GitPython to overlay temporal repository data (code hotspots and churn). This report details the architecture, implementation methodology, and analytical capabilities of the LogicLens system.

## 2. Introduction
Modern software engineering relies heavily on large-scale, heterogeneous codebases. Developers frequently face challenges when onboarding, refactoring, or debugging due to the hidden dependencies between files, classes, and functions. Traditional IDEs offer localized jump-to-definition features, but lack macro-level visibility into how a system is architected or how specific code changes might propagate (the "blast radius"). LogicLens was conceptualized to bridge this gap by combining static analysis, graph theory, vector search, and Large Language Models (LLMs). The tool provides a visual dashboard where developers can visually traverse dependencies, query code using natural language, and predict the architectural impact of their changes before writing a single line of code.

## 3. Literature Review
The domain of static code analysis has evolved significantly. Traditional tools like SonarQube focus heavily on code quality and security vulnerabilities via abstract interpretation. Dependency visualization tools like Understand or Sourcetrail (now deprecated) utilized static parsing to map component relationships but struggled with dynamic or multi-language projects.
Recent advancements in parsing technology, specifically GitHub's **Tree-sitter**, have enabled high-speed, incremental parsing that standardizes ASTs across languages. Simultaneously, the application of **Graph Databases (Neo4j)** has proven highly effective for mapping complex, deeply nested code relationships that relational databases struggle to query efficiently. Furthermore, the introduction of Vector Databases (**ChromaDB**) and LLMs has allowed developers to bridge the gap between structural code and semantic natural language, enabling semantic search and AI-driven architectural reasoning. Tools like CodeScene have also popularized the analysis of temporal dependencies (Git churn) to identify technical debt, a concept highly influential to the LogicLens Source Control module.

## 4. Problem Statement
The primary problem addressed by LogicLens is the cognitive overload developers experience when managing complex, multi-language software architectures. Specifically:
1. **Disconnected Architectures:** It is difficult to visualize how different components (e.g., a JavaScript frontend and a Python backend) interact.
2. **Unpredictable Blast Radius:** Modifying a core utility function carries unknown risks, often breaking downstream systems unexpectedly.
3. **Keyword Dependency:** Searching for logic is restricted to exact variable names, ignoring the actual semantic intent of the code.
4. **Hidden Technical Debt:** Static snapshots of code fail to reveal which files are constantly breaking or changing (temporal instability).

## 5. Methodology & System Architecture
The LogicLens system architecture follows a distinct data pipeline, transforming raw text into an interactive, AI-assisted dashboard.

### 5.1 Multi-Language AST Extraction (Tree-sitter)
The backend utilizes Tree-sitter to perform localized parsing of the target directory. It supports Python, JavaScript, TypeScript, Java, Go, and C++. The `extractor.py` module defines language-specific grammar maps to identify `Function` and `Class` definitions, as well as `call_expression` nodes to determine function invocations.

### 5.2 Storage Layer (Neo4j & ChromaDB)
Extracted entities are synchronized into two distinct databases:
- **Neo4j Graph Database:** Stores nodes (`File`, `Class`, `Function`) and relationships (`CONTAINS`, `CALLS`). This allows for highly efficient recursive querying (e.g., tracing a dependency chain 5 levels deep).
- **ChromaDB Vector Database:** Stores the raw code blocks alongside their vector embeddings, enabling similarity matching for natural language queries.

### 5.3 AI Impact Analysis Engine (CrewAI)
The "What-If" module utilizes CrewAI to orchestrate a multi-agent system. When a user selects a function, the engine queries Neo4j via a custom Cypher tool to find all incoming `:CALLS` relationships. The AI agents (Graph Analyst and Semantic Architect) then analyze the original source code and the structural graph to synthesize a "Blast Radius" report, explaining exactly how downstream components will be affected.

### 5.4 Temporal Hotspot Analysis (GitPython)
LogicLens dynamically hooks into the analyzed project's `.git` repository. By iterating through the most recent 50 commits, the system calculates the "churn rate" (frequency of modification) for every file. This data is exposed in the Source Control dashboard to highlight architectural hotspots.

### 5.5 User Interface & UX Design
The frontend `index.html` leverages `vis.js` to render the Neo4j data as a dynamic, interactive force-directed graph. The UI follows a strict dark-mode Glassmorphism design philosophy (using Vanilla TailwindCSS) to reduce developer eye strain. Information is compartmentalized into sleek, sliding side-drawers for Impact Analysis, Semantic Search, and Git History, keeping the central graph unobstructed.

## 6. Experimental Analysis and Discussion of Results
During the implementation and testing of LogicLens, several critical features were validated:

### 6.1 Accurate Structural Mapping and Bug Resolution
Initial tests of the graph visualization revealed issues with misleading dependency counts. Specifically, the system was mislabeling parent-child structural containment (`CONTAINS`) as function calls (`CALLED BY`). This was diagnosed and resolved by restricting the Neo4j API trace queries to strictly match `[r:CALLS]`. Post-resolution, the graph accurately reflected true function-to-function invocations, aligning perfectly with the AI's blast radius calculations.

### 6.2 Bridging the "Disconnected Islands"
A significant architectural challenge was parsing cross-language network calls (e.g., a React UI fetching a Flask API). Because AST engines cannot parse strings, these endpoints formed "disconnected islands" in the graph. The methodology was expanded to include a secondary Regex/String-Matching pass over the raw code blocks. By extracting API strings (`/api/...`) from both frontend and backend files, the system successfully forced a `[:CALLS_API]` edge, bridging the architectural gap.

### 6.3 Temporal Insight Validation
The Git Source Control module successfully generated the Commit Timeline and Code Hotspots dashboards. When tested on active repositories, the churn analysis accurately highlighted files with the highest commit frequency, providing an immediate visual indicator of technical debt and complex logic zones.

## 7. Limitations & Future Scope
While LogicLens is highly capable, there are areas targeted for future enhancement:
- **Dynamic Route Parsing:** The current Cross-Language API Bridge relies on Regex string matching (`/api/...`). It may miss highly abstracted or dynamically constructed API routes. Future versions could employ LLMs to identify obscure network calls.
- **IDE Integration:** Currently, LogicLens operates as a standalone web application. Developing a VS Code or IntelliJ extension would allow developers to access the dependency graph natively alongside their code editor.
- **Cloud Deployment Architecture:** Transitioning from a local Flask/Neo4j setup to a cloud-native architecture (using Neo4j Aura and managed ChromaDB) would enable real-time collaborative graph analysis for remote engineering teams.

## 8. Conclusions
LogicLens successfully achieves its objective of demystifying complex codebases. By integrating deterministic AST parsing with the predictive reasoning of LLMs and the temporal history of Git, the tool provides a comprehensive, 360-degree view of software architecture. The interactive graph prevents cognitive overload, the Semantic Search accelerates code discovery, and the CrewAI Impact Analysis actively prevents regressions by predicting the exact consequences of code modifications. The system serves as a powerful foundational tool for modern software maintenance.

## 9. References
1. GitHub. (n.d.). *Tree-sitter: An incremental parsing system for programming tools*. Retrieved from https://tree-sitter.github.io/tree-sitter/
2. Neo4j, Inc. (n.d.). *Neo4j Graph Database Platform*. Retrieved from https://neo4j.com/
3. Chroma. (n.d.). *Chroma: The AI-native open-source embedding database*. Retrieved from https://www.trychroma.com/
4. CrewAI. (n.d.). *Framework for orchestrating role-playing, autonomous AI agents*. Retrieved from https://www.crewai.com/
5. GitPython Developers. (n.d.). *GitPython Documentation*. Retrieved from https://gitpython.readthedocs.io/
6. Vis.js Community. (n.d.). *vis.js: A dynamic, browser based visualization library*. Retrieved from https://visjs.org/
