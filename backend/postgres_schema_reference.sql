-- Generated from current SQLAlchemy models
-- Reference only; not executed against any database.

CREATE TABLE organizations (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	is_active BOOLEAN DEFAULT '1' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE audit_number_counters (
	id SERIAL NOT NULL, 
	site_id VARCHAR(20) NOT NULL, 
	year INTEGER NOT NULL, 
	last_sequence INTEGER DEFAULT '0' NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_audit_counter_site_year UNIQUE (site_id, year)
);

CREATE INDEX ix_audit_number_counters_site_id ON audit_number_counters (site_id);

CREATE TABLE plants (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	location VARCHAR(200), 
	capacity_mw FLOAT, 
	site_id VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	building_name VARCHAR(200), 
	roof_type VARCHAR(100), 
	city VARCHAR(100), 
	state VARCHAR(100), 
	no_of_blocks INTEGER, 
	dc_capacity FLOAT, 
	ac_capacity FLOAT, 
	central_inverter_type VARCHAR(200), 
	central_inverter_count INTEGER, 
	string_inverter_type VARCHAR(200), 
	string_inverter_count INTEGER, 
	scb_count INTEGER, 
	mms_type VARCHAR(200), 
	mms_count INTEGER, 
	transformer_type VARCHAR(200), 
	transformer_count INTEGER, 
	clamps_connectors_count INTEGER, 
	lt_panel_type VARCHAR(200), 
	lt_panel_count INTEGER, 
	ht_panel_type VARCHAR(200), 
	ht_panel_count INTEGER, 
	acdb_count INTEGER, 
	ups_db_battery_count INTEGER, 
	lightning_protection_count INTEGER, 
	dc_earthing VARCHAR(200), 
	ac_earthing VARCHAR(200), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_plants_site_id ON plants (site_id);

CREATE TABLE asset_types (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE audit_sections (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	section_order INTEGER, 
	PRIMARY KEY (id)
);

CREATE TABLE sites (
	id VARCHAR(36) NOT NULL, 
	site_id VARCHAR(20) NOT NULL, 
	site_name VARCHAR(200) NOT NULL, 
	organization_id VARCHAR(36), 
	is_active BOOLEAN DEFAULT '1' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id)
);

CREATE INDEX ix_sites_organization_id ON sites (organization_id);

CREATE UNIQUE INDEX ix_sites_site_id ON sites (site_id);

CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	username VARCHAR(200) NOT NULL, 
	full_name VARCHAR(200) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	role VARCHAR(50), 
	is_active BOOLEAN, 
	site_id VARCHAR(20), 
	organization_id VARCHAR(36), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (username), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id)
);

CREATE INDEX ix_users_site_id ON users (site_id);

CREATE INDEX ix_users_organization_id ON users (organization_id);

CREATE TABLE assets (
	id VARCHAR(36) NOT NULL, 
	plant_id VARCHAR(36), 
	asset_type_id INTEGER, 
	asset_name VARCHAR(200), 
	is_active BOOLEAN, 
	site_id VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(plant_id) REFERENCES plants (id), 
	FOREIGN KEY(asset_type_id) REFERENCES asset_types (id)
);

CREATE INDEX ix_assets_site_id ON assets (site_id);

CREATE TABLE audit_questions (
	id SERIAL NOT NULL, 
	section_id INTEGER, 
	asset_type_id INTEGER, 
	question_text TEXT NOT NULL, 
	is_mandatory BOOLEAN, 
	question_order INTEGER, 
	is_active BOOLEAN, 
	source VARCHAR(200), 
	weight FLOAT, 
	added_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	retired_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(section_id) REFERENCES audit_sections (id), 
	FOREIGN KEY(asset_type_id) REFERENCES asset_types (id)
);

