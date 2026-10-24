CREATE TABLE IF NOT EXISTS "monitor" (
	id INTEGER not null primary key autoincrement,
	name VARCHAR(150),
	active BOOLEAN default 1 not null,
	user_id INTEGER references user on update cascade on delete
	set
		null,
		interval INTEGER default 20 not null,
		url TEXT,
		type VARCHAR(20),
		weight INTEGER default 2000,
		hostname VARCHAR(255),
		port INTEGER,
		created_date DATETIME default (DATETIME('now')) not null,
		keyword VARCHAR(255),
		maxretries INTEGER NOT NULL DEFAULT 0,
		ignore_tls BOOLEAN default 0 not null,
		upside_down BOOLEAN default 0 not null,
        maxredirects INTEGER default 10 not null,
        accepted_statuscodes_json TEXT default '["200-299"]' not null
, dns_resolve_type VARCHAR(5), dns_resolve_server VARCHAR(255), dns_last_result VARCHAR(255), retry_interval INTEGER default 0 not null, push_token VARCHAR(20) DEFAULT NULL, method TEXT default 'GET' not null, body TEXT default null, headers TEXT default null, basic_auth_user TEXT default null, basic_auth_pass TEXT default null, docker_host INTEGER REFERENCES docker_host(id), docker_container VARCHAR(255), proxy_id INTEGER REFERENCES proxy(id), expiry_notification BOOLEAN default 1, mqtt_topic TEXT, mqtt_success_message VARCHAR(255), mqtt_username VARCHAR(255), mqtt_password VARCHAR(255), database_connection_string VARCHAR(2000), database_query TEXT, auth_method VARCHAR(250), auth_domain TEXT, auth_workstation TEXT, grpc_url VARCHAR(255) default null, grpc_protobuf TEXT default null, grpc_body TEXT default null, grpc_metadata TEXT default null, grpc_method VARCHAR(255) default null, grpc_service_name VARCHAR(255) default null, grpc_enable_tls BOOLEAN default 0 not null, radius_username VARCHAR(255), radius_password VARCHAR(255), radius_calling_station_id VARCHAR(50), radius_called_station_id VARCHAR(50), radius_secret VARCHAR(255), resend_interval INTEGER default 0 not null, packet_size INTEGER DEFAULT 56 NOT NULL, game VARCHAR(255), http_body_encoding VARCHAR(25), description TEXT default null, tls_ca TEXT default null, tls_cert TEXT default null, tls_key TEXT default null, parent INTEGER REFERENCES [monitor] ([id]) ON DELETE SET NULL ON UPDATE CASCADE, invert_keyword BOOLEAN default 0 not null, json_path TEXT, expected_value VARCHAR(255), kafka_producer_topic VARCHAR(255), kafka_producer_brokers TEXT, kafka_producer_sasl_options TEXT, kafka_producer_message TEXT, oauth_client_id TEXT default null, oauth_client_secret TEXT default null, oauth_token_url TEXT default null, oauth_scopes TEXT default null, oauth_auth_method TEXT default null, timeout DOUBLE default 0 not null, gamedig_given_port_only BOOLEAN default 1 not null, kafka_producer_ssl BOOLEAN default 0 NOT NULL, kafka_producer_allow_auto_topic_creation BOOLEAN default 0 NOT NULL);
CREATE INDEX user_id on monitor (user_id);
CREATE INDEX proxy_id ON monitor (proxy_id);
CREATE TABLE [notification](
  [id] INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  [name] VARCHAR(255),
  [active] BOOLEAN NOT NULL DEFAULT 1,
  [user_id] INTEGER NOT NULL, is_default BOOLEAN default 0 NOT NULL, config TEXT);
CREATE TABLE [monitor_notification](
  [id] INTEGER PRIMARY KEY NOT NULL,
  [monitor_id] INTEGER NOT NULL REFERENCES [monitor]([id]) ON DELETE CASCADE ON UPDATE CASCADE,
  [notification_id] INTEGER NOT NULL REFERENCES [notification]([id]) ON DELETE CASCADE ON UPDATE CASCADE);
CREATE INDEX [monitor_notification_index]
ON [monitor_notification](
  [monitor_id],
  [notification_id]);
CREATE TABLE [heartbeat](
  [id] INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  [important] BOOLEAN NOT NULL DEFAULT 0,
  [monitor_id] INTEGER NOT NULL REFERENCES [monitor]([id]) ON DELETE CASCADE ON UPDATE CASCADE,
  [status] SMALLINT NOT NULL,
  [msg] TEXT,
  [time] DATETIME NOT NULL,
  [ping] INTEGER,
  [duration] INTEGER NOT NULL DEFAULT 0, down_count INTEGER default 0 not null);
CREATE INDEX [monitor_id] ON [heartbeat]([monitor_id]);
CREATE INDEX [important] ON [heartbeat]([important]);
CREATE INDEX monitor_time_index ON heartbeat (monitor_id, time);
CREATE INDEX monitor_important_time_index ON heartbeat (monitor_id, important,time);
