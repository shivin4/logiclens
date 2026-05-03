# LogicLens Code Dependency Analyzer

LogicLens is a code analysis and visualization tool designed to help developers understand complex codebases through dependency graphs, semantic search, and AI-driven impact analysis. It converts source code into a structured graph representation, enabling efficient exploration of relationships between files, classes, and functions.

## Overview

Modern codebases become difficult to navigate as they scale. LogicLens addresses this by combining static analysis, graph databases, and AI agents to provide:

- Structural visibility across the codebase
- Dependency tracking between components
- Natural language search over code
- Predictive analysis of code changes

This makes it useful for debugging, onboarding, refactoring, and architectural reviews.

## Key Features

### Multi-Language Parsing (Tree-sitter)

LogicLens uses Tree-sitter to generate Abstract Syntax Trees (AST) for multiple languages, enabling consistent parsing across heterogeneous codebases.

Supported languages:
- Python (.py)
- JavaScript / TypeScript (.js, .ts)
- Java (.java)
- Go (.go)
- C++ (.cpp, .cc)

### Graph-Based Dependency Visualization (Neo4j)

All extracted entities such as files, classes, and functions are stored in a Neo4j graph database.

Relationships include:
- `CONTAINS` (file → class/function)
- `CALLS` (function → function)

The frontend visualizes this data as an interactive dependency graph, making relationships easy to explore.

### AI-Powered Impact Analysis (CrewAI)

LogicLens uses a multi-agent system to simulate architectural reasoning.

Given a target function:
- Traces all upstream and downstream dependencies
- Identifies affected components (blast radius)
- Generates a structured report explaining potential impact

This helps reduce risk during refactoring or feature updates.

### Semantic Code Search (ChromaDB)

Instead of keyword-based search, LogicLens enables natural language queries over the codebase.

Examples:
- "Where is authentication handled?"
- "Find password hashing logic"

Code is embedded into a vector database (ChromaDB), allowing semantic retrieval of relevant logic.

### Git-Based Hotspot Analysis

LogicLens integrates with Git history to analyze temporal patterns in the codebase.

Key insights:
- Commit timeline
- File-level churn rate
- Frequently modified components

This helps identify unstable or high-risk areas of the system.

## Tech Stack
- **Backend:** Python (Flask), GitPython
- **Parsing Engine:** Tree-sitter
- **Databases:** Neo4j (graph), ChromaDB (vector)
- **AI Layer:** CrewAI, Groq API, Gemini API
- **Frontend:** JavaScript, vis.js, TailwindCSS

## Installation

1. **Clone the Repository**
   ```bash
   git clone https://github.com/palakbhatt1/LogicLens-Code-Dependency-Analyzer.git
   cd LogicLens-Code-Dependency-Analyzer
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables**
   Create a `.env` file in the root directory:
   ```env
   GEMINI_API_KEY=your_gemini_key
   GROQ_API_KEY=your_groq_key

   NEO4J_URI=neo4j://127.0.0.1:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=your_password
   ```

4. **Run the Application**
   ```bash
   python app.py
   ```
   Access the application at: `http://localhost:5000`

## Usage

1. Launch the application and provide the absolute path to the target project.
2. Run analysis to generate the dependency graph.
3. Explore nodes to view:
   - Source code
   - Dependency relationships
   - AI-generated explanations
4. Use semantic search to locate logic using natural language.
5. Analyze Git-based hotspots to identify unstable files.
6. Run impact analysis to evaluate the effect of modifying specific functions.