CREATE TABLE audits (
	id VARCHAR(36) NOT NULL, 
	audit_number VARCHAR(40), 
	plant_id VARCHAR(36), 
	site_id VARCHAR(20) NOT NULL, 
	audit_date VARCHAR(20), 
	audit_time VARCHAR(10), 
	status VARCHAR(50), 
	building_name VARCHAR(200), 
	capacity_mw VARCHAR(50), 
	roof_type VARCHAR(100), 
	city VARCHAR(100), 
	state VARCHAR(100), 
	audit_type VARCHAR(100), 
	auditor_name VARCHAR(200), 
	auditee_name VARCHAR(200), 
	asset_id_ref VARCHAR(100), 
	is_deleted BOOLEAN, 
	submitted_at TIMESTAMP WITHOUT TIME ZONE, 
	compliance_pct FLOAT, 
	score_pct FLOAT, 
	site_rating VARCHAR(50), 
	score_detail TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(plant_id) REFERENCES plants (id)
);

CREATE INDEX ix_audits_site_status ON audits (site_id, status);

CREATE UNIQUE INDEX ix_audits_audit_number ON audits (audit_number);

CREATE INDEX ix_audits_site_id ON audits (site_id);

CREATE TABLE mobile_api_tokens (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	device_name VARCHAR(120) DEFAULT 'Android device' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (token_hash)
);

CREATE INDEX ix_mobile_api_tokens_expires_at ON mobile_api_tokens (expires_at);

CREATE INDEX ix_mobile_api_tokens_revoked_at ON mobile_api_tokens (revoked_at);

CREATE INDEX ix_mobile_api_tokens_user_id ON mobile_api_tokens (user_id);

