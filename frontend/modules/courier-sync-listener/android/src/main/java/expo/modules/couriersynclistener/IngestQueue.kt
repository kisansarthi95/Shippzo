package expo.modules.couriersynclistener

import android.content.Context
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Phase F8.0 — Offline-tolerant delivery pipeline for courier SMS payloads.
 *
 * Why: operators frequently have mobile data switched OFF when an SMS
 * arrives. The NotificationListener still fires, but the HTTP POST to
 * /api/courier-sync/ingest fails. Previously the SMS was silently lost.
 *
 * Now every payload that cannot be delivered is persisted into a
 * SharedPreferences-backed FIFO queue and re-sent when:
 *   • the device regains connectivity (ConnectivityManager callback in
 *     CourierNotificationListenerService),
 *   • the next SMS arrives and its POST succeeds (piggyback drain),
 *   • the React layer calls `flushPendingQueue()` (app open / foreground).
 *
 * Outcome semantics for one POST:
 *   OK    → 2xx. Also emits "onIngestResult" to JS so open screens refresh.
 *   RETRY → offline / timeout / 401 (stale token) / 408 / 429 / 5xx.
 *   DROP  → other 4xx (permanently rejected — retrying would loop forever).
 */
object IngestQueue {
  private const val TAG = "CourierSync"
  private const val KEY_QUEUE = "pending_ingest_queue"
  private const val MAX_QUEUE = 100
  private const val MAX_ATTEMPTS = 25

  const val OUTCOME_OK = "ok"
  const val OUTCOME_RETRY = "retry"
  const val OUTCOME_DROP = "drop"

  private val client: OkHttpClient by lazy {
    OkHttpClient.Builder()
      .connectTimeout(10, TimeUnit.SECONDS)
      .writeTimeout(15, TimeUnit.SECONDS)
      .readTimeout(15, TimeUnit.SECONDS)
      .build()
  }

  @Synchronized
  private fun readQueue(ctx: Context): JSONArray {
    val prefs = ctx.getSharedPreferences(CourierSyncListenerModule.PREFS, Context.MODE_PRIVATE)
    val raw = prefs.getString(KEY_QUEUE, "") ?: ""
    return try {
      if (raw.isEmpty()) JSONArray() else JSONArray(raw)
    } catch (_: Exception) {
      JSONArray()
    }
  }

  @Synchronized
  private fun writeQueue(ctx: Context, arr: JSONArray) {
    ctx.getSharedPreferences(CourierSyncListenerModule.PREFS, Context.MODE_PRIVATE)
      .edit()
      .putString(KEY_QUEUE, arr.toString())
      .apply()
  }

  /** Append one payload; oldest entries are evicted past MAX_QUEUE. */
  @Synchronized
  fun enqueue(ctx: Context, payload: JSONObject) {
    val arr = readQueue(ctx)
    val next = JSONArray()
    val overflow = (arr.length() + 1) - MAX_QUEUE
    var skipped = 0
    for (i in 0 until arr.length()) {
      val item = arr.optJSONObject(i) ?: continue
      if (skipped < overflow) {
        skipped++
        continue
      }
      next.put(item)
    }
    next.put(payload)
    writeQueue(ctx, next)
    Log.d(TAG, "queue=enqueued size=" + next.length())
  }

  @Synchronized
  fun count(ctx: Context): Int = readQueue(ctx).length()

  @Synchronized
  private fun popFirst(ctx: Context): JSONObject? {
    val arr = readQueue(ctx)
    if (arr.length() == 0) return null
    val first = arr.optJSONObject(0)
    val rest = JSONArray()
    for (i in 1 until arr.length()) {
      val item = arr.optJSONObject(i) ?: continue
      rest.put(item)
    }
    writeQueue(ctx, rest)
    return first
  }

  @Synchronized
  private fun pushFront(ctx: Context, payload: JSONObject) {
    val arr = readQueue(ctx)
    val next = JSONArray()
    next.put(payload)
    for (i in 0 until arr.length()) {
      val item = arr.optJSONObject(i) ?: continue
      next.put(item)
    }
    writeQueue(ctx, next)
  }

  /**
   * POST one payload to {backendUrl}/api/courier-sync/ingest.
   * Config (URL + JWT) is re-read from SharedPreferences on EVERY call
   * so a token refreshed by the React layer applies to queued retries.
   */
  fun post(ctx: Context, payload: JSONObject): String {
    val prefs = ctx.getSharedPreferences(CourierSyncListenerModule.PREFS, Context.MODE_PRIVATE)
    val backendUrl = prefs.getString(CourierSyncListenerModule.KEY_BACKEND_URL, "") ?: ""
    val authToken = prefs.getString(CourierSyncListenerModule.KEY_AUTH_TOKEN, "") ?: ""
    if (backendUrl.isEmpty() || authToken.isEmpty()) {
      Log.d(TAG, "post: config missing (url or token) — retry later")
      return OUTCOME_RETRY
    }
    val url = backendUrl.trimEnd('/') + "/api/courier-sync/ingest"
    return try {
      val req = Request.Builder()
        .url(url)
        .addHeader("Authorization", "Bearer " + authToken)
        .addHeader("Content-Type", "application/json")
        .post(payload.toString().toRequestBody("application/json".toMediaType()))
        .build()
      val started = System.currentTimeMillis()
      client.newCall(req).execute().use { resp ->
        val took = System.currentTimeMillis() - started
        val body = try {
          resp.peekBody(600).string()
        } catch (_: Exception) {
          ""
        }
        Log.d(TAG, "post: status=" + resp.code + " tookMs=" + took + " bodyPreview=" + body.take(300))
        when {
          resp.code in 200..299 -> {
            CourierSyncListenerModule.emitIngestResult(
              mapOf("status" to resp.code, "body" to body),
            )
            OUTCOME_OK
          }
          // 401 = stale token (React layer refreshes it on next app
          // open); 408/429 = transient. Keep the payload for retry.
          resp.code == 401 || resp.code == 408 || resp.code == 429 -> OUTCOME_RETRY
          resp.code in 400..499 -> OUTCOME_DROP
          else -> OUTCOME_RETRY
        }
      }
    } catch (e: Exception) {
      Log.w(TAG, "post: exception=" + (e.message ?: "") + " class=" + e.javaClass.simpleName)
      OUTCOME_RETRY
    }
  }

  /**
   * Drain the queue front-to-back, preserving arrival order. Stops at
   * the first RETRY outcome (network still down / token still stale).
   */
  fun flush(ctx: Context) {
    val pending = count(ctx)
    if (pending == 0) return
    Log.d(TAG, "flush: start pending=" + pending)
    var guard = 0
    while (guard < MAX_QUEUE) {
      guard++
      val entry = popFirst(ctx) ?: break
      when (post(ctx, entry)) {
        OUTCOME_OK -> Log.d(TAG, "flush: delivered one, remaining=" + count(ctx))
        OUTCOME_DROP -> Log.w(TAG, "flush: dropped permanently-rejected entry")
        else -> {
          val attempts = entry.optInt("attempts", 0) + 1
          if (attempts < MAX_ATTEMPTS) {
            entry.put("attempts", attempts)
            pushFront(ctx, entry)
          } else {
            Log.w(TAG, "flush: entry exceeded max attempts — dropped")
          }
          Log.d(TAG, "flush: paused (will retry later), remaining=" + count(ctx))
          return
        }
      }
    }
  }
}
