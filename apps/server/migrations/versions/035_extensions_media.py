"""Versioned extensions, media connections, persistent jobs and UMA residency receipts."""

from alembic import op

revision = "035_extensions_media"
down_revision = "034_computer_control_switch"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE extension_installations (
            user_id VARCHAR(36) NOT NULL,
            workspace_id VARCHAR(36),
            name VARCHAR NOT NULL,
            source JSONB NOT NULL,
            current_revision_id VARCHAR NOT NULL,
            enabled BOOLEAN NOT NULL,
            status VARCHAR NOT NULL,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(user_id) REFERENCES users (id),
            FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_extension_installations_user_id ON extension_installations (user_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_extension_installations_workspace_id
        ON extension_installations (workspace_id)
        """
    )
    op.execute(
        """
        CREATE TABLE extension_revisions (
            installation_id VARCHAR(36) NOT NULL,
            content_digest VARCHAR NOT NULL,
            source_revision VARCHAR,
            manifest JSONB NOT NULL,
            storage_path VARCHAR NOT NULL,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (installation_id, content_digest),
            FOREIGN KEY(installation_id) REFERENCES extension_installations (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_extension_revisions_installation_id ON extension_revisions (installation_id)
        """
    )
    op.execute(
        """
        CREATE TABLE extension_overrides (
            installation_id VARCHAR(36) NOT NULL,
            workspace_id VARCHAR(36) NOT NULL,
            enabled BOOLEAN NOT NULL,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (installation_id, workspace_id),
            FOREIGN KEY(installation_id) REFERENCES extension_installations (id),
            FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE extension_mcp_bindings (
            installation_id VARCHAR(36) NOT NULL,
            revision_id VARCHAR(36) NOT NULL,
            workspace_id VARCHAR(36) NOT NULL,
            connection_id VARCHAR(36) NOT NULL,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(installation_id) REFERENCES extension_installations (id),
            FOREIGN KEY(revision_id) REFERENCES extension_revisions (id),
            FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
            UNIQUE (connection_id),
            FOREIGN KEY(connection_id) REFERENCES mcp_connections (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE media_connections (
            user_id VARCHAR(36) NOT NULL,
            workspace_id VARCHAR(36),
            name VARCHAR NOT NULL,
            capability VARCHAR NOT NULL,
            driver VARCHAR NOT NULL,
            model_identifier VARCHAR NOT NULL,
            endpoint VARCHAR,
            encrypted_api_key VARCHAR,
            allow_remote BOOLEAN NOT NULL,
            enabled BOOLEAN NOT NULL,
            is_default BOOLEAN NOT NULL,
            configuration JSONB NOT NULL,
            verification VARCHAR NOT NULL,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(user_id) REFERENCES users (id),
            FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_media_connections_user_id ON media_connections (user_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_media_connections_workspace_id ON media_connections (workspace_id)
        """
    )
    op.execute(
        """
        CREATE TABLE media_tasks (
            run_id VARCHAR(36) NOT NULL,
            connection_id VARCHAR(36) NOT NULL,
            connection_version INTEGER NOT NULL,
            provider_snapshot JSONB NOT NULL,
            encrypted_credential VARCHAR,
            request JSONB NOT NULL,
            status VARCHAR NOT NULL,
            phase VARCHAR NOT NULL,
            progress DOUBLE PRECISION,
            dispatch_state VARCHAR NOT NULL,
            provider_job_id VARCHAR,
            artifacts JSONB NOT NULL,
            metadata_json JSONB NOT NULL,
            error JSONB,
            resources_closed BOOLEAN NOT NULL,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (run_id),
            FOREIGN KEY(run_id) REFERENCES runs (id),
            FOREIGN KEY(connection_id) REFERENCES media_connections (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE media_residencies (
            run_id VARCHAR(36) NOT NULL,
            phase VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            reserved_bytes BIGINT NOT NULL,
            headroom_bytes BIGINT NOT NULL,
            command_directory VARCHAR NOT NULL,
            supervisor_identity JSONB,
            closed_at TIMESTAMP WITH TIME ZONE,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (run_id, phase),
            FOREIGN KEY(run_id) REFERENCES runs (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_media_residencies_run_id ON media_residencies (run_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_media_residencies_status ON media_residencies (status)
        """
    )


def downgrade():
    op.drop_table("media_residencies")
    op.drop_table("media_tasks")
    op.drop_table("media_connections")
    op.drop_table("extension_mcp_bindings")
    op.drop_table("extension_overrides")
    op.drop_table("extension_revisions")
    op.drop_table("extension_installations")
