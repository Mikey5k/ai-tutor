CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_modules INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ingesting'
);

CREATE TABLE IF NOT EXISTS modules (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    module_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    key_concepts TEXT DEFAULT '[]',
    prerequisites TEXT DEFAULT '[]',
    estimated_minutes INTEGER DEFAULT 20,
    script_ready BOOLEAN DEFAULT 0,
    audio_ready BOOLEAN DEFAULT 0,
    FOREIGN KEY (course_id) REFERENCES courses(id)
);

CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    current_course_id TEXT,
    current_module INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS student_knowledge (
    student_id TEXT NOT NULL,
    concept TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    last_tested TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    correct_count INTEGER DEFAULT 0,
    incorrect_count INTEGER DEFAULT 0,
    PRIMARY KEY (student_id, concept)
);

CREATE TABLE IF NOT EXISTS student_progress (
    student_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    module_number INTEGER NOT NULL,
    completed BOOLEAN DEFAULT 0,
    test_score REAL DEFAULT 0.0,
    attempts INTEGER DEFAULT 0,
    completed_at TIMESTAMP,
    PRIMARY KEY (student_id, course_id, module_number)
);

CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT NOT NULL,
    event_data TEXT DEFAULT '{}'
);
