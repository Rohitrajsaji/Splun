-- 1. Multi-Project Workspace Registry
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Deterministic Relational Task & Scenario Ledger (80-scenario tracker)
CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scenario_num INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'Unstarted', -- 'Unstarted', 'Clarification_Needed', 'Blocked', 'Ready_For_Impl', 'Impl_Reported', 'Verified'
    blockers TEXT,
    linked_req_id TEXT,
    code_symbols TEXT,
    verification_evidence_id TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- 3. Bi-Temporal Decision & Epistemic Graph
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    subject_entity TEXT NOT NULL,
    decision_text TEXT NOT NULL,
    rationale TEXT,
    epistemic_status TEXT NOT NULL, -- 'DOCUMENTED_FACT', 'REPORTED_CLAIM', 'INFERENCE', 'PROPOSAL', 'APPROVED_DECISION', 'VERIFIED_OUTCOME'
    source_evidence_id TEXT NOT NULL,
    valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_to TIMESTAMP,
    superseded_by TEXT,
    created_by TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- 4. Verbatim Evidence Vault (FTS5 Full-Text Search)
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_vault USING fts5(
    evidence_id,
    project_id,
    source_type,       -- 'BRD', 'EXCEL', 'CHAT', 'GIT_DIFF'
    source_reference,  -- e.g., '80_Scenarios.xlsx!Sheet1!A14' or 'BRD.md#L42-L50'
    content,
    byte_range,
    recorded_at
);

-- 5. Structured Requirement Registry (parsed from BRD; current version per req_id)
CREATE TABLE IF NOT EXISTS requirements (
    project_id TEXT NOT NULL,
    req_id TEXT NOT NULL,
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    constraint_level TEXT,      -- 'mandatory', 'optional', or NULL if unstated
    constraint_source TEXT,     -- 'explicit' (Constraint: line) or 'keyword' (MUST/SHALL/MAY...)
    fields TEXT,                -- JSON list of field identifiers
    acceptance_criteria TEXT,   -- JSON list
    priority TEXT,
    source_reference TEXT NOT NULL,  -- e.g. 'BRD.md#L42-L50'
    evidence_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, req_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- 6. Code Symbol Registry (latest AST scan snapshot; history lives in evidence_vault)
CREATE TABLE IF NOT EXISTS code_symbols (
    project_id TEXT NOT NULL,
    file_path TEXT NOT NULL,    -- POSIX path relative to scan root
    qualname TEXT NOT NULL,     -- e.g. 'Customer.email'
    symbol_name TEXT NOT NULL,  -- e.g. 'email'
    kind TEXT NOT NULL,         -- 'class', 'function', 'method', 'field', 'assignment'
    parent TEXT,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    byte_range TEXT,
    details TEXT,               -- JSON: keywords, annotation, indicators, indicates, bases, args
    evidence_id TEXT NOT NULL,
    PRIMARY KEY (project_id, file_path, qualname),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
CREATE INDEX IF NOT EXISTS idx_code_symbols_name ON code_symbols(project_id, symbol_name);

-- 7. Multi-Thread Conversational Memory
CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'project', -- 'project' or 'lightweight'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    role TEXT NOT NULL, -- 'user' or 'assistant'
    content TEXT NOT NULL,
    mutations_json TEXT, -- snapshot of pre-mutation state and mutation summary for undo
    citations_json TEXT, -- cited requirement/evidence references
    undone INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (thread_id) REFERENCES threads(thread_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, created_at);
