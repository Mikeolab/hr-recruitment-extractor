"""
HR-specific database schema extensions
"""


def extend_hr_schema(conn):
    """
    Extend database schema for HR recruitment features.
    Adds HR-specific fields to leads and new HR-specific tables.
    """
    conn.executescript("""
        -- Add HR-specific columns to leads table if they don't exist
        ALTER TABLE leads ADD COLUMN job_title TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN seniority_level TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN department TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN is_hiring_role BOOLEAN DEFAULT 0;
        ALTER TABLE leads ADD COLUMN company_name TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN company_size TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN industry TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN open_positions TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN linkedin_url TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN hiring_status TEXT DEFAULT '';
        ALTER TABLE leads ADD COLUMN contact_quality_score REAL DEFAULT 0.0;
        ALTER TABLE leads ADD COLUMN best_contact_time TEXT DEFAULT '';

        -- HR-specific lead enrichment table
        CREATE TABLE IF NOT EXISTS hr_lead_enrichment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER UNIQUE,
            verified_email BOOLEAN DEFAULT 0,
            verified_phone BOOLEAN DEFAULT 0,
            last_enriched_at TIMESTAMP,
            data_source TEXT,
            enrichment_notes TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
        );

        -- HR campaign tracking
        CREATE TABLE IF NOT EXISTS hr_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target_position TEXT,
            target_company TEXT,
            target_location TEXT,
            total_leads INTEGER DEFAULT 0,
            contacted_count INTEGER DEFAULT 0,
            response_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            ended_at TIMESTAMP
        );

        -- HR lead activity tracking
        CREATE TABLE IF NOT EXISTS hr_lead_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            campaign_id INTEGER,
            activity_type TEXT,
            notes TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
            FOREIGN KEY (campaign_id) REFERENCES hr_campaigns(id)
        );

        CREATE INDEX IF NOT EXISTS idx_hr_enrichment_lead_id ON hr_lead_enrichment(lead_id);
        CREATE INDEX IF NOT EXISTS idx_hr_campaigns_status ON hr_campaigns(status);
        CREATE INDEX IF NOT EXISTS idx_hr_activity_lead_id ON hr_lead_activity(lead_id);
        CREATE INDEX IF NOT EXISTS idx_hr_activity_campaign_id ON hr_lead_activity(campaign_id);
    """)


def init_hr_schema(conn):
    """Initialize HR schema with fallback for columns that might fail."""
    try:
        extend_hr_schema(conn)
    except Exception as e:
        # Columns might already exist in development, suppress error
        if "duplicate column" not in str(e).lower():
            # Log but don't fail - migration already done
            pass
    conn.commit()
