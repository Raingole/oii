package dev.yin2hao.smsbridge.queue

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import dev.yin2hao.smsbridge.network.SmsEventRequest

class PendingEventStore(context: Context) : SQLiteOpenHelper(context, "sms_bridge.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE pending (event_id TEXT PRIMARY KEY, timestamp INTEGER, sender TEXT, body TEXT, code TEXT, retry_count INTEGER, next_retry_at INTEGER)")
        db.execSQL("CREATE TABLE processed (event_id TEXT PRIMARY KEY, processed_at INTEGER)")
    }
    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
    @Synchronized fun enqueue(event: SmsEventRequest): Boolean {
        val now = System.currentTimeMillis()
        writableDatabase.insertWithOnConflict("pending", null, android.content.ContentValues().apply {
            put("event_id", event.eventId); put("timestamp", event.timestamp); put("sender", event.sender)
            put("body", event.body); put("code", event.code); put("retry_count", 0); put("next_retry_at", now)
        }, SQLiteDatabase.CONFLICT_IGNORE)
        return true
    }
    @Synchronized fun due(now: Long = System.currentTimeMillis()): List<SmsEventRequest> {
        val result = mutableListOf<SmsEventRequest>()
        readableDatabase.query("pending", null, "next_retry_at <= ?", arrayOf(now.toString()), null, null, "next_retry_at ASC", "50").use { c ->
            while (c.moveToNext()) result += SmsEventRequest(c.getString(c.getColumnIndexOrThrow("event_id")), c.getLong(c.getColumnIndexOrThrow("timestamp")), c.getString(c.getColumnIndexOrThrow("sender")), c.getString(c.getColumnIndexOrThrow("body")), c.getString(c.getColumnIndexOrThrow("code")))
        }
        return result
    }
    @Synchronized fun success(eventId: String) {
        writableDatabase.delete("pending", "event_id = ?", arrayOf(eventId))
        writableDatabase.insertWithOnConflict("processed", null, android.content.ContentValues().apply { put("event_id", eventId); put("processed_at", System.currentTimeMillis()) }, SQLiteDatabase.CONFLICT_REPLACE)
        writableDatabase.delete("processed", "processed_at < ?", arrayOf((System.currentTimeMillis() - 86_400_000L).toString()))
    }
    @Synchronized fun fail(eventId: String) {
        var retryCount = 0
        readableDatabase.query("pending", arrayOf("retry_count"), "event_id = ?", arrayOf(eventId), null, null, null).use { c -> if (c.moveToFirst()) retryCount = c.getInt(0) }
        val delay = when (retryCount) { 0 -> 5_000L; 1 -> 15_000L; 2 -> 30_000L; 3 -> 60_000L; else -> 120_000L }
        writableDatabase.execSQL("UPDATE pending SET retry_count = retry_count + 1, next_retry_at = ? WHERE event_id = ?", arrayOf(System.currentTimeMillis() + delay, eventId))
        writableDatabase.delete("pending", "retry_count > 8 OR timestamp < ?", arrayOf((System.currentTimeMillis() - 900_000L).toString()))
    }
}
