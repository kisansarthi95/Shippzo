package expo.modules.couriersynclistener

import android.app.Notification
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors

/**
 * NotificationListenerService that filters incoming SMS-app notifications
 * for India Post DLT senders (or whatever sender pattern is configured)
 * and POSTs the raw body to the backend ingest endpoint.
 *
 * Reasons for choosing a NotificationListener over READ_SMS:
 *   • READ_SMS is a Play-Store-restricted permission; using it requires
 *     a manual policy declaration and routinely gets apps removed.
 *   • NotificationListener works for ANY messaging app that posts SMS
 *     notifications — Google Messages, Samsung Messages, MIUI Messaging
 *     and the OEM Messenger forks all post identical Notification objects.
 *   • The user explicitly opts in via System Settings → Notification access,
 *     which is the standard Android UX for this kind of background reader.
 *
 * The service is intentionally LIGHT — we keep zero state in memory, read
 * the latest filter / backend / token from SharedPreferences on every
 * notification (so changes from the React layer take effect instantly).
 *
 * Phase F4.8 — Verbose Log.d instrumentation at every gate so operators
 * can `adb logcat -s CourierSync` and see exactly why a given SMS was
 * or wasn't ingested. Every log line starts with a per-notification
 * short id (last 6 chars of the SBN key) so logs from concurrent
 * notifications are easy to correlate.
 */
class CourierNotificationListenerService : NotificationListenerService() {

  private val executor = Executors.newSingleThreadExecutor()
  private val httpClient: OkHttpClient by lazy {
    OkHttpClient.Builder()
      .connectTimeout(10, TimeUnit.SECONDS)
      .writeTimeout(15,  TimeUnit.SECONDS)
      .readTimeout(15,   TimeUnit.SECONDS)
      .build()
  }

  override fun onNotificationPosted(sbn: StatusBarNotification?) {
    if (sbn == null) {
      Log.d(TAG, "onNotificationPosted: sbn=null — skip")
      return
    }
    val sid = try {
      sbn.key.takeLast(6)
    } catch (_: Exception) {
      "??????"
    }
    val extras = sbn.notification?.extras
    if (extras == null) {
      Log.d(TAG, "[$sid] extras=null pkg=${sbn.packageName.orEmpty()} — skip")
      return
    }

    val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
    val text  = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
      ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString().orEmpty()
    val pkg   = sbn.packageName.orEmpty()

    // ─── LOG raw notification fields — ALWAYS logged so we can see
    // even the ones we drop. Truncate text to avoid multi-MB logs
    // from apps that stuff huge bodies into notifications.
    val textPreview = if (text.length > 200) text.take(200) + "…[+${text.length - 200}]" else text
    Log.d(TAG, "[$sid] recv pkg=$pkg title=\"$title\" text=\"$textPreview\"")

    if (text.isBlank() && title.isBlank()) {
      Log.d(TAG, "[$sid] gate=payload_empty reason=title+text both blank — skip")
      return
    }

    val prefs = applicationContext.getSharedPreferences(
      CourierSyncListenerModule.PREFS,
      Context.MODE_PRIVATE,
    )
    val enabled       = prefs.getBoolean(CourierSyncListenerModule.KEY_ENABLED, false)
    Log.d(TAG, "[$sid] gate=enabled value=$enabled")
    if (!enabled) return

    val senderFilter  = prefs.getString(
      CourierSyncListenerModule.KEY_SENDER_PATTERN, "IndiaPost",
    ).orEmpty()
    val backendUrl    = prefs.getString(CourierSyncListenerModule.KEY_BACKEND_URL, "").orEmpty()
    val authToken     = prefs.getString(CourierSyncListenerModule.KEY_AUTH_TOKEN, "").orEmpty()
    val deviceId      = prefs.getString(CourierSyncListenerModule.KEY_DEVICE_ID, "").orEmpty()

    val configOk = backendUrl.isNotEmpty() && authToken.isNotEmpty()
    Log.d(
      TAG,
      "[$sid] gate=config_present backendUrl=${if (backendUrl.isEmpty()) \"MISSING\" else \"set(${backendUrl.length}c)\"} " +
        "authToken=${if (authToken.isEmpty()) \"MISSING\" else \"set(${authToken.length}c)\"} " +
        "deviceId=${if (deviceId.isEmpty()) \"MISSING\" else deviceId} " +
        "senderFilter=\"$senderFilter\"",
    )
    if (!configOk) {
      Log.d(TAG, "[$sid] gate=config_present result=FAIL — skip")
      return
    }

    if (senderFilter.isNotEmpty()) {
      val haystack = "$title $text".uppercase()
      val match = haystack.contains(senderFilter.uppercase())
      Log.d(TAG, "[$sid] gate=sender_filter needle=\"$senderFilter\" match=$match")
      if (!match) return
    } else {
      Log.d(TAG, "[$sid] gate=sender_filter needle=EMPTY — pass-through")
    }

    // Only ingest from likely SMS apps to avoid forwarding random
    // app notifications. Wide allow-list — errs on the side of false-
    // positives because the backend parser is the real gate.
    val isSmsApp = pkg.contains("messag", ignoreCase = true) ||
                   pkg.contains("sms",    ignoreCase = true) ||
                   pkg == "com.android.mms" ||
                   pkg == "com.samsung.android.messaging"
    Log.d(TAG, "[$sid] gate=sms_app_match pkg=$pkg match=$isSmsApp")
    if (!isSmsApp) return

    val postedAt = try { sbn.postTime } catch (_: Exception) { System.currentTimeMillis() }

    executor.execute {
      val url = "${backendUrl.trimEnd('/')}/api/courier-sync/ingest"
      Log.d(TAG, "[$sid] http=POST_pre url=$url textLen=${text.length}")
      try {
        val payload = JSONObject().apply {
          put("sender",    title)            // SMS app puts the sender in the title
          put("title",     title)
          put("text",      text)
          put("package",   pkg)
          put("posted_at", java.time.Instant.ofEpochMilli(postedAt).toString())
          put("device_id", deviceId)
        }
        val req = Request.Builder()
          .url(url)
          .addHeader("Authorization", "Bearer $authToken")
          .addHeader("Content-Type",  "application/json")
          .post(payload.toString().toRequestBody("application/json".toMediaType()))
          .build()
        val started = System.currentTimeMillis()
        httpClient.newCall(req).execute().use { resp ->
          val took = System.currentTimeMillis() - started
          val bodySnippet = try {
            resp.peekBody(400).string()
          } catch (_: Exception) { "<unreadable>" }
          Log.d(
            TAG,
            "[$sid] http=POST_response status=${resp.code} tookMs=$took bodyPreview=$bodySnippet",
          )
          if (!resp.isSuccessful) {
            Log.w(TAG, "[$sid] ingest non-2xx: ${resp.code}")
          }
        }
      } catch (e: Exception) {
        Log.w(TAG, "[$sid] http=POST_exception msg=${e.message} class=${e.javaClass.simpleName}")
      }
    }
  }

  override fun onNotificationRemoved(sbn: StatusBarNotification?) {
    // No-op — we only care about posting events.
  }

  companion object {
    // Phase F4.8 — Tag intentionally matches the "CourierSync" tag
    // used in the user's `adb logcat -s CourierSync` command so both
    // the React-native side (`Api.courierSync*` axios logs — client-
    // side console) and the native side surface under a single tag.
    private const val TAG = "CourierSync"
  }
}
