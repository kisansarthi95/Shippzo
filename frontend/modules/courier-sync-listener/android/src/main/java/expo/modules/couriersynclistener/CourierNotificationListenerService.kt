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
    if (sbn == null) return
    val extras = sbn.notification?.extras ?: return

    val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
    val text  = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
      ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString().orEmpty()
    val pkg   = sbn.packageName.orEmpty()

    if (text.isBlank() && title.isBlank()) return

    val prefs = applicationContext.getSharedPreferences(
      CourierSyncListenerModule.PREFS,
      Context.MODE_PRIVATE,
    )
    val enabled       = prefs.getBoolean(CourierSyncListenerModule.KEY_ENABLED, false)
    if (!enabled) return

    val senderFilter  = prefs.getString(
      CourierSyncListenerModule.KEY_SENDER_PATTERN, "INPOST",
    ).orEmpty()
    val backendUrl    = prefs.getString(CourierSyncListenerModule.KEY_BACKEND_URL, "").orEmpty()
    val authToken     = prefs.getString(CourierSyncListenerModule.KEY_AUTH_TOKEN, "").orEmpty()
    val deviceId      = prefs.getString(CourierSyncListenerModule.KEY_DEVICE_ID, "").orEmpty()

    if (backendUrl.isEmpty() || authToken.isEmpty()) return
    if (senderFilter.isNotEmpty()) {
      val haystack = "$title $text".uppercase()
      if (!haystack.contains(senderFilter.uppercase())) return
    }

    // Only ingest from likely SMS apps to avoid forwarding random
    // app notifications. Wide allow-list — errs on the side of false-
    // positives because the backend parser is the real gate.
    val isSmsApp = pkg.contains("messag", ignoreCase = true) ||
                   pkg.contains("sms",    ignoreCase = true) ||
                   pkg == "com.android.mms" ||
                   pkg == "com.samsung.android.messaging"
    if (!isSmsApp) return

    val postedAt = try { sbn.postTime } catch (_: Exception) { System.currentTimeMillis() }

    executor.execute {
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
          .url("${backendUrl.trimEnd('/')}/api/courier-sync/ingest")
          .addHeader("Authorization", "Bearer $authToken")
          .addHeader("Content-Type",  "application/json")
          .post(payload.toString().toRequestBody("application/json".toMediaType()))
          .build()
        httpClient.newCall(req).execute().use { resp ->
          if (!resp.isSuccessful) {
            Log.w(TAG, "ingest non-2xx: ${resp.code}")
          }
        }
      } catch (e: Exception) {
        Log.w(TAG, "ingest failed: ${e.message}")
      }
    }
  }

  override fun onNotificationRemoved(sbn: StatusBarNotification?) {
    // No-op — we only care about posting events.
  }

  companion object {
    private const val TAG = "CourierSyncListener"
  }
}