CREATE TABLE user_site_access (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	site_id VARCHAR(20) NOT NULL, 
	is_active BOOLEAN DEFAULT '1' NOT NULL, 
	granted_by VARCHAR(36), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_site_access UNIQUE (user_id, site_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(site_id) REFERENCES sites (site_id) ON DELETE CASCADE, 
	FOREIGN KEY(granted_by) REFERENCES users (id)
);

CREATE INDEX ix_user_site_access_user_id ON user_site_access (user_id);

CREATE INDEX ix_user_site_access_site_id ON user_site_access (site_id);

CREATE TABLE user_plant_assignments (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	plant_id VARCHAR(36) NOT NULL, 
	site_id VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_plant UNIQUE (user_id, plant_id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(plant_id) REFERENCES plants (id)
);

CREATE INDEX ix_user_plant_assignments_site_id ON user_plant_assignments (site_id);

CREATE INDEX ix_user_plant_assignments_plant_id ON user_plant_assignments (plant_id);

CREATE INDEX ix_user_plant_assignments_user_id ON user_plant_assignments (user_id);

CREATE TABLE audit_site_details (
	id VARCHAR(36) NOT NULL, 
	audit_id VARCHAR(36), 
	no_of_blocks INTEGER, 
	dc_capacity FLOAT, 
	ac_capacity FLOAT, 
	central_inverter_type VARCHAR(200), 
	central_inverter_count INTEGER, 
	string_inverter_type VARCHAR(200), 
	string_inverter_count INTEGER, 
	scb_count INTEGER, 
	mms_type VARCHAR(200), 
	mms_count INTEGER, 
	transformer_type VARCHAR(200), 
	transformer_count INTEGER, 
	clamps_connectors_count INTEGER, 
	lt_panel_type VARCHAR(200), 
	lt_panel_count INTEGER, 
	ht_panel_type VARCHAR(200), 
	ht_panel_count INTEGER, 
	acdb_count INTEGER, 
	ups_db_battery_count INTEGER, 
	lightning_protection_count INTEGER, 
	dc_earthing VARCHAR(200), 
	ac_earthing VARCHAR(200), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (audit_id), 
	FOREIGN KEY(audit_id) REFERENCES audits (id)
);

CREATE TABLE audit_assets (
	id VARCHAR(36) NOT NULL, 
	audit_id VARCHAR(36), 
	asset_id VARCHAR(36), 
	location VARCHAR(300), 
	rank VARCHAR(50), 
	observation TEXT, 
	remarks TEXT, 
	is_audited BOOLEAN, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_audit_asset UNIQUE (audit_id, asset_id), 
	FOREIGN KEY(audit_id) REFERENCES audits (id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id)
);

CREATE TABLE audit_responses (
	id VARCHAR(36) NOT NULL, 
	audit_id VARCHAR(36), 
	asset_id VARCHAR(36), 
	question_id INTEGER, 
	response_status VARCHAR(50), 
	remarks TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	responded_by VARCHAR(200), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_audit_response UNIQUE (audit_id, asset_id, question_id), 
	FOREIGN KEY(audit_id) REFERENCES audits (id), 
	FOREIGN KEY(asset_id) REFERENCES assets (id), 
	FOREIGN KEY(question_id) REFERENCES audit_questions (id)
);

CREATE TABLE audit_response_photos (
	id VARCHAR(36) NOT NULL, 
	audit_response_id VARCHAR(36), 
	file_path VARCHAR(500), 
	latitude FLOAT, 
	longitude FLOAT, 
	gps_accuracy FLOAT, 
	captured_at TIMESTAMP WITHOUT TIME ZONE, 
	uploaded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(audit_response_id) REFERENCES audit_responses (id)
);

CREATE TABLE corrective_actions (
	id VARCHAR(36) NOT NULL, 
	audit_response_id VARCHAR(36), 
	description TEXT, 
	priority VARCHAR(50), 
	status VARCHAR(50), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(audit_response_id) REFERENCES audit_responses (id)
);

CREATE TABLE observations (
	id VARCHAR(36) NOT NULL, 
	audit_id VARCHAR(36) NOT NULL, 
	audit_response_id VARCHAR(36) NOT NULL, 
	site_id VARCHAR(20) NOT NULL, 
	observation TEXT, 
	severity VARCHAR(20), 
	responsible_person VARCHAR(200), 
	target_date VARCHAR(20), 
	remarks TEXT, 
	finding_type VARCHAR(10) NOT NULL, 
	action_taken TEXT, 
	closure_date VARCHAR(20), 
	reviewer_comments TEXT, 
	review_status VARCHAR(20), 
	status VARCHAR(30), 
	reviewed_by VARCHAR(200), 
	reviewed_at TIMESTAMP WITHOUT TIME ZONE, 
	closed_at TIMESTAMP WITHOUT TIME ZONE, 
	reopen_count INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(audit_id) REFERENCES audits (id), 
	UNIQUE (audit_response_id), 
	FOREIGN KEY(audit_response_id) REFERENCES audit_responses (id)
);

CREATE INDEX ix_observations_status ON observations (status);

CREATE INDEX ix_observations_audit_id ON observations (audit_id);

CREATE INDEX ix_observations_site_id ON observations (site_id);

CREATE TABLE observation_photos (
	id VARCHAR(36) NOT NULL, 
	observation_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(20), 
	file_path VARCHAR(500), 
	latitude FLOAT, 
	longitude FLOAT, 
	gps_accuracy FLOAT, 
	captured_at TIMESTAMP WITHOUT TIME ZONE, 
	uploaded_by VARCHAR(200), 
	uploaded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(observation_id) REFERENCES observations (id)
);

CREATE INDEX ix_observation_photos_observation_id ON observation_photos (observation_id);

CREATE TABLE observation_events (
	id VARCHAR(36) NOT NULL, 
	observation_id VARCHAR(36) NOT NULL, 
	event_type VARCHAR(40) NOT NULL, 
	from_status VARCHAR(30), 
	to_status VARCHAR(30), 
	comment TEXT, 
	actor VARCHAR(200), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(observation_id) REFERENCES observations (id)
);

CREATE INDEX ix_observation_events_observation_id ON observation_events (observation_id);
