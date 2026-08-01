-- CrawAgent v2 迁移：时间列 Float(单精度) -> Double(双精度)
-- 原因：Unix 时间戳(~1.7e9)在单精度 FLOAT 下丢失亚秒精度，
--       导致同秒内多条消息 created_at 相同、排序不稳定。
-- 用法：mysql -u root -p crawagent < migrate_v2_time_precision.sql

USE crawagent;

ALTER TABLE harness_sessions
    MODIFY COLUMN created_at DOUBLE NOT NULL DEFAULT 0,
    MODIFY COLUMN updated_at DOUBLE NOT NULL DEFAULT 0;

ALTER TABLE harness_entries
    MODIFY COLUMN created_at DOUBLE NOT NULL DEFAULT 0;

ALTER TABLE harness_operation_logs
    MODIFY COLUMN started_at DOUBLE NOT NULL DEFAULT 0,
    MODIFY COLUMN finished_at DOUBLE NULL;

ALTER TABLE harness_global_facts
    MODIFY COLUMN updated_at DOUBLE NOT NULL DEFAULT 0;
