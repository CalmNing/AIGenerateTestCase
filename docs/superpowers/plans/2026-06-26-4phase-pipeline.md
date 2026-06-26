# 4-Phase Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a 4-phase AI test case generation pipeline (requirement splitting → test points → test case design → optimization) as an independent module alongside the existing generation flow.

**Architecture:** New `backend/app/pipeline/` module with 4 LangChain agents, 2 new DB models (RequirementPoint, TestPoint), extended TestCase model, new API endpoints, and frontend mode switching.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, LangChain, SQLite

## Global Constraints

- All models extend `BaseModel(SQLModel)` from `db/models.py`
- All routes use `SessionDep` + `CurrentUser` dependency injection
- All responses wrapped in `Response[T]` from `utils/base_response.py`
- JSON columns use `sa_type=JSON` (SQLite dialect)
- Timestamps use `Asia/Shanghai` timezone via `cn_tz`
- Auto-migration via `_migrate_missing_columns()` in `db/db.py` (no Alembic)
- LLM calls use `ChatOpenAI` from `langchain_openai` with `SecretStr` for API keys
- Structured output via `_structured_output_robust()` three-tier fallback

---

### Task 1: Database Models — RequirementPoint & TestPoint

**Files:**
- Modify: `backend/db/models.py` — add RequirementPoint, TestPoint models; extend TestCase
- Modify: `backend/db/db.py` — import new models for auto-registration

**Interfaces:**
- Produces: `RequirementPoint`, `TestPoint` SQLModel table classes
- `TestCase` gains: `test_point_id`, `test_type`, `design_method` optional fields

- [ ] **Step 1:** Add `RequirementPoint` model to `db/models.py` after existing models
- [ ] **Step 2:** Add `TestPoint` model to `db/models.py`
- [ ] **Step 3:** Add `test_point_id`, `test_type`, `design_method` fields to `TestCase` model
- [ ] **Step 4:** Add `Relationship` from `Session` to `RequirementPoint` and `TestPoint`
- [ ] **Step 5:** Import new models in `db/db.py` for metadata registration
- [ ] **Step 6:** Commit

---

### Task 2: Pipeline Schemas

**Files:**
- Create: `backend/app/pipeline/__init__.py`
- Create: `backend/app/pipeline/schemas.py`

**Interfaces:**
- Produces: Pydantic schemas for structured output (RequirementPointSchema, TestPointSchema, TestCaseSchema, OptimizedCaseSchema, PipelineProgress)

- [ ] **Step 1:** Create `backend/app/pipeline/__init__.py`
- [ ] **Step 2:** Create `backend/app/pipeline/schemas.py` with all Pydantic models
- [ ] **Step 3:** Commit

---

### Task 3: Prompt Templates

**Files:**
- Create: `backend/app/pipeline/prompts.py`

**Interfaces:**
- Produces: `REQUIREMENT_SPLITTER_SYSTEM`, `REQUIREMENT_ANALYSIS_USER`, `TEST_POINT_GENERATOR_SYSTEM`, `TEST_POINT_USER`, `TEST_CASE_DESIGNER_SYSTEM`, `TEST_CASE_DESIGN_USER`, `TEST_CASE_OPTIMIZER_SYSTEM`, `TEST_CASE_BATCH_OPTIMIZE_USER` string constants

- [ ] **Step 1:** Create `prompts.py` with all 4 agent prompt templates (adapted from testflow)
- [ ] **Step 2:** Commit

---

### Task 4: Agent 1 — Requirement Splitter

**Files:**
- Create: `backend/app/pipeline/requirement_splitter.py`

**Interfaces:**
- Consumes: `ChatOpenAI` model, requirement text string
- Produces: `List[dict]` with keys: `content`, `module`, `priority`, `order_index`

- [ ] **Step 1:** Create `requirement_splitter.py` with `RequirementSplitter` class
- [ ] **Step 2:** Implement `analyze(requirement_text, model_config) -> List[dict]`
- [ ] **Step 3:** Commit

---

### Task 5: Agent 2 — Test Point Generator

**Files:**
- Create: `backend/app/pipeline/test_point_generator.py`

**Interfaces:**
- Consumes: `ChatOpenAI` model, requirement point content, test categories, design methods
- Produces: `List[dict]` with keys: `content`, `test_type`, `design_method`, `priority`

- [ ] **Step 1:** Create `test_point_generator.py` with `TestPointGenerator` class
- [ ] **Step 2:** Implement `generate(requirement_content, model_config) -> List[dict]`
- [ ] **Step 3:** Commit

---

### Task 6: Agent 3 — Test Case Designer

**Files:**
- Create: `backend/app/pipeline/test_case_designer.py`

**Interfaces:**
- Consumes: `ChatOpenAI` model, test points list, original requirement text, selected skills
- Produces: `List[dict]` with keys: `test_point_id`, `title`, `description`, `preconditions`, `test_steps`

- [ ] **Step 1:** Create `test_case_designer.py` with `TestCaseDesigner` class
- [ ] **Step 2:** Implement `design_batch(test_points, requirement_text, model_config, selected_skills) -> List[dict]`
- [ ] **Step 3:** Commit

---

### Task 7: Agent 4 — Test Case Optimizer

**Files:**
- Create: `backend/app/pipeline/test_case_optimizer.py`

**Interfaces:**
- Consumes: `ChatOpenAI` model, test cases list
- Produces: `List[dict]` with keys: `id`, `title`, `description`, `preconditions`, `test_steps`

- [ ] **Step 1:** Create `test_case_optimizer.py` with `TestCaseOptimizer` class
- [ ] **Step 2:** Implement `optimize(test_cases, model_config) -> List[dict]`
- [ ] **Step 3:** Commit

---

### Task 8: Pipeline Runner

**Files:**
- Create: `backend/app/pipeline/pipeline_runner.py`

**Interfaces:**
- Consumes: all 4 agents, DB session, config
- Produces: Pipeline execution with progress callbacks

- [ ] **Step 1:** Create `pipeline_runner.py` with `PipelineRunner` class
- [ ] **Step 2:** Implement `run()` method orchestrating 4 phases
- [ ] **Step 3:** Commit

---

### Task 9: Pipeline API Routes

**Files:**
- Create: `backend/app/routes/pipeline.py`
- Modify: `backend/app/main.py` — register new router

**Interfaces:**
- Consumes: `SessionDep`, `CurrentUser`, `PipelineRunner`
- Produces: REST endpoints for pipeline execution and status

- [ ] **Step 1:** Create `backend/app/routes/pipeline.py` with all endpoints
- [ ] **Step 2:** Register router in `backend/app/main.py`
- [ ] **Step 3:** Commit

---

### Task 10: Frontend — Mode Switch & Pipeline UI

**Files:**
- Modify: `frontend/src/App.tsx` — add mode switching and pipeline progress UI

- [ ] **Step 1:** Add generation mode state and toggle UI
- [ ] **Step 2:** Add pipeline progress component
- [ ] **Step 3:** Add requirement points / test points viewing UI
- [ ] **Step 4:** Commit
